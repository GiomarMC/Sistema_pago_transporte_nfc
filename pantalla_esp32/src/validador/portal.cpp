#include "portal.h"

#include <DNSServer.h>
#include <PantallaUI.h>
#include <WebServer.h>
#include <WiFi.h>
#include <esp_random.h>

namespace {

constexpr uint32_t SIN_USO_MAX = 10 * 60000UL;  // ms sin peticiones antes de cerrarse
const IPAddress IP_PORTAL(192, 168, 4, 1);

DNSServer dns;
WebServer web(80);

String html(const String &s) {
  String r;
  for (char c : s) {
    if (c == '<') r += "&lt;";
    else if (c == '>') r += "&gt;";
    else if (c == '&') r += "&amp;";
    else if (c == '"') r += "&quot;";
    else r += c;
  }
  return r;
}

const char ESTILO[] PROGMEM = R"(<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:sans-serif;margin:0;background:#eef2f6;color:#123}
header{background:#00285a;color:#fff;padding:16px}h1{font-size:20px;margin:0}
main{padding:16px;max-width:480px;margin:auto}section{background:#fff;border-radius:10px;padding:16px;margin-bottom:16px}
label{display:block;font-weight:bold;margin:12px 0 4px}input,select{width:100%;box-sizing:border-box;padding:10px;font-size:16px;border:1px solid #9ab;border-radius:6px}
button{width:100%;padding:12px;font-size:16px;border:0;border-radius:6px;background:#00285a;color:#fff;margin-top:16px}
.sec{background:#678}.aviso{background:#dfd;padding:10px;border-radius:6px}.red{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #dde;padding:8px 0}
.red form button{width:auto;margin:0;padding:6px 10px;font-size:14px;background:#a33}small{color:#567}</style>)";

}  // namespace

void PortalWifi::iniciar() {
  if (activo_) return;
  nombreRed_ = "Validador-" + String(almacen_.validador());
  claveRed_ = String(10000000 + esp_random() % 90000000);  // 8 cifras: el mínimo de WPA2

  // Las redes de alrededor se buscan antes de crear la propia (tarda unos 2 s)
  WiFi.disconnect();
  WiFi.mode(WIFI_AP_STA);
  const int n = WiFi.scanNetworks();
  redesVistas_ = "";
  String vistas = "|";
  for (int i = 0; i < n; i++) {  // ya vienen ordenadas por señal
    const String ssid = WiFi.SSID(i);
    if (!ssid.length() || vistas.indexOf("|" + ssid + "|") >= 0) continue;
    vistas += ssid + "|";
    redesVistas_ += "<option value=\"" + html(ssid) + "\">" + html(ssid) + " (" + String(WiFi.RSSI(i)) + " dBm" +
                    (WiFi.encryptionType(i) == WIFI_AUTH_OPEN ? ", abierta" : "") + ")</option>";
  }
  WiFi.scanDelete();

  WiFi.softAPConfig(IP_PORTAL, IP_PORTAL, IPAddress(255, 255, 255, 0));
  WiFi.softAP(nombreRed_.c_str(), claveRed_.c_str());
  dns.start(53, "*", IP_PORTAL);  // todos los nombres apuntan al portal

  static bool rutas = false;  // el servidor web se reutiliza si el portal se abre otra vez
  if (!rutas) registrarRutas();
  rutas = true;
  web.begin();

  activo_ = true;
  ultimoUso_ = millis();
  cerrarEn_ = 0;
  const String lineas[] = {"Conecte el celular a", "*" + nombreRed_, "Contrasena:", "*" + claveRed_,
                           "o abra 192.168.4.1"};
  ui::informacion("CONFIGURAR WIFI", lineas, 5);
  log_("Portal de configuración abierto: red '" + nombreRed_ + "', contraseña en la pantalla");
}

void PortalWifi::registrarRutas() {
  web.on("/", HTTP_GET, [this] {
    if (!redirigirSiOtroHost()) paginaPrincipal();
  });
  web.on("/guardar", HTTP_POST, [this] {
    ultimoUso_ = millis();
    String ssid = web.arg("otra");
    ssid.trim();
    if (!ssid.length()) ssid = web.arg("red");
    const String clave = web.arg("clave");
    if (!ssid.length()) return paginaPrincipal("Elija o escriba una red.");
    if (clave.length() && clave.length() < 8) return paginaPrincipal("La contraseña de un WiFi tiene al menos 8 caracteres.");
    if (!almacen_.guardarRed(ssid, clave)) return paginaPrincipal("No se pudo guardar en la microSD.");
    log_("Portal: red WiFi '" + ssid + "' guardada");
    web.send(200, "text/html; charset=utf-8",
             String("<!doctype html>") + FPSTR(ESTILO) + "<header><h1>Red guardada</h1></header><main><section>" +
                 "<p>El validador se conecta ahora a <b>" + html(ssid) + "</b>.</p>" +
                 "<p>Esta red <b>" + html(nombreRed_) + "</b> desaparece: vuelva a la red de siempre en el celular.</p>" +
                 "<p>El resultado se ve en la pantalla del validador: <b>WiFi OK</b>, o <b>Sin WiFi</b> si la "
                 "contraseña es incorrecta (en ese caso, mantenga pulsado BOOT 3 s para volver aquí).</p>"
                 "</section></main>");
    cerrarEn_ = millis() + 1500;
  });
  web.on("/olvidar", HTTP_POST, [this] {
    ultimoUso_ = millis();
    const String ssid = web.arg("red");
    almacen_.olvidarRed(ssid);
    log_("Portal: red WiFi '" + ssid + "' olvidada");
    paginaPrincipal("Red olvidada: " + ssid);
  });
  web.on("/terminar", HTTP_POST, [this] {
    web.send(200, "text/html; charset=utf-8",
             String("<!doctype html>") + FPSTR(ESTILO) +
                 "<header><h1>Listo</h1></header><main><section><p>El validador vuelve a cobrar.</p></section></main>");
    cerrarEn_ = millis() + 1500;
  });
  // Cualquier otra dirección (las comprobaciones de internet del celular:
  // /generate_204, /hotspot-detect.html, /connecttest.txt...) lleva al portal.
  // Así el celular detecta un "portal cautivo" y lo abre solo.
  web.onNotFound([this] {
    web.sendHeader("Location", "http://192.168.4.1/", true);
    web.send(302, "text/plain", "");
  });
}

bool PortalWifi::redirigirSiOtroHost() {
  // Si el celular pidió otro nombre (p. ej. connectivitycheck.gstatic.com), se le
  // manda a la IP del portal para que la página y el formulario usen esa dirección
  if (web.hostHeader() == IP_PORTAL.toString()) return false;
  web.sendHeader("Location", "http://192.168.4.1/", true);
  web.send(302, "text/plain", "");
  return true;
}

void PortalWifi::paginaPrincipal(const String &aviso) {
  ultimoUso_ = millis();
  String p = String("<!doctype html><html lang=\"es\"><title>Validador</title>") + FPSTR(ESTILO) +
             "<header><h1>Validador " + String(almacen_.validador()) + "</h1><small style=\"color:#cde\">"
             "Configuración WiFi</small></header><main>";
  if (aviso.length()) p += "<p class=\"aviso\">" + html(aviso) + "</p>";

  p += "<section><form method=\"post\" action=\"/guardar\"><label>Red WiFi</label><select name=\"red\">" +
       redesVistas_ + "</select><label>u otra red (oculta o fuera de alcance)</label>"
       "<input name=\"otra\" placeholder=\"Nombre exacto de la red\" autocapitalize=\"none\">"
       "<label>Contraseña</label><input name=\"clave\" type=\"password\" autocomplete=\"off\">"
       "<small>Solo redes de 2,4 GHz. Si usa los datos del celular como punto de acceso, "
       "active en él la banda de 2,4 GHz.</small><button>Guardar y conectar</button></form></section>";

  p += "<section><b>Redes guardadas</b> <small>(se prueban en este orden, máx. " + String(Almacen::MAX_REDES) +
       ")</small>";
  if (almacen_.redes().empty()) p += "<p><small>Ninguna.</small></p>";
  for (auto &r : almacen_.redes()) {
    p += "<div class=\"red\"><span>" + html(r.ssid) +
         "</span><form method=\"post\" action=\"/olvidar\"><input type=\"hidden\" name=\"red\" value=\"" + html(r.ssid) +
         "\"><button>Olvidar</button></form></div>";
  }
  p += "</section><form method=\"post\" action=\"/terminar\"><button class=\"sec\">Terminar sin cambios</button>"
       "</form></main></html>";
  web.send(200, "text/html; charset=utf-8", p);
}

bool PortalWifi::atender() {
  if (!activo_) return false;
  dns.processNextRequest();
  web.handleClient();
  const bool porTiempo = millis() - ultimoUso_ > SIN_USO_MAX;
  if ((cerrarEn_ && (int32_t)(millis() - cerrarEn_) >= 0) || porTiempo) {
    if (porTiempo) log_("Portal de configuración cerrado por inactividad");
    cerrar();
    return true;
  }
  return false;
}

void PortalWifi::cerrar() {
  web.stop();
  dns.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_STA);
  activo_ = false;
  ui::cerrarInformacion();
}
