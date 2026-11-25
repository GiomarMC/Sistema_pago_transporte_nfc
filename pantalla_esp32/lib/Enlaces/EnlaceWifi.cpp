#include "EnlaceWifi.h"

#include <ESPmDNS.h>
#include <WiFi.h>
#include <esp_wifi.h>

volatile uint8_t EnlaceWifi::motivos_[EnlaceWifi::MOTIVOS] = {};
volatile uint16_t EnlaceWifi::desconexiones_ = 0;
volatile uint16_t EnlaceWifi::fallos_ = 0;
volatile bool EnlaceWifi::escaneoListo_ = false;

namespace {
const char *textoMotivo(uint8_t m) {
  switch (m) {
    case WIFI_REASON_AUTH_EXPIRE: return "autenticación caducada";
    case WIFI_REASON_AUTH_LEAVE: return "el AP la echó";
    case WIFI_REASON_ASSOC_LEAVE: return "se desconectó la placa";
    case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT: return "sin respuesta en el handshake (¿contraseña?)";
    case WIFI_REASON_BEACON_TIMEOUT: return "sin balizas del AP";
    case WIFI_REASON_NO_AP_FOUND: return "no se encontró la red";
    case WIFI_REASON_AUTH_FAIL: return "autenticación fallida";
    case WIFI_REASON_ASSOC_FAIL: return "asociación fallida";
    case WIFI_REASON_HANDSHAKE_TIMEOUT: return "handshake sin terminar";
    case WIFI_REASON_CONNECTION_FAIL: return "conexión fallida";
    default: return "";
  }
}
}  // namespace

void EnlaceWifi::anotarFallo(const String &ssid) {
  // El primer motivo suele ser el 8 del WiFi.disconnect() que hace empezarIntento() antes de cada intento
  String lista;
  const uint16_t n = desconexiones_;
  for (uint8_t i = 0; i < n && i < MOTIVOS; i++) {
    const uint8_t m = motivos_[i];
    lista += (i ? ", " : "") + String(m) + (textoMotivo(m)[0] ? String(" ") + textoMotivo(m) : "");
  }
  log_("WiFi " + ssid + ": no conectó en " + String(ESPERA_RED_MS / 1000) + " s; " + String(n) + " desconexiones (" +
       lista + (n > MOTIVOS ? ", ..." : "") + "), estado " + String((int)WiFi.status()));
}

EnlaceWifi::EnlaceWifi(ListaRedes redes, Registro log, const char *raicesPem)
    : redes_(redes), log_(log), raices_(raicesPem) {
  contado_.usar(&plano_, &tx_, &rx_);
}

void EnlaceWifi::encender() {
  encendido_ = true;
  iniciado_ = false;
  intentando_ = false;
  hayEscaneo_ = false;  // el siguiente atender() empieza a buscar
}

void EnlaceWifi::apagar() {
  encendido_ = false;
  iniciado_ = false;
  conectado_ = false;
  intentando_ = false;
  escaneando_ = false;
  cerrarPropio();
  olvidarNombres();
  WiFi.scanDelete();
  WiFi.disconnect();
  WiFi.mode(WIFI_OFF);
  avisarCanal(0);
}

void EnlaceWifi::reiniciar() {
  iniciado_ = false;
  conectado_ = false;
  intentando_ = false;
  escaneando_ = false;
  hayEscaneo_ = false;
  vistas_ = "?";  // distinto de cualquier lista: lo primero que se vea se anota
  WiFi.scanDelete();
  cerrarPropio();
  olvidarNombres();
}

EstadoEnlace EnlaceWifi::estado() const {
  if (!encendido_) return EstadoEnlace::Apagado;
  if (WiFi.status() == WL_CONNECTED) return EstadoEnlace::Listo;
  if (redes_().empty()) return EstadoEnlace::Error;  // nada a lo que conectarse
  return EstadoEnlace::Conectando;
}

int8_t EnlaceWifi::senal() const {
  if (WiFi.status() != WL_CONNECTED) return 0;
  return static_cast<int8_t>(WiFi.RSSI() / 2);
}

