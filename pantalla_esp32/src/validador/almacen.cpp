#include "almacen.h"

#include <SD.h>
#include <SPI.h>
#include <time.h>

namespace {

// microSD en el segundo bus SPI (HSPI), para no interferir con la pantalla
constexpr int8_t SD_SCK = 14, SD_MOSI = 13, SD_MISO = 27, SD_CS = 26;
SPIClass spiSD(HSPI);

String leerArchivo(const char *ruta) {
  File f = SD.open(ruta, FILE_READ);
  if (!f) return "";
  String s = f.readString();
  f.close();
  return s;
}

bool escribirArchivo(const char *ruta, const String &contenido) {
  File f = SD.open(ruta, FILE_WRITE);  // FILE_WRITE en ESP32 = truncar y escribir
  if (!f) return false;
  size_t n = f.print(contenido);
  f.close();
  return n == contenido.length();
}

// Recorre las líneas no vacías de un texto
template <typename F>
void porLinea(const String &texto, F f) {
  int inicio = 0;
  while (inicio < (int)texto.length()) {
    int fin = texto.indexOf('\n', inicio);
    if (fin < 0) fin = texto.length();
    String l = texto.substring(inicio, fin);
    l.trim();
    if (l.length()) f(l);
    inicio = fin + 1;
  }
}

String fechaIso(uint32_t unix) {
  time_t t = unix;
  struct tm tm;
  gmtime_r(&t, &tm);
  char b[32];
  strftime(b, sizeof(b), "%Y-%m-%dT%H:%M:%S+00:00", &tm);
  return b;
}

String jsonTexto(const String &s) {
  String r = "\"";
  for (char c : s) {
    if (c == '"' || c == '\\') r += '\\';
    if (c >= 32) r += c;
  }
  return r + "\"";
}

bool deHex32(const String &s, uint8_t out[32]) {
  if (s.length() != 64) return false;
  for (int i = 0; i < 32; i++) {
    char b[3] = {s[2 * i], s[2 * i + 1], 0};
    char *fin;
    out[i] = strtoul(b, &fin, 16);
    if (*fin) return false;
  }
  return true;
}

}  // namespace

bool Almacen::montar() {
#ifdef MONTAJE_FALLA_PRUEBA
  // Solo para comprobar los reintentos (-DMONTAJE_FALLA_PRUEBA=N): los N primeros montajes fallan
  static uint32_t llamadas = 0;
  if (llamadas++ < MONTAJE_FALLA_PRUEBA) return false;
#endif
  // Si un intento anterior falló, se suelta el bus y se vuelve a abrir: la tarjeta puede haber
  // quedado a mitad de una orden tras un reinicio brusco y solo contesta al volver a empezar
  SD.end();
  spiSD.end();
  spiSD.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  return SD.begin(SD_CS, spiSD, 4000000);
}

bool Almacen::iniciar() {
  for (uint8_t intento = 1; intento <= INTENTOS_MONTAJE; intento++) {
    listo_ = montar();
    if (listo_) break;
    Serial.printf("microSD: intento %u de %u sin respuesta\n", intento, INTENTOS_MONTAJE);
    if (intento < INTENTOS_MONTAJE) delay(ESPERA_MONTAJE_MS);
  }
  ultimoIntentoMontaje_ = millis();
  if (!listo_) return false;
  cargarDatos();
  return true;
}

bool Almacen::reintentarMontaje() {
  if (listo_ || millis() - ultimoIntentoMontaje_ < REINTENTO_MONTAJE_MS) return false;
  ultimoIntentoMontaje_ = millis();
  if (!montar()) return false;
  listo_ = true;
  Serial.println("microSD: montada al reintentar");
  cargarDatos();
  return true;
}

