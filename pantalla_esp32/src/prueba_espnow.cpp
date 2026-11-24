/*
 * Prueba de ESP-NOW entre buses: balizas y tabla de vecinos (M8-01) y transferencia de
 * mensajes de 2 KB (M8-02).
 *
 * Cargar en DOS placas:  pio run -e prueba_espnow -t upload
 * Ver los mensajes:      pio device monitor
 *
 * No usa WiFi, pantalla, microSD ni PN532: solo la ESP32. El LED azul de la placa (GPIO2)
 * se enciende mientras haya al menos un vecino.
 *
 * M8-01: cada placa escribe "vecino ... detectado" al ver a la otra (menos de 3 s) y
 * "perdido" a los 10 s sin balizas.
 *
 * M8-02: cuando se ven, la placa de número de validador MÁS BAJO envía 20 mensajes de
 * 2048 B (el máximo de un paquete C1) a la otra, con un contenido que depende de la
 * ronda y del número de mensaje. La otra comprueba cada byte. Se repite en 10 rondas, y
 * entre una y otra se reinicia el encuentro (el límite es de 20 mensajes por encuentro).
 *   Emisor:   "[emisor] RONDA n: 20 aceptados, 20 entregados, 0 abortados, ..."
 *   Receptor: "[receptor] RONDA n: recibidos 20/20, íntegros 20, corruptos 0, ..."
 * Al terminar cada ronda, el receptor le devuelve su resultado al emisor por la misma radio
 * (un mensaje corto), y el emisor lo escribe junto al suyo. Así, con las placas separadas
 * (p. ej. a 10 m), basta con una conectada al ordenador: la del emisor, la de número más
 * bajo. La otra puede ir con un cargador USB o una batería externa.
 *   Emisor:   "[emisor] RONDA n: el receptor confirma: recibidos 20/20, íntegros 20, ..."
 *
 * Esperado: 10 rondas con los 20 mensajes íntegros cada una y 0 abortados. Al final el
 * emisor escribe "PRUEBA M8-02 COMPLETA". Los reintentos de fragmentos (en la línea del
 * emisor) indican cuánto cuesta la distancia; los abortados, que ya no alcanza.
 *
 * El número de validador de prueba sale de la MAC, así que cada placa tiene el suyo.
 */

#include <Arduino.h>
#include <VecinosEspNow.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

constexpr uint8_t PIN_LED = 2;
constexpr size_t LARGO_MENSAJE = 2048;
constexpr uint32_t MENSAJES_POR_RONDA = 20;
constexpr uint32_t RONDAS = 10;
constexpr uint32_t ESPERA_VECINO_MS = 2000;   // el vecino debe llevar este tiempo en la tabla
constexpr uint32_t PAUSA_RONDA_MS = 3000;     // para que el receptor escriba su resumen
constexpr uint32_t REINICIO_ENCUENTRO_MS = 800;
constexpr uint32_t SILENCIO_RECEPTOR_MS = 1500;  // sin mensajes tanto tiempo: la ronda terminó
constexpr uint32_t ESPERA_RESULTADO_MS = 5000;   // el emisor espera la respuesta del receptor
constexpr uint32_t MAGIA_RESULTADO = 0x544C5352;  // "RSLT"

// Lo que el receptor le cuenta al emisor al terminar una ronda (28 B). El RSSI es el de las
// tramas del EMISOR medidas en el receptor durante la ronda (0 muestras = sin dato).
struct Resultado {
  uint32_t magia, ronda, recibidos, integros, corruptos;
  int32_t rssiMedio;
  uint32_t rssiN;
};

void registro(const String &texto) { Serial.printf("[%8lu ms] %s\n", (unsigned long)millis(), texto.c_str()); }

VecinosEspNow vecinos(registro);
sp::Baliza propia;