void EnlaceWifi::registrarEventos() {
  if (eventosRegistrados_) return;
  eventosRegistrados_ = true;
  WiFi.onEvent(
      [](WiFiEvent_t, WiFiEventInfo_t info) {
        const uint8_t motivo = info.wifi_sta_disconnected.reason;
        if (desconexiones_ < MOTIVOS) motivos_[desconexiones_] = motivo;
        desconexiones_++;
        if (motivo != WIFI_REASON_ASSOC_LEAVE) fallos_++;
      },
      ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  // Arduino guarda los resultados al llegar el SCAN_DONE (antes de llamar a este aviso); aquí solo se marca el fin
  WiFi.onEvent([](WiFiEvent_t, WiFiEventInfo_t) { escaneoListo_ = true; }, ARDUINO_EVENT_WIFI_SCAN_DONE);
}

void EnlaceWifi::atender() {
  if (!encendido_) return;
  const auto &redes = redes_();
  if (redes.empty()) return;
  registrarEventos();

  if (WiFi.status() == WL_CONNECTED) {
    if (!conectado_) {
      conectado_ = true;
      intentando_ = false;
      log_("WiFi conectado a " + WiFi.SSID() + ": IP " + WiFi.localIP().toString() + ", señal " +
           String(WiFi.RSSI()) + " dBm, canal " + String(WiFi.channel()));
      avisarCanal(WiFi.channel());
    }
    return;
  }

  if (conectado_) {
    conectado_ = false;
    intentando_ = false;
    hayEscaneo_ = false;  // se busca enseguida
    vistas_ = "?";  // distinto de cualquier lista: lo primero que se vea se anota
    cerrarPropio();
    olvidarNombres();  // la IP del servidor local puede ser otra al volver
    log_("WiFi perdido; se sigue cobrando sin conexión");
    avisarCanal(0);
  }

  const uint32_t ahora = millis();
  if (intentando_) {
    if (fallos_ > 0 && reintentos_ < REINTENTOS_RED && ahora - inicioIntento_ > PAUSA_REINTENTO_MS) {
      reintentos_++;
      uint8_t motivo = 0;
      for (uint8_t i = 0; i < desconexiones_ && i < MOTIVOS; i++)
        if (motivos_[i] != WIFI_REASON_ASSOC_LEAVE) motivo = motivos_[i];
      log_("WiFi " + intentoSsid_ + ": no dejó conectar (motivo " + String(motivo) + "); reintento " +
           String(reintentos_) + " de " + String(REINTENTOS_RED));
      empezarIntento();
      return;
    }
    if (ahora - inicioIntento_ <= ESPERA_RED_MS) return;
    anotarFallo(intentoSsid_);
    fallidaSsid_ = intentoSsid_;
    fallidaMs_ = ahora;
    intentando_ = false;
    hayEscaneo_ = false;  // se vuelve a buscar enseguida
    WiFi.disconnect();
    avisarCanal(0);
    return;
  }

  // Sin conexión ni intento en curso: buscar las redes guardadas (en segundo plano: no bloquea el cobro)
  if (escaneando_) {
    const uint32_t duro = ahora - ultimoEscaneoMs_;
    if (!escaneoListo_ && duro < LIMITE_ESCANEO_MS) return;
    escaneando_ = false;
    if (escaneoListo_) {
      const int16_t n = WiFi.scanComplete();  // los resultados los guardó Arduino al llegar el SCAN_DONE
      escaneosFallidos_ = 0;
      elegirYConectar(redes, n > 0 ? n : 0);
    } else {
      esp_wifi_scan_stop();  // no terminó: se para en el driver
      falloEscaneo("no terminó en " + String(duro) + " ms");
    }
    WiFi.scanDelete();
    return;
  }
  if (!iniciado_ && !hayEscaneo_) {
    // Recién encendido: el primer escaneo con la radio recién iniciada falla siempre (medido: -2 al cabo de 4 s, y
    // luego hay que esperar a repetirlo). Se le da ESPERA_INICIAL_MS antes
    WiFi.enableSTA(true);
    hayEscaneo_ = true;
    ultimoEscaneoMs_ = ahora - ESCANEO_MS + ESPERA_INICIAL_MS;
    return;
  }
  if (hayEscaneo_ && ahora - ultimoEscaneoMs_ < ESCANEO_MS) return;
  if (puedeEscanear_ && !puedeEscanear_()) return;
  empezarEscaneo();
}

void EnlaceWifi::empezarEscaneo() {
  WiFi.enableSTA(true);  // sin quitar el AP (ESP-NOW puede ir por él)
  // Sin reconexión automática del framework: reintentaría a ciegas mientras aquí se busca
  WiFi.setAutoReconnect(false);
  hayEscaneo_ = true;
  ultimoEscaneoMs_ = millis();
  // Directamente en el driver, no con WiFi.scanNetworks(true): Arduino da por fallido un escaneo que pasa de 20 veces
  // el tiempo por canal pero no reinicia su reloj, y desde ahí todos los siguientes salían "fallidos" o "con 0 redes"
  // al instante. Medido: tras encender, ninguna placa conectaba nunca; con el AP de ESP-NOW (AP+STA) el escaneo tarda
  // más y caía siempre. Aquí el fin lo marca el SCAN_DONE del driver (registrarEventos) y el límite es propio.
  WiFi.scanDelete();
  escaneoListo_ = false;
  wifi_scan_config_t c = {};
  c.show_hidden = false;
  c.scan_type = WIFI_SCAN_TYPE_ACTIVE;
  c.scan_time.active.min = 60;   // ms por canal
  c.scan_time.active.max = 150;
  const esp_err_t r = esp_wifi_scan_start(&c, false);
  escaneando_ = r == ESP_OK;
  if (!escaneando_) {
    esp_wifi_scan_stop();
    falloEscaneo(String("no arrancó (") + esp_err_to_name(r) + ")");
  }
  if (!iniciado_) {
    iniciado_ = true;
    log_("WiFi: buscando las redes guardadas");
  }
}

// Un escaneo que no arranca o no termina: se repite en REPETIR_ESCANEO_MS y se anota (como mucho una vez por minuto)
void EnlaceWifi::falloEscaneo(const String &que) {
  escaneosFallidos_++;
  ultimoEscaneoMs_ = millis() - ESCANEO_MS + REPETIR_ESCANEO_MS;
  if (avisoEscaneoMs_ == 0 || millis() - avisoEscaneoMs_ >= 60000) {
    avisoEscaneoMs_ = millis();
    log_("WiFi: la búsqueda de redes " + que + " (" + String(escaneosFallidos_) + " seguidas); se repite");
  }
}

void EnlaceWifi::elegirYConectar(const std::vector<RedWifi> &redes, int16_t vistas) {
  const uint32_t ahora = millis();
  int mejor = -1, mejorRed = -1;
  int32_t mejorPuntos = -10000;
  String lista;
  for (int16_t i = 0; i < vistas; i++) {
    const String ssid = WiFi.SSID(i);
    for (size_t k = 0; k < redes.size(); k++) {
      if (redes[k].ssid != ssid) continue;
      const int32_t rssi = WiFi.RSSI(i);
      if (lista.indexOf(ssid + " (") < 0)
        lista += (lista.length() ? ", " : "") + ssid + " (canal " + String(WiFi.channel(i)) + ", " + String(rssi) + " dBm)";
      int32_t puntos = rssi;
      if (ssid == fallidaSsid_ && ahora - fallidaMs_ < CASTIGO_FALLIDA_MS) puntos -= 40;  // otra, si hay
      if (puntos > mejorPuntos) {
        mejorPuntos = puntos;
        mejor = i;
        mejorRed = (int)k;
      }
      break;
    }
  }
  if (lista != vistas_) {  // solo cuando cambia lo que se ve, para no llenar el registro
    vistas_ = lista;
    log_(lista.length() ? "WiFi a la vista: " + lista : String("WiFi: ninguna red guardada a la vista"));
  }
  if (mejor < 0) return;
  conectarA(redes[mejorRed], WiFi.channel(mejor), WiFi.BSSID(mejor), WiFi.RSSI(mejor));
}

void EnlaceWifi::conectarA(const RedWifi &r, int32_t canal, const uint8_t *bssid, int32_t rssi) {
  intentoSsid_ = r.ssid;
  intentoClave_ = r.clave;
  intentoCanal_ = canal;
  if (bssid) memcpy(intentoBssid_, bssid, 6);
  reintentos_ = 0;
  log_("Conectando al WiFi " + r.ssid + " (canal " + String(canal) + ", " + String(rssi) + " dBm)...");
  avisarCanal(static_cast<uint8_t>(canal));  // ESP-NOW al canal del AP ANTES de empezar a autenticar
  empezarIntento();
}

// WiFi.begin no bloquea: la conexión avanza mientras se sigue cobrando
void EnlaceWifi::empezarIntento() {
  desconexiones_ = 0;
  fallos_ = 0;
  WiFi.disconnect();
  WiFi.begin(intentoSsid_.c_str(), intentoClave_.c_str(), intentoCanal_, intentoBssid_);
  intentando_ = true;
  inicioIntento_ = millis();
}

void EnlaceWifi::prepararTls() {
  if (tlsListo_) return;
  seguro_.setCACert(raices_);
  seguro_.setHandshakeTimeout(10);  // s
  tlsListo_ = true;
}

WiFiClient &EnlaceWifi::clienteHttp(bool tls) {
  if (!tls) return plano_;
  if (raices_) prepararTls();
  return seguro_;
}

Client *EnlaceWifi::abrir(const char *host, uint16_t puerto) {
  if (!raices_) {  // sin raíces no hay forma de verificar al servidor: no se abre sin verificar
    ultimoError_ = "faltan las raíces de confianza TLS";
    return nullptr;
  }
  prepararTls();
  return abrirCon(seguro_, true, host, puerto);
}

Client *EnlaceWifi::abrirPlano(const char *host, uint16_t puerto) {
  return abrirCon(plano_, false, host, puerto);
}

Client *EnlaceWifi::abrirCon(WiFiClient &cliente, bool tls, const char *host, uint16_t puerto) {
  ultimoError_ = "";
  if (estado() != EstadoEnlace::Listo) {
    ultimoError_ = "WiFi no está listo";
    return nullptr;
  }
  const String destino = String(tls ? "tls|" : "tcp|") + host + ":" + String(puerto);
  contado_.usar(&cliente, &tx_, &rx_);
  if (destino == destino_ && cliente.connected()) return &contado_;  // keep-alive
  cerrar();

  IPAddress ip;
  const String nombre = host;
  if (nombre.endsWith(".local")) {
    ip = resolverLocal(nombre);
    if (ip == IPAddress()) {
      ultimoError_ = "no se encontró " + nombre + " en la red local";
      return nullptr;
    }
  }
  // Los tiempos son los del sincronizador v1: por internet se tarda más que en la red local
  const int32_t espera = tls ? 5000 : 2000;
  const int conectado = ip == IPAddress() ? cliente.connect(host, puerto, espera)
                                          : cliente.connect(ip, puerto, espera);
  if (!conectado) {
    char detalle[100];
    ultimoError_ = "no se pudo conectar a " + nombre + ":" + String(puerto);
    if (tls && seguro_.lastError(detalle, sizeof(detalle))) ultimoError_ += String(" (TLS: ") + detalle + ")";
    cliente.stop();
    return nullptr;
  }
  destino_ = destino;
  return &contado_;
}

void EnlaceWifi::cerrarPropio() {
  if (prestado_) cierrePendiente_ = true;
  else cerrar();
}

void EnlaceWifi::prestarFlujo(bool prestado) {
  prestado_ = prestado;
  if (!prestado && cierrePendiente_) {
    cierrePendiente_ = false;
    cerrar();
  }
}

void EnlaceWifi::cerrar() {
  plano_.stop();
  seguro_.stop();
  destino_ = "";
}

IPAddress EnlaceWifi::resolverLocal(const String &host) {
  // La IP del ordenador con el servidor la reparte el router y puede cambiar; con
  // "nombre.local" se busca en la red local (mDNS) y se recuerda la IP
  if (!host.endsWith(".local")) return IPAddress();
  if (host == hostLocal_ && ipLocal_ != IPAddress()) return ipLocal_;
  if (!mdnsIniciado_) mdnsIniciado_ = MDNS.begin(miNombre_.c_str());
  ipLocal_ = MDNS.queryHost(host.substring(0, host.length() - 6), ESPERA_MDNS_MS);
  hostLocal_ = ipLocal_ == IPAddress() ? "" : host;
  return ipLocal_;
}

void EnlaceWifi::olvidarNombres() {
  hostLocal_ = "";
  ipLocal_ = IPAddress();
}