void Almacen::cargarDatos() {
  cargarConfig();
  String estado = leerArchivo("/estado.txt");
  porLinea(estado, [&](const String &l) {
    if (l.startsWith("siguiente_id=")) siguienteId_ = l.substring(13).toInt();
    if (l.startsWith("ultimo_subido=")) ultimoSubido_ = l.substring(14).toInt();
  });
  if (siguienteId_ < 1) siguienteId_ = 1;
  porLinea(leerArchivo("/ultima_op.txt"), [&](const String &l) {
    int sp = l.indexOf(' ');
    if (sp > 0) ultimaOp_[l.substring(0, sp).toInt()] = l.substring(sp + 1).toInt();
  });
  cargarListas();
  cargarRedes();
  // Sin ellos el validador sigue cobrando: solo no puede guardar paquetes de otros buses
  if (!ajenos_.iniciar()) Serial.println("AVISO: no se pudo preparar /ajenos en la microSD");
  if (!firmados_.iniciar()) Serial.println("AVISO: no se pudo preparar /firmados en la microSD");
  consumo_.iniciar();  // sin historial todavía la primera vez
}

void Almacen::cargarConfig() {
  config_.clear();
  porLinea(leerArchivo("/config.txt"), [&](const String &l) {
    if (l.startsWith("#")) return;
    int eq = l.indexOf('=');
    if (eq > 0) {
      String k = l.substring(0, eq), v = l.substring(eq + 1);
      k.trim();
      v.trim();
      config_[k] = v;
    }
  });
  validador_ = config("validador").toInt();
  tieneClave_ = deHex32(config("clave_maestra"), clave_);
}

void Almacen::cargarListas() {
  // Se recargan enteras en cada sincronización: el servidor manda la lista completa
  listaNegra_.clear();
  const bool hayRecargas = recargas_.iniciar();
  const bool hayTarifas = tarifasBin_.iniciar();
  migrarListasDeTexto(hayRecargas, hayTarifas);
  porLinea(leerArchivo("/lista_negra.txt"), [&](const String &l) { listaNegra_[l.toInt()] = true; });
}

// Un validador que ya tenía datos de una versión anterior los conserva: lo que haya en los
// archivos de texto pasa a los binarios (una sola vez; el texto se borra al conseguirlo).
void Almacen::migrarListasDeTexto(bool hayRecargas, bool hayTarifas) {
  if (!hayRecargas && SD.exists("/recargas.txt")) {
    std::vector<Recarga> r;
    porLinea(leerArchivo("/recargas.txt"), [&](const String &l) {
      int a = l.indexOf(' '), b = l.lastIndexOf(' ');
      if (a > 0 && b > a) r.push_back({(uint32_t)l.substring(0, a).toInt(), (uint32_t)l.substring(a + 1, b).toInt(), (int32_t)l.substring(b + 1).toInt()});
    });
    if (recargas_.reemplazar(std::move(r))) {
      SD.remove("/recargas.txt");
      Serial.println("microSD: /recargas.txt migrado a /recargas.bin");
    }
  }
  if (!hayTarifas && SD.exists("/tarifas.txt")) {
    if (guardarTarifasDeTexto(leerArchivo("/tarifas.txt"))) {
      SD.remove("/tarifas.txt");
      Serial.println("microSD: /tarifas.txt migrado a /tarifas.bin");
    }
  }
}

String Almacen::config(const String &clave) const {
  auto it = config_.find(clave);
  return it == config_.end() ? "" : it->second;
}

bool Almacen::guardarConfig(const String &pares) {
  // Conserva las claves que ya había y sustituye las recibidas
  String entrada = pares;
  entrada.replace(";", "\n");
  porLinea(entrada, [&](const String &l) {
    int eq = l.indexOf('=');
    if (eq > 0) config_[l.substring(0, eq)] = l.substring(eq + 1);
  });
  String texto = "# Configuración del validador (generada por puente_nfc.py --configurar)\n";
  for (auto &kv : config_) texto += kv.first + "=" + kv.second + "\n";
  if (!escribirArchivo("/config.txt", texto)) return false;
  cargarConfig();
  cargarRedes();  // por si llegó una red nueva con --configurar --wifi
  return configurado();
}

bool Almacen::bloqueada(uint32_t idTarjeta) const { return listaNegra_.count(idTarjeta) > 0; }