// --- Modo de radio (experimento de alcance) ------------------------------------------------
// El PC puede mandar por serie, en los 3 s siguientes al arranque, una línea "MODO <lr> <reint>":
//   lr = 0 tasa por defecto (1 Mbit/s), 1 = largo alcance 250 kbit/s, 2 = largo alcance 500 kbit/s,
//        3 = adaptativa: datos a 1 Mbit/s, y los reintentos y las balizas en largo alcance 250 kbit/s
//   reint = reintentos por fragmento antes de abortar el mensaje (por defecto 3)
// El emisor anuncia su "lr" en los bits 2-4 de los flags de su baliza y el receptor lo sigue, así
// una campaña entera corre sin cargar firmware entre variantes. La radio siempre acepta recibir
// en LR (máscara de protocolos completa); solo cambia la tasa con la que se TRANSMITE.
constexpr uint8_t BALIZA_FLAG_LR_DESPL = 2, BALIZA_FLAG_LR_MASCARA = 0x1C;
struct Modo {
  uint8_t lr = 0;
  uint8_t reintentos = 3;
};
Modo modo;
uint8_t macPropia[6];

const char *nombreTasa(uint8_t lr) {
  switch (lr) {
    case 1: return "LR 250 kbit/s";
    case 2: return "LR 500 kbit/s";
    case 3: return "adaptativa (datos 1 Mbit/s, reintentos y balizas LR 250)";
    default: return "1 Mbit/s (defecto)";
  }
}

void aplicarTasa(uint8_t lr) {
  using T = VecinosEspNow::Tasa;
  switch (lr) {
    case 1: vecinos.fijarTasas(T::LR250, T::LR250, T::LR250); break;
    case 2: vecinos.fijarTasas(T::LR500, T::LR500, T::LR500); break;
    case 3: vecinos.fijarTasas(T::ESTANDAR, T::LR250, T::LR250); break;
    default: vecinos.fijarTasas(T::ESTANDAR, T::ESTANDAR, T::ESTANDAR); break;
  }
  registro(String("Radio: tasa de ESP-NOW ") + nombreTasa(lr));
  propia.flags = (propia.flags & ~BALIZA_FLAG_LR_MASCARA) | (lr << BALIZA_FLAG_LR_DESPL);
  vecinos.publicar(propia);
}

void leerModoInicial() {
  String linea;
  const uint32_t t0 = millis();
  while (millis() - t0 < 3000) {
    while (Serial.available()) {
      const char c = Serial.read();
      if (c == '\n') {
        int lr = 0, reint = 3;
        if (sscanf(linea.c_str(), "MODO %d %d", &lr, &reint) == 2 && lr >= 0 && lr <= 3 && reint >= 0 && reint <= 30) {
          modo.lr = lr;
          modo.reintentos = reint;
          registro("Modo pedido por el PC: lr=" + String(lr) + ", reintentos=" + String(reint));
        } else {
          registro("Línea de modo no válida: " + linea);
        }
        return;
      }
      if (c != '\r') linea += c;
    }
    delay(5);
  }
}

// --- RSSI de las tramas ESP-NOW (modo promiscuo, solo lectura) ----------------------------
struct AcumRssi {
  int32_t suma = 0;
  int16_t minimo = 0, maximo = -127;
  uint16_t n = 0;
  void anotar(int r) {
    suma += r;
    if (n == 0 || r < minimo) minimo = r;
    if (n == 0 || r > maximo) maximo = r;
    n++;
  }
  int32_t medio() const { return n ? suma / n : 0; }
};
// Velocidad real de las tramas recibidas: [0] balizas (cortas), [1] fragmentos de datos (largos).
// La clave es rate | (sig_mode << 5), tal como la informa la radio.
uint16_t histoTasas[2][128] = {};
AcumRssi rssiVentana, rssiRonda;  // la de 10 s (línea de resumen) y la de la ronda en curso
portMUX_TYPE muxRssi = portMUX_INITIALIZER_UNLOCKED;

// Corre en la tarea del WiFi. Una trama ESP-NOW es una acción de gestión (0xD0) con la categoría 127
// y el OUI de Espressif (18:FE:34) tras los 24 B de la cabecera 802.11.
void alPaquete(void *buf, wifi_promiscuous_pkt_type_t tipo) {
  if (tipo != WIFI_PKT_MGMT) return;
  const auto *p = static_cast<const wifi_promiscuous_pkt_t *>(buf);
  const uint8_t *d = p->payload;
  if (p->rx_ctrl.sig_len < 28 || d[0] != 0xD0 || d[24] != 127 || d[25] != 0x18 || d[26] != 0xFE || d[27] != 0x34) return;
  if (memcmp(d + 10, macPropia, 6) == 0) return;
  portENTER_CRITICAL(&muxRssi);
  rssiVentana.anotar(p->rx_ctrl.rssi);
  rssiRonda.anotar(p->rx_ctrl.rssi);
  histoTasas[p->rx_ctrl.sig_len < 150 ? 0 : 1][(p->rx_ctrl.rate & 31) | ((p->rx_ctrl.sig_mode & 3) << 5)]++;
  portEXIT_CRITICAL(&muxRssi);
}

