#include "VecinosEspNow.h"

#include <WiFi.h>
#include <esp_now.h>
#include <esp_random.h>
#include <esp_wifi.h>

VecinosEspNow *VecinosEspNow::instancia_ = nullptr;

namespace {
const uint8_t DIFUSION[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
constexpr size_t COLA_BALIZAS = 8;  // sobra: caben 5 vecinos por segundo
}  // namespace

VecinosEspNow::~VecinosEspNow() {
  if (iniciado_) esp_now_deinit();
  if (cola_) vQueueDelete(cola_);
  if (instancia_ == this) instancia_ = nullptr;
}

void VecinosEspNow::anotar(const String &texto) const {
  if (log_) log_(texto);
}

String VecinosEspNow::macTexto(const uint8_t mac[6]) {
  char s[18];
  snprintf(s, sizeof(s), "%02X:%02X:%02X:%02X:%02X:%02X", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  return s;
}

void VecinosEspNow::fijarCanal() {
  // Con el WiFi sin asociar, el canal de la radio solo se cambia en modo promiscuo
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(canal_, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);
}

void VecinosEspNow::iniciar(uint8_t canalWifi) {
  if (iniciado_) return;
  canal_ = canalWifi ? canalWifi : 1;
  if (!cola_) cola_ = xQueueCreate(COLA_BALIZAS, sizeof(Recibida));
  if (!cola_) {
    anotar("ESP-NOW: ERROR, no hay memoria para la cola de balizas");
    return;
  }

  WiFi.mode(WIFI_STA);
  fijarCanal();
  if (esp_now_init() != ESP_OK) {
    anotar("ESP-NOW: ERROR al iniciar");
    return;
  }
  esp_now_peer_info_t par = {};
  memcpy(par.peer_addr, DIFUSION, 6);
  par.channel = 0;  // el canal actual de la radio
  par.ifidx = WIFI_IF_STA;
  par.encrypt = false;
  if (esp_now_add_peer(&par) != ESP_OK) {
    anotar("ESP-NOW: ERROR al registrar la difusión");
    esp_now_deinit();
    return;
  }
  instancia_ = this;
  esp_now_register_recv_cb(&VecinosEspNow::alRecibir);
  iniciado_ = true;
  programarSiguiente(millis(), true);
  uint8_t propiaMac[6];
  WiFi.macAddress(propiaMac);
  anotar("ESP-NOW: iniciado en el canal " + String(canal_) + ", MAC " + macTexto(propiaMac));
}

// Corre en la tarea del WiFi: solo filtra y encola; la tabla la toca atender()
void VecinosEspNow::alRecibir(const uint8_t *mac, const uint8_t *datos, int largo) {
  VecinosEspNow *yo = instancia_;
  if (!yo || !yo->cola_ || largo < (int)sp::TAM_BALIZA || datos[0] != 'E' || datos[1] != 'N' ||
      datos[2] != sp::BALIZA)
    return;  // otras clases de trama (fragmentos, pedidos) son de M8-02
  Recibida r;
  memcpy(r.mac, mac, 6);
  memcpy(r.datos, datos, sp::TAM_BALIZA);
  yo->recibidas_++;
  if (xQueueSend(yo->cola_, &r, 0) != pdTRUE) yo->perdidasCola_++;
}

void VecinosEspNow::programarSiguiente(uint32_t ahoraMs, bool primera) {
  if (primera) {
    // La primera sale enseguida (con un poco de azar): así dos placas que arrancan a la
    // vez se ven en menos de 3 s, sin esperar un periodo entero
    proximaMs_ = ahoraMs + esp_random() % (VARIACION_MS + 1);
    return;
  }
  const int32_t variacion = (int32_t)(esp_random() % (2 * VARIACION_MS + 1)) - (int32_t)VARIACION_MS;
  proximaMs_ = ahoraMs + (uint32_t)((int32_t)BALIZA_MS + variacion);
}

void VecinosEspNow::enviarBaliza() {
  uint8_t trama[sp::TAM_TRAMA_MAXIMO];
  const size_t largo = sp::armarBaliza(propia_, trama);
  if (esp_now_send(DIFUSION, trama, largo) == ESP_OK) {
    enviadas_++;
  } else {
    erroresEnvio_++;
  }
}

void VecinosEspNow::atender() {
  if (!iniciado_ || pausado_) return;
  const uint32_t ahora = millis();

  Recibida r;
  while (xQueueReceive(cola_, &r, 0) == pdTRUE) {
    sp::Baliza b;
    if (!sp::leerBaliza(r.datos, sp::TAM_BALIZA, b)) continue;
    if (b.validador == propia_.validador) continue;  // un bus con nuestro mismo número: no es un vecino
    const bool nuevo = tabla_.actualizar(r.mac, b, ahora) == TablaVecinos::Cambio::NUEVO;
    if (nuevo) {
      anotar("ESP-NOW: vecino " + String(b.validador) + " (" + macTexto(r.mac) + ") detectado; pendientes " +
             String(b.pendientes) + ", ajenos " + String(b.ajenos) + ", listas v" + String(b.versionListas) +
             ". Vecinos: " + String(tabla_.cantidad()));
    }
  }

  Vecino perdidos[TablaVecinos::MAXIMO];
  const size_t n = tabla_.caducar(ahora, perdidos);
  for (size_t i = 0; i < n; i++) {
    anotar("ESP-NOW: vecino " + String(perdidos[i].baliza.validador) + " (" + macTexto(perdidos[i].mac) +
           ") perdido: sin balizas desde hace " + String(TablaVecinos::CADUCIDAD_MS / 1000) + " s. Vecinos: " +
           String(tabla_.cantidad()));
  }

  if ((int32_t)(ahora - proximaMs_) >= 0) {
    enviarBaliza();
    programarSiguiente(ahora, false);
  }
}

void VecinosEspNow::pausar(bool si) {
  if (!iniciado_ || si == pausado_) return;
  pausado_ = si;
  if (si) {
    // Pausado no se oye a nadie: la tabla dejaría de ser verdad
    tabla_.vaciar();
    Recibida descartada;
    while (xQueueReceive(cola_, &descartada, 0) == pdTRUE) {
    }
    anotar("ESP-NOW: en pausa (el WiFi usa otro canal)");
  } else {
    programarSiguiente(millis(), true);
    anotar("ESP-NOW: reanudado");
  }
}