bool Almacen::tarifa(uint8_t codigo, Tarifa &t) const {
  if (tarifasBin_.cantidad() == 0) {  // sin sincronizar todavía: las de siempre
    if (codigo == 1) t = {"general", 130};
    else if (codigo == 2) t = {"estudiante", 80};
    else return false;
    return true;
  }
  sp::Tarifa c;
  if (!tarifasBin_.tarifa(codigo, c)) return false;
  t = {String(c.nombre), static_cast<int32_t>(c.precio)};
  return true;
}

uint32_t Almacen::ultimaOperacion(uint32_t idTarjeta) const {
  auto it = ultimaOp_.find(idTarjeta);
  return it == ultimaOp_.end() ? 0 : it->second;
}

void Almacen::recargasPendientes(uint32_t idCuenta, uint32_t ultima, int32_t &total,
                                 uint32_t &hasta) const {
  recargas_.pendientes(idCuenta, ultima, total, hasta);  // solo correlativas: no se salta ninguna
}

bool Almacen::existeViaje(uint32_t idTarjeta, uint16_t operacion) {
  String a = "\"tarjeta_id\":" + String(idTarjeta) + ",";
  String b = "\"operacion\":" + String(operacion) + ",";
  bool hay = false;
  File f = SD.open("/eventos.txt", FILE_READ);
  if (!f) return false;
  while (f.available() && !hay) {
    String l = f.readStringUntil('\n');
    hay = l.indexOf("\"tipo\":\"viaje\"") >= 0 && l.indexOf(a) >= 0 && l.indexOf(b) >= 0;
  }
  f.close();
  return hay;
}

bool Almacen::agregarEvento(const String &json) {
  File f = SD.open("/eventos.txt", FILE_APPEND);
  if (!f) return false;
  size_t n = f.println(json);
  f.close();
  if (n < json.length()) return false;
  siguienteId_++;
  guardarEstado();
  return true;
}

void Almacen::guardarEstado() {
  escribirArchivo("/estado.txt", "siguiente_id=" + String(siguienteId_) + "\nultimo_subido=" +
                                     String(ultimoSubido_) + "\n");
}

void Almacen::guardarUltimasOperaciones() {
  String t;
  for (auto &kv : ultimaOp_) t += String(kv.first) + " " + String(kv.second) + "\n";
  escribirArchivo("/ultima_op.txt", t);
}

bool Almacen::registrarViaje(uint32_t fecha, uint32_t idTarjeta, const String &uid, int32_t monto,
                             int32_t saldoFinal, uint16_t operacion, uint32_t contador,
                             uint32_t recargaHasta) {
  String j = "{\"id\":" + String(siguienteId_) + ",\"tipo\":\"viaje\",\"fecha\":\"" +
             fechaIso(fecha) + "\",\"tarjeta_id\":" + String(idTarjeta) + ",\"uid\":\"" + uid +
             "\",\"monto\":" + String(monto) + ",\"saldo_final\":" + String(saldoFinal) +
             ",\"operacion\":" + String(operacion) + ",\"contador\":" + String(contador) +
             ",\"recarga_hasta\":" + (recargaHasta ? String(recargaHasta) : String("null")) +
             ",\"detalle\":null}";
  if (!agregarEvento(j)) return false;
  if (operacion > ultimaOperacion(idTarjeta)) {
    ultimaOp_[idTarjeta] = operacion;
    guardarUltimasOperaciones();
  }
  return true;
}

bool Almacen::registrarIncidencia(uint32_t fecha, uint32_t idTarjeta, const String &uid,
                                  const String &detalle, bool fraude) {
  String j = "{\"id\":" + String(siguienteId_) + ",\"tipo\":\"" +
             (fraude ? "fraude" : "incidencia") + "\",\"fecha\":\"" + fechaIso(fecha) +
             "\",\"tarjeta_id\":" + String(idTarjeta) + ",\"uid\":\"" + uid +
             "\",\"monto\":null,\"saldo_final\":null,\"operacion\":null,\"contador\":null," +
             "\"recarga_hasta\":null,\"detalle\":" + jsonTexto(detalle) + "}";
  return agregarEvento(j);
}

