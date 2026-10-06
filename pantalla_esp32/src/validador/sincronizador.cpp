#include "sincronizador.h"

#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <WiFi.h>

#include <set>
#include <vector>

namespace {

constexpr uint32_t INTERVALO = 30000;        // ms entre sincronizaciones correctas
constexpr uint32_t REINTENTO_MIN = 10000;    // ms tras un fallo; se duplica hasta el máximo
constexpr uint32_t REINTENTO_MAX = 300000;
constexpr uint32_t ESPERA_WIFI = 20000;      // ms para conectar antes de probar otra red
constexpr size_t EVENTOS_POR_PETICION = 40;  // acota la memoria del cuerpo JSON
constexpr uint32_t ESPERA_MDNS = 2000;       // ms para encontrar un servidor "nombre.local"

// Días desde 1970-01-01 (algoritmo de H. Hinnant; sin depender de la zona horaria)
int32_t diasDesdeCivil(int y, unsigned m, unsigned d) {
  y -= m <= 2;
  const int era = (y >= 0 ? y : y - 399) / 400;
  const unsigned yoe = (unsigned)(y - era * 400);
  const unsigned doy = (153 * (m > 2 ? m - 3 : m + 9) + 2) / 5 + d - 1;
  const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return era * 146097 + (int32_t)doe - 719468;
}

// "2026-10-03T19:30:00+00:00" (o con 'Z', o con fracciones de segundo) -> unix
uint32_t isoAUnix(const char *s) {
  int Y, M, D, h, m, sec;
  if (!s || sscanf(s, "%d-%d-%dT%d:%d:%d", &Y, &M, &D, &h, &m, &sec) != 6) return 0;
  int64_t t = (int64_t)diasDesdeCivil(Y, M, D) * 86400 + h * 3600 + m * 60 + sec;
  const char *zona = s + 19;
  while (*zona == '.' || isdigit((unsigned char)*zona)) zona++;
  int zh = 0, zm = 0;
  if ((*zona == '+' || *zona == '-') && sscanf(zona + 1, "%d:%d", &zh, &zm) == 2) {
    t -= (*zona == '+' ? 1 : -1) * (zh * 3600 + zm * 60);
  }
  return t > 0 ? (uint32_t)t : 0;
}

}  // namespace

bool Sincronizador::atender() {
  if (!almacen_.listo() || !almacen_.configurado()) return false;
  const auto &redes = almacen_.redes();
  if (redes.empty() || !almacen_.config("servidor").length() || !almacen_.config("token").length()) {
    estado_ = "";
    return false;
  }

  if (WiFi.status() != WL_CONNECTED) {
    estado_ = "Sin WiFi";
    if (wifiConectado_) {
      wifiConectado_ = false;
      log_("WiFi perdido; se sigue cobrando sin conexión");
    }
    // WiFi.begin no bloquea: la conexión avanza mientras se sigue cobrando. Si una
    // red no conecta en ESPERA_WIFI, se prueba la siguiente de las guardadas.
    if (!wifiIniciado_ || millis() - inicioWifi_ > ESPERA_WIFI) {
      if (wifiIniciado_) red_ = (red_ + 1) % redes.size();
      if (red_ >= redes.size()) red_ = 0;
      const RedWifi &r = redes[red_];
      if (!wifiIniciado_ || redes.size() > 1) log_("Conectando al WiFi " + r.ssid + "...");
      WiFi.mode(WIFI_STA);
      WiFi.setAutoReconnect(true);
      WiFi.disconnect();
      WiFi.begin(r.ssid.c_str(), r.clave.c_str());
      wifiIniciado_ = true;
      inicioWifi_ = millis();
    }
    return false;
  }
  if (!wifiConectado_) {
    wifiConectado_ = true;
    log_("WiFi conectado a " + WiFi.SSID() + ": IP " + WiFi.localIP().toString() + ", señal " + String(WiFi.RSSI()) + " dBm");
    proxima_ = millis();  // sincroniza en cuanto hay red
  }

  if ((int32_t)(millis() - proxima_) < 0) return false;
  const bool ok = sincronizar();
  estado_ = ok ? "WiFi OK" : "Sin servidor";
  if (ok) {
    reintento_ = 0;
    proxima_ = millis() + INTERVALO;
  } else {
    reintento_ = reintento_ ? min(reintento_ * 2, REINTENTO_MAX) : REINTENTO_MIN;
    proxima_ = millis() + reintento_;
  }
  return ok;
}