void configurarRadio() {
  int8_t potencia = 0;
  esp_wifi_get_max_tx_power(&potencia);
  registro("Radio: potencia máxima de transmisión " + String(potencia * 0.25f, 1) + " dBm, reintentos por fragmento " +
           String(modo.reintentos));
  vecinos.fijarReintentos(modo.reintentos);
  aplicarTasa(modo.lr);
  wifi_promiscuous_filter_t filtro = {WIFI_PROMIS_FILTER_MASK_MGMT};
  esp_wifi_set_promiscuous_filter(&filtro);
  esp_wifi_set_promiscuous_rx_cb(&alPaquete);
  esp_wifi_set_promiscuous(true);
}

// El receptor transmite con la misma tasa que anuncia el emisor
void seguirTasaDelEmisor() {
  if (vecinos.cantidad() == 0) return;
  const uint8_t lrVecino = (vecinos.vecino(0).baliza.flags & BALIZA_FLAG_LR_MASCARA) >> BALIZA_FLAG_LR_DESPL;
  if (lrVecino != modo.lr && lrVecino <= 3) {
    modo.lr = lrVecino;
    aplicarTasa(modo.lr);
  }
}

// --- Contenido de prueba -----------------------------------------------------------------

void armarMensaje(uint8_t *m, uint32_t ronda, uint32_t indice) {
  memcpy(m, &ronda, 4);
  memcpy(m + 4, &indice, 4);
  uint32_t x = ronda * 2654435761u + indice * 40503u + 1;
  for (size_t j = 8; j < LARGO_MENSAJE; j++) {
    x = x * 1664525u + 1013904223u;
    m[j] = x >> 24;
  }
}

static uint8_t bufferMensaje[LARGO_MENSAJE], bufferEsperado[LARGO_MENSAJE];

// --- Emisor ------------------------------------------------------------------------------

enum class Fase : uint8_t { ESPERA_VECINO, RECEPTOR, ENVIANDO, PAUSA, REINICIO, FIN };
Fase fase = Fase::ESPERA_VECINO;
uint32_t vecinoDesde = 0, faseDesde = 0;
uint32_t ronda = 0, siguiente = 0, inicioRondaMs = 0;
uint32_t entregadosAntes = 0, abortadosAntes = 0, reintentosAntes = 0;
uint32_t totalEntregados = 0, totalAbortados = 0, totalRondasOk = 0;
uint8_t macVecino[6];
bool resultadoLlego = false;
uint32_t totalRecibidosReceptor = 0, totalIntegrosReceptor = 0;

void iniciarRonda() {
  ronda++;
  siguiente = 0;
  inicioRondaMs = millis();
  entregadosAntes = vecinos.mensajesEnviados();
  abortadosAntes = vecinos.mensajesAbortados();
  reintentosAntes = vecinos.fragmentosReintentados();
  memcpy(macVecino, vecinos.vecino(0).mac, 6);
  resultadoLlego = false;
  registro("[emisor] RONDA " + String(ronda) + " de " + String(RONDAS) + ": envío " + String(MENSAJES_POR_RONDA) +
           " mensajes de " + String(LARGO_MENSAJE) + " B al validador " + String(vecinos.vecino(0).baliza.validador));
  fase = Fase::ENVIANDO;
}

void cerrarRonda(bool error) {
  const uint32_t entregados = vecinos.mensajesEnviados() - entregadosAntes;
  const uint32_t abortados = vecinos.mensajesAbortados() - abortadosAntes;
  const uint32_t reintentos = vecinos.fragmentosReintentados() - reintentosAntes;
  const uint32_t ms = millis() - inicioRondaMs;
  totalEntregados += entregados;
  totalAbortados += abortados;
  if (entregados == MENSAJES_POR_RONDA && abortados == 0 && !error) totalRondasOk++;
  registro("[emisor] RONDA " + String(ronda) + ": " + String(siguiente) + " aceptados, " + String(entregados) +
           " entregados, " + String(abortados) + " abortados, " + String(reintentos) + " fragmentos reintentados, " +
           String(ms) + " ms" + (error ? "  (RONDA INTERRUMPIDA)" : ""));
  fase = Fase::PAUSA;
  faseDesde = millis();
}