String Almacen::eventosPendientes(size_t max, std::vector<uint32_t> &ids) {
  ids.clear();
  String json = "[";
  File f = SD.open("/eventos.txt", FILE_READ);
  if (!f) return "[]";
  while (f.available() && ids.size() < max) {
    String l = f.readStringUntil('\n');
    l.trim();
    // Una línea cortada (corte de corriente al escribirla) no se envía
    if (!l.startsWith("{\"id\":") || !l.endsWith("}")) continue;
    uint32_t id = l.substring(6).toInt();
    if (id <= ultimoSubido_) continue;
    if (ids.size()) json += ",";
    json += l;
    ids.push_back(id);
  }
  f.close();
  return json + "]";
}

void Almacen::marcarSubidos(uint32_t hastaId) {
  if (hastaId <= ultimoSubido_) return;
  ultimoSubido_ = hastaId;
  guardarEstado();
}

// Las tarifas del texto "código nombre precio" (una por línea) a TARIFAS de C1. El servidor no manda
// aquí una versión, así que queda en 0.
bool Almacen::guardarTarifasDeTexto(const String &texto) {
  std::unique_ptr<sp::Tarifas> t(new sp::Tarifas());
  memset(t.get(), 0, sizeof(*t));
  porLinea(texto, [&](const String &l) {
    int a = l.indexOf(' '), b = l.lastIndexOf(' ');
    if (a <= 0 || b <= a || t->n >= sp::MAX_TARIFAS) return;
    sp::Tarifa &x = t->tarifas[t->n++];
    x.codigo = l.substring(0, a).toInt();
    x.precio = max<int32_t>(0, l.substring(b + 1).toInt());
    strncpy(x.nombre, l.substring(a + 1, b).c_str(), 16);
    x.nombre[16] = 0;
  });
  return tarifasBin_.guardar(*t);
}

bool Almacen::guardarListas(const String &listaNegra, const String &tarifas, const String &recargas) {
  std::vector<Recarga> r;
  porLinea(recargas, [&](const String &l) {
    int a = l.indexOf(' '), b = l.lastIndexOf(' ');
    if (a > 0 && b > a) r.push_back({(uint32_t)l.substring(0, a).toInt(), (uint32_t)l.substring(a + 1, b).toInt(), (int32_t)l.substring(b + 1).toInt()});
  });
  bool ok = escribirArchivo("/lista_negra.txt", listaNegra) && guardarTarifasDeTexto(tarifas) &&
            recargas_.reemplazar(std::move(r));
  cargarListas();
  return ok;
}

void Almacen::cargarRedes() {
  redes_.clear();
  porLinea(leerArchivo("/redes.txt"), [&](const String &l) {
    int tab = l.indexOf('\t');
    if (tab > 0) redes_.push_back({l.substring(0, tab), l.substring(tab + 1)});
  });
  // La red de config.txt (puente_nfc.py --configurar --wifi) también cuenta
  const String ssid = config("wifi_ssid");
  if (ssid.length()) {
    bool esta = false;
    for (auto &r : redes_) esta = esta || r.ssid == ssid;
    if (!esta) redes_.push_back({ssid, config("wifi_clave")});
  }
}

bool Almacen::guardarRedes() {
  String t;
  for (auto &r : redes_) t += r.ssid + "\t" + r.clave + "\n";
  return escribirArchivo("/redes.txt", t);
}

bool Almacen::guardarRed(const String &ssid, const String &clave) {
  for (size_t i = 0; i < redes_.size(); i++) {
    if (redes_[i].ssid == ssid) {
      redes_.erase(redes_.begin() + i);
      break;
    }
  }
  redes_.insert(redes_.begin(), {ssid, clave});
  if (redes_.size() > MAX_REDES) redes_.resize(MAX_REDES);
  return guardarRedes();
}

bool Almacen::olvidarRed(const String &ssid) {
  for (size_t i = 0; i < redes_.size(); i++) {
    if (redes_[i].ssid == ssid) {
      redes_.erase(redes_.begin() + i);
      if (!guardarRedes()) return false;
      // Si era la de config.txt, se quita también de ahí para que no vuelva
      return config("wifi_ssid") != ssid || guardarConfig("wifi_ssid=;wifi_clave=");
    }
  }
  return true;
}
