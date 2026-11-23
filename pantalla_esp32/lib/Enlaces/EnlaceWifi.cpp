#include "EnlaceWifi.h"

#include <ESPmDNS.h>
#include <WiFi.h>

volatile uint8_t EnlaceWifi::motivos_[EnlaceWifi::MOTIVOS] = {};
volatile uint16_t EnlaceWifi::desconexiones_ = 0;

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
  // El primer motivo suele ser el 8 del WiFi.disconnect() que hace conectarSiguiente() antes de cada intento
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
  iniciado_ = false;  // el siguiente atender() empieza a conectar
}

void EnlaceWifi::apagar() {
  encendido_ = false;
  iniciado_ = false;
  conectado_ = false;
  cerrar();
  olvidarNombres();
  WiFi.disconnect();
  WiFi.mode(WIFI_OFF);
}

void EnlaceWifi::reiniciar() {
  iniciado_ = false;
  conectado_ = false;
  red_ = 0;
  cerrar();
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

void EnlaceWifi::atender() {
  if (!encendido_) return;
  const auto &redes = redes_();
  if (redes.empty()) return;

  if (WiFi.status() == WL_CONNECTED) {
    if (!conectado_) {
      conectado_ = true;
      log_("WiFi conectado a " + WiFi.SSID() + ": IP " + WiFi.localIP().toString() + ", señal " +
           String(WiFi.RSSI()) + " dBm");
    }
    return;
  }

  if (conectado_) {
    conectado_ = false;
    cerrar();
    olvidarNombres();  // la IP del servidor local puede ser otra al volver
    log_("WiFi perdido; se sigue cobrando sin conexión");
  }
  // WiFi.begin no bloquea: la conexión avanza mientras se sigue cobrando. Si una red
  // no conecta en ESPERA_RED_MS, se prueba la siguiente de las guardadas.
  if (iniciado_ && WiFi.status() == WL_CONNECT_FAILED && reintentos_ < REINTENTOS_RED && red_ < redes.size() &&
      millis() - inicioIntento_ > PAUSA_REINTENTO_MS) {
    reintentos_++;
    const RedWifi &r = redes[red_];
    log_("WiFi " + r.ssid + ": rechazó la conexión (motivo " + String(motivos_[0]) + "); reintento " +
         String(reintentos_) + " de " + String(REINTENTOS_RED));
    desconexiones_ = 0;
    WiFi.disconnect();
    WiFi.begin(r.ssid.c_str(), r.clave.c_str());
    inicioIntento_ = millis();
    return;
  }
  if (!iniciado_ || millis() - inicioIntento_ > ESPERA_RED_MS) conectarSiguiente(redes);
}

void EnlaceWifi::conectarSiguiente(const std::vector<RedWifi> &redes) {
  if (!eventosRegistrados_) {
    eventosRegistrados_ = true;
    WiFi.onEvent(
        [](WiFiEvent_t, WiFiEventInfo_t info) {
          if (desconexiones_ < MOTIVOS) motivos_[desconexiones_] = info.wifi_sta_disconnected.reason;
          desconexiones_++;
        },
        ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  }
  if (iniciado_ && red_ < redes.size()) anotarFallo(redes[red_].ssid);
  desconexiones_ = 0;
  reintentos_ = 0;
  if (iniciado_) red_ = (red_ + 1) % redes.size();
  if (red_ >= redes.size()) red_ = 0;
  const RedWifi &r = redes[red_];
  if (!iniciado_ || redes.size() > 1) log_("Conectando al WiFi " + r.ssid + "...");
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.disconnect();
  WiFi.begin(r.ssid.c_str(), r.clave.c_str());
  iniciado_ = true;
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