bool Sincronizador::resolverNombre(String &url) {
  // La IP del ordenador con el servidor la reparte el router y puede cambiar; con
  // "http://nombre.local:8000" se busca en la red local (mDNS) y se guarda la IP
  const int inicio = url.indexOf("://") + 3;
  int fin = inicio;
  while (fin < (int)url.length() && url[fin] != ':' && url[fin] != '/') fin++;
  const String host = url.substring(inicio, fin);
  if (!host.endsWith(".local")) return true;
  if (ipServidor_ == IPAddress()) {
    if (!mdnsIniciado_) mdnsIniciado_ = MDNS.begin(("validador-" + String(almacen_.validador())).c_str());
    ipServidor_ = MDNS.queryHost(host.substring(0, host.length() - 6), ESPERA_MDNS);
    if (ipServidor_ == IPAddress()) {
      fallo("no se encontró " + host + " en la red (¿ordenador apagado o en otra red?)");
      return false;
    }
  }
  url = url.substring(0, inicio) + ipServidor_.toString() + url.substring(fin);
  return true;
}

void Sincronizador::reiniciarWifi() {
  wifiIniciado_ = false;
  wifiConectado_ = false;
  red_ = 0;
  ipServidor_ = IPAddress();
}

void Sincronizador::fallo(const String &motivo) {
  // Solo se avisa al pasar de bien a mal, para no llenar la consola con reintentos
  if (ultimaOk_) log_("Sincronización fallida: " + motivo + " (se reintentará)");
  ultimaOk_ = false;
}

bool Sincronizador::sincronizar() {
  std::vector<uint32_t> ids;
  const String eventos = almacen_.eventosPendientes(EVENTOS_POR_PETICION, ids);
  String url = almacen_.config("servidor");
  while (url.endsWith("/")) url.remove(url.length() - 1);
  url += "/api/sync/";
  if (!resolverNombre(url)) return false;

  WiFiClient cliente;
  HTTPClient http;
  http.setConnectTimeout(2000);
  http.setTimeout(4000);
  if (!http.begin(cliente, url)) {
    fallo("URL del servidor no válida: " + url);
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Accept", "application/json");
  http.addHeader("Authorization", "Token " + almacen_.config("token"));
  const int codigo = http.POST("{\"validador\":" + String(almacen_.validador()) + ",\"eventos\":" + eventos + "}");
  if (codigo != 200) {
    String motivo = codigo < 0 ? HTTPClient::errorToString(codigo) : "HTTP " + String(codigo);
    if (codigo == 401 || codigo == 403) motivo += " (token no válido o validador inactivo)";
    http.end();
    ipServidor_ = IPAddress();  // por si cambió de IP: se vuelve a buscar
    fallo(motivo + " en " + url);
    return false;
  }
  const String cuerpo = http.getString();
  http.end();
  const uint32_t recibido = millis();

  JsonDocument doc;
  if (deserializeJson(doc, cuerpo)) {
    fallo("respuesta del servidor no válida");
    return false;
  }
  if (doc["validador"].as<uint32_t>() != almacen_.validador()) {
    fallo("el token es del validador " + String(doc["validador"].as<uint32_t>()) + ", no del " +
          String(almacen_.validador()));
    return false;
  }

  // Solo se marca lo que el servidor confirmó: si se corta antes de la respuesta,
  // se reenvía y el servidor ignora los repetidos
  std::set<uint32_t> aceptados;
  for (JsonVariant v : doc["aceptados"].as<JsonArray>()) aceptados.insert(v.as<uint32_t>());
  uint32_t hasta = 0;
  size_t subidos = 0;
  for (uint32_t id : ids) {
    if (!aceptados.count(id)) break;
    hasta = id;
    subidos++;
  }
  almacen_.marcarSubidos(hasta);

  String negra, tarifas, recargas;
  for (JsonVariant v : doc["lista_negra"].as<JsonArray>()) negra += String(v.as<uint32_t>()) + "\n";
  for (JsonPair t : doc["tarifas"].as<JsonObject>()) {
    tarifas += String(t.key().c_str()) + " " + t.value()[0].as<String>() + " " + String(t.value()[1].as<int32_t>()) + "\n";
  }
  for (JsonVariant r : doc["recargas"].as<JsonArray>()) {
    recargas += String(r["cuenta_id"].as<uint32_t>()) + " " + String(r["seq"].as<uint32_t>()) + " " +
                String(r["monto"].as<int32_t>()) + "\n";
  }
  if (!almacen_.guardarListas(negra, tarifas, recargas)) {
    fallo("no se pudieron guardar las listas en la microSD");
    return false;
  }

  const uint32_t hora = isoAUnix(doc["hora_servidor"].as<const char *>());
  if (hora) {
    horaServidor_ = hora;
    millisHora_ = recibido;
  }
  if (subidos || !ultimaOk_ || primera_) {
    log_("Sincronizado: " + String(subidos) + " eventos subidos, " + String(almacen_.pendientes()) +
         " pendientes, " + String(almacen_.tamListaNegra()) + " en lista negra, " +
         String(almacen_.numRecargas()) + " recargas pendientes");
  }
  ultimaOk_ = true;
  primera_ = false;
  return true;
}