void atenderEmisor() {
  switch (fase) {
    case Fase::ENVIANDO: {
      if (siguiente < MENSAJES_POR_RONDA) {
        armarMensaje(bufferMensaje, ronda, siguiente);
        const auto r = vecinos.enviar(macVecino, bufferMensaje, LARGO_MENSAJE);
        if (r == VecinosEspNow::Envio::ACEPTADO) {
          siguiente++;
        } else if (r != VecinosEspNow::Envio::OCUPADO) {
          registro("[emisor] ERROR: enviar() devolvió " + String((int)r) + " en el mensaje " + String(siguiente));
          while (vecinos.enviando()) {  // deja terminar el que va en curso
            vecinos.atender();
            delay(2);
          }
          cerrarRonda(true);
        }
      } else if (!vecinos.enviando()) {
        cerrarRonda(false);
      }
      break;
    }
    case Fase::PAUSA:
      // Se espera lo que cuenta el receptor (o a que pase el plazo) antes de reiniciar el encuentro
      if (!resultadoLlego && millis() - faseDesde < ESPERA_RESULTADO_MS) break;
      if (!resultadoLlego) registro("[emisor] RONDA " + String(ronda) + ": SIN respuesta del receptor en " +
                                    String(ESPERA_RESULTADO_MS / 1000) + " s");
      if (ronda >= RONDAS) {
        registro("PRUEBA M8-02 COMPLETA: " + String(RONDAS) + " rondas, " + String(RONDAS * MENSAJES_POR_RONDA) +
                 " mensajes: el emisor entregó " + String(totalEntregados) + " y abortó " + String(totalAbortados) +
                 "; el receptor confirma " + String(totalIntegrosReceptor) + " íntegros de " +
                 String(totalRecibidosReceptor) + " recibidos; rondas perfectas " + String(totalRondasOk) + "/" +
                 String(RONDAS));
        fase = Fase::FIN;
        break;
      }
      vecinos.pausar(true);  // vacía la tabla: el próximo encuentro empieza de cero
      fase = Fase::REINICIO;
      faseDesde = millis();
      break;
    case Fase::REINICIO:
      if (millis() - faseDesde < REINICIO_ENCUENTRO_MS) break;
      vecinos.pausar(false);
      vecinoDesde = 0;
      fase = Fase::ESPERA_VECINO;
      break;
    default:
      break;
  }
}

// --- Receptor ----------------------------------------------------------------------------

uint32_t rondaRecibida = 0, recibidos = 0, integros = 0, corruptos = 0, duplicados = 0, vistos = 0;
uint32_t ultimoMensajeMs = 0;
uint32_t acumRecibidos = 0, acumIntegros = 0;
uint8_t macEmisor[6] = {};
bool resultadoPendiente = false;
Resultado resultadoAEnviar = {};
uint32_t resultadoDesdeMs = 0;
bool reinicioPendiente = false, enReinicio = false;
uint32_t reinicioDesdeMs = 0;

void resumenReceptor() {
  String faltan;
  for (uint32_t i = 0; i < MENSAJES_POR_RONDA; i++)
    if (!(vistos & (1u << i))) faltan += (faltan.length() ? "," : "") + String(i);
  acumRecibidos += recibidos;
  acumIntegros += integros;
  registro("[receptor] RONDA " + String(rondaRecibida) + ": recibidos " + String(recibidos) + "/" + String(MENSAJES_POR_RONDA) +
           ", íntegros " + String(integros) + ", corruptos " + String(corruptos) + ", duplicados " + String(duplicados) +
           (faltan.length() ? ", FALTAN " + faltan : "") + " | acumulado: " + String(acumIntegros) + " íntegros de " +
           String(acumRecibidos) + " recibidos");
  portENTER_CRITICAL(&muxRssi);
  const int32_t rssiMedio = rssiRonda.medio();
  const uint32_t rssiN = rssiRonda.n;
  rssiRonda = AcumRssi();
  portEXIT_CRITICAL(&muxRssi);
  resultadoAEnviar = {MAGIA_RESULTADO, rondaRecibida, recibidos, integros, corruptos, rssiMedio, rssiN};
  resultadoPendiente = true;  // se le cuenta al emisor en cuanto la radio lo permita
  resultadoDesdeMs = millis();
  recibidos = integros = corruptos = duplicados = vistos = 0;
}

void enviarResultadoPendiente() {
  if (!resultadoPendiente) return;
  if (millis() - resultadoDesdeMs > ESPERA_RESULTADO_MS) {  // ya no tiene sentido insistir
    resultadoPendiente = false;
    return;
  }
  const auto r = vecinos.enviar(macEmisor, reinterpret_cast<const uint8_t *>(&resultadoAEnviar), sizeof(resultadoAEnviar));
  if (r == VecinosEspNow::Envio::ACEPTADO) {
    resultadoPendiente = false;
    reinicioPendiente = true;
  }
}

// El límite de 20 mensajes por encuentro también cuenta en el receptor: cada ronda le cuesta una
// confirmación, así que sin reiniciar su encuentro (como hace el emisor entre rondas) se agota a
// las 2 pasadas y el emisor se queda sin respuesta. Se reinicia cuando la confirmación ya salió.
void reiniciarEncuentroReceptor() {
  if (enReinicio) {
    if (millis() - reinicioDesdeMs < REINICIO_ENCUENTRO_MS) return;
    vecinos.pausar(false);
    enReinicio = false;
  } else if (reinicioPendiente && !vecinos.enviando()) {
    vecinos.pausar(true);  // vacía la tabla: el próximo encuentro empieza de cero
    enReinicio = true;
    reinicioPendiente = false;
    reinicioDesdeMs = millis();
  }
}

void atenderReceptor() {
  reiniciarEncuentroReceptor();
  if (enReinicio) return;  // en pausa no se oye ni se envía nada
  enviarResultadoPendiente();
  uint8_t mac[6];
  const size_t n = vecinos.recibir(bufferMensaje, sizeof(bufferMensaje), mac);
  if (n == sizeof(Resultado)) {
    // La respuesta del receptor a una ronda (solo la recibe el emisor)
    Resultado r;
    memcpy(&r, bufferMensaje, sizeof(r));
    if (r.magia == MAGIA_RESULTADO) {
      resultadoLlego = true;
      totalRecibidosReceptor += r.recibidos;
      totalIntegrosReceptor += r.integros;
      registro("[emisor] RONDA " + String(r.ronda) + ": el receptor confirma: recibidos " + String(r.recibidos) + "/" +
               String(MENSAJES_POR_RONDA) + ", íntegros " + String(r.integros) + ", corruptos " + String(r.corruptos) +
               (r.rssiN ? ", RSSI en el receptor " + String(r.rssiMedio) + " dBm (" + String(r.rssiN) + " tramas)" : ""));
    }
    return;
  }
  if (n > 0) {
    memcpy(macEmisor, mac, 6);
    uint32_t r = 0, i = 0;
    if (n >= 8) {
      memcpy(&r, bufferMensaje, 4);
      memcpy(&i, bufferMensaje + 4, 4);
    }
    if (recibidos > 0 && r != rondaRecibida) resumenReceptor();  // empezó otra ronda sin quedar en silencio
    rondaRecibida = r;
    ultimoMensajeMs = millis();
    recibidos++;
    if (i < MENSAJES_POR_RONDA && (vistos & (1u << i))) duplicados++;
    if (i < MENSAJES_POR_RONDA) vistos |= 1u << i;
    armarMensaje(bufferEsperado, r, i);
    if (n == LARGO_MENSAJE && memcmp(bufferMensaje, bufferEsperado, LARGO_MENSAJE) == 0) {
      integros++;
    } else {
      corruptos++;
      registro("[receptor] mensaje " + String(i) + " de la ronda " + String(r) + " CORRUPTO (" + String(n) + " B)");
    }
    if (recibidos >= MENSAJES_POR_RONDA) resumenReceptor();  // ronda completa: se cuenta enseguida
  }
  if (recibidos > 0 && millis() - ultimoMensajeMs >= SILENCIO_RECEPTOR_MS) resumenReceptor();
}

// --- Principal ---------------------------------------------------------------------------

uint32_t ultimaTabla = 0;

void imprimirTabla() {
  Serial.printf("[%8lu ms] --- %u vecino(s) | balizas enviadas %lu, recibidas %lu | mensajes: enviados %lu, abortados %lu, "
                "recibidos %lu, descartados %lu\n",
                (unsigned long)millis(), (unsigned)vecinos.cantidad(), (unsigned long)vecinos.balizasEnviadas(),
                (unsigned long)vecinos.balizasRecibidas(), (unsigned long)vecinos.mensajesEnviados(),
                (unsigned long)vecinos.mensajesAbortados(), (unsigned long)vecinos.mensajesRecibidos(),
                (unsigned long)vecinos.mensajesDescartados());
  // RSSI de las tramas del vecino en esta ventana de 10 s (el emisor ve las del receptor y viceversa)
  portENTER_CRITICAL(&muxRssi);
  const AcumRssi v = rssiVentana;
  rssiVentana = AcumRssi();
  uint16_t h[2][128];
  memcpy(h, histoTasas, sizeof(h));
  memset(histoTasas, 0, sizeof(histoTasas));
  portEXIT_CRITICAL(&muxRssi);
  if (v.n)
    Serial.printf("[%8lu ms] RSSI del vecino: %d dBm de media (mín %d, máx %d) en %u tramas | tasa %s\n", (unsigned long)millis(),
                  (int)v.medio(), (int)v.minimo, (int)v.maximo, (unsigned)v.n, nombreTasa(modo.lr));
  for (int tipo = 0; tipo < 2; tipo++) {
    String linea;
    for (int k = 0; k < 128; k++)
      if (h[tipo][k]) linea += " r" + String(k & 31) + "/m" + String(k >> 5) + "=" + String(h[tipo][k]);
    if (linea.length()) Serial.printf("[%8lu ms] velocidad recibida (%s):%s\n", (unsigned long)millis(), tipo ? "datos" : "balizas", linea.c_str());
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LED, OUTPUT);
  WiFi.mode(WIFI_STA);
  uint8_t mac[6];
  WiFi.macAddress(mac);
  memcpy(macPropia, mac, 6);

  leerModoInicial();  // el PC puede pedir lr y reintentos en los primeros 3 s
  propia.validador = 1 + ((mac[4] << 8) | mac[5]) % 899;  // 1-899
  propia.flags = sp::BALIZA_ACEPTA_AJENOS;
  vecinos.publicar(propia);
  registro("Prueba ESP-NOW: validador de prueba " + String(propia.validador));
  // Las dos placas en el canal 1 fijo: en la prueba de alcance, al perder al vecino por distancia no debe irse a
  // buscarlo a otro canal (al volver a acercarse estaría en otro y se mediría mal)
  vecinos.buscarVecinos(false);
  vecinos.iniciar(1);
  configurarRadio();  // después de iniciar: la radio ya está en marcha
}

void loop() {
  vecinos.atender();
  digitalWrite(PIN_LED, vecinos.cantidad() > 0 ? HIGH : LOW);

  if (fase == Fase::ESPERA_VECINO || fase == Fase::RECEPTOR) {
    if (vecinos.cantidad() == 0) {
      vecinoDesde = 0;
    } else {
      if (vecinoDesde == 0) vecinoDesde = millis();
      // La de número más bajo envía; la otra recibe
      if (fase == Fase::ESPERA_VECINO && millis() - vecinoDesde >= ESPERA_VECINO_MS) {
        if (propia.validador < vecinos.vecino(0).baliza.validador) {
          iniciarRonda();
        } else {
          registro("[receptor] soy el validador " + String(propia.validador) + ": espero los mensajes del " +
                   String(vecinos.vecino(0).baliza.validador));
          fase = Fase::RECEPTOR;
        }
      }
    }
  }
  if (fase == Fase::RECEPTOR) seguirTasaDelEmisor();
  atenderEmisor();
  atenderReceptor();

  if (millis() - ultimaTabla >= 10000) {
    ultimaTabla = millis();
    imprimirTabla();
  }
  delay(2);
}
