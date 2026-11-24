#include "almacen.h"

#include <EventosTexto.h>
#include <SD.h>
#include <SPI.h>
#include <esp_task_wdt.h>
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
  // Eventos y estado en binario. Si hay un /eventos.txt de una versión anterior, antes de abrir la cola se deja el
  // estado (siguiente_id, ultimo_subido) como lo tenía, para que los ids sigan donde iban
  const bool hayTexto = SD.exists("/eventos.txt") || SD.exists("/estado.txt");
  estado_.cargar();  // sin copia válida (validador nuevo) arranca con el estado inicial
  if (hayTexto) prepararMigracion();
  if (!cola_.iniciar()) Serial.println("ERROR: no se pudo abrir /eventos.bin en la microSD");
  if (hayTexto) migrarEventosDeTexto();
  porLinea(leerArchivo("/ultima_op.txt"), [&](const String &l) {
    int sp = l.indexOf(' ');
    if (sp > 0) ultimaOp_[l.substring(0, sp).toInt()] = l.substring(sp + 1).toInt();
  });
  dudosos_.deTexto(leerArchivo("/cobros_dudosos.txt").c_str());
  cargarListas();
  v2_ = config("sync") == "v2";
  if (v2_ && !listaNegraBin_.iniciar()) Serial.println("microSD: sin lista negra binaria todavía (arranca vacía)");
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

bool Almacen::bloqueada(uint32_t idTarjeta) {
  if (v2_) return listaNegraBin_.bloqueada(idTarjeta);  // ~36 ms en la microSD a 4 MHz; los urgentes van en RAM
  return listaNegra_.count(idTarjeta) > 0;
}

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

// Un viaje ya registrado aquí: registrarViaje() anota la operación de la tarjeta DESPUÉS de guardar el evento, así
// que "ya la vi" equivale a "ya está guardado" (con la cola compactada tras el acuse, buscarlo en ella ya no vale)
bool Almacen::existeViaje(uint32_t idTarjeta, uint16_t operacion) {
  auto it = ultimaOp_.find(idTarjeta);
  return it != ultimaOp_.end() && it->second >= operacion;
}

void Almacen::guardarCobrosDudosos() {
  char texto[CobrosDudosos::MAXIMO * 80];
  dudosos_.aTexto(texto, sizeof(texto));
  escribirArchivo("/cobros_dudosos.txt", texto);
}

void Almacen::anotarCobroDudoso(const CobroDudoso &c) {
  dudosos_.anotar(c);
  guardarCobrosDudosos();
}

CobrosDudosos::Resultado Almacen::resolverCobroDudoso(uint32_t idTarjeta, uint16_t operacion,
                                                     uint16_t validadorTarjeta, CobroDudoso &c) {
  const CobrosDudosos::Resultado r = dudosos_.resolver(idTarjeta, operacion, validadorTarjeta, validador_, c);
  if (r != CobrosDudosos::Resultado::NINGUNO) guardarCobrosDudosos();
  return r;
}

void Almacen::avisarCola(const char *texto) { Serial.printf("AVISO cola de eventos: %s\n", texto); }

// El evento se da por guardado solo cuando ya está vaciado a la microSD (ColaEventos::agregar)
bool Almacen::guardarEvento(sp::Evento &e) {
  if (!cola_.agregar(e)) return false;
  if (alGuardar_) alGuardar_(e);
  return true;
}

void Almacen::guardarUltimasOperaciones() {
  String t;
  for (auto &kv : ultimaOp_) t += String(kv.first) + " " + String(kv.second) + "\n";
  escribirArchivo("/ultima_op.txt", t);
}

bool Almacen::registrarViaje(uint32_t fecha, uint32_t idTarjeta, const String &uid, int32_t monto,
                             int32_t saldoFinal, uint16_t operacion, uint32_t contador,
                             uint32_t recargaHasta) {
  sp::Evento e;
  memset(&e, 0, sizeof(e));
  e.tipo = sp::VIAJE;
  e.fecha = fecha;
  e.idTarjeta = idTarjeta;
  eventos_texto::uidDeHex(uid.c_str(), e);
  e.monto = monto;
  e.saldoFinal = saldoFinal;
  e.operacion = operacion;
  e.contador = contador;
  e.recargaHasta = recargaHasta;
  if (!guardarEvento(e)) return false;
  if (operacion > ultimaOperacion(idTarjeta)) {
    ultimaOp_[idTarjeta] = operacion;
    guardarUltimasOperaciones();
  }
  return true;
}

bool Almacen::registrarIncidencia(uint32_t fecha, uint32_t idTarjeta, const String &uid,
                                  const String &detalle, bool fraude) {
  sp::Evento e;
  memset(&e, 0, sizeof(e));
  e.tipo = fraude ? sp::FRAUDE : sp::INCIDENCIA;
  e.fecha = fecha;
  e.idTarjeta = idTarjeta;
  eventos_texto::uidDeHex(uid.c_str(), e);
  e.contador = sp::SIN_CONTADOR;
  eventos_texto::ponerDetalle(e, detalle.c_str());  // C1: 20 caracteres ASCII
  return guardarEvento(e);
}

// Los eventos sin confirmar, como el JSON de siempre (sincronizador v1)
String Almacen::eventosPendientes(size_t max, std::vector<uint32_t> &ids) {
  ids.clear();
  String json = "[";
  sp::Evento lote[8];
  uint32_t desde = cola_.ultimoAcusado();
  while (ids.size() < max) {
    const size_t pedir = max - ids.size() < 8 ? max - ids.size() : 8;
    const size_t n = cola_.leerDesde(desde, lote, pedir);
    if (n == 0) break;
    for (size_t i = 0; i < n; i++) {
      char linea[512];
      if (eventos_texto::aJson(lote[i], linea, sizeof(linea)) == 0) continue;
      if (ids.size()) json += ",";
      json += linea;
      ids.push_back(lote[i].idEvento);
    }
    desde = lote[n - 1].idEvento;
  }
  return json + "]";
}

// Solo guarda el acuse (una escritura del estado). Compactar la cola cuesta ~1,5 s en la microSD: lo hace
// mantenimiento() cuando el lector está libre, no aquí
void Almacen::marcarSubidos(uint32_t hastaId) {
  fallosCompactar_ = 0;
  cola_.acusarHasta(hastaId, false);
}

// Desde loop(), solo con el lector libre (sin tarjeta delante): un paso (una operación en la microSD, ~0,8 s como
// mucho) de la compactación pendiente de la cola. true si hizo algo.
bool Almacen::mantenimiento() {
  if (!listo_ || !cola_.compactacionPendiente() || fallosCompactar_ >= 3) return false;
  if (!cola_.compactarPaso()) {
    fallosCompactar_++;
    Serial.println("AVISO: no se pudo compactar la cola de eventos");
  }
  return true;
}

// Un validador que ya tenía eventos de una versión anterior los conserva. Primero (antes de abrir la cola) el
// estado: los ids siguen desde ultimo_subido + 1, que es lo que el servidor espera.
void Almacen::prepararMigracion() {
  uint32_t subido = 0;
  porLinea(leerArchivo("/estado.txt"), [&](const String &l) {
    if (l.startsWith("ultimo_subido=")) subido = l.substring(14).toInt();
  });
  if (subido > estado_.estado().hastaAcusado) estado_.fijarHastaAcusado(subido);
  if (subido + 1 > estado_.estado().siguienteId) estado_.fijarSiguienteId(subido + 1);
}

// Luego los eventos pendientes (los ya subidos se dejan), uno a uno a la cola. Un corte a mitad se retoma sin duplicar:
// lo que ya está en la cola se salta. Al terminar el texto pasa a *.migrado (se conserva por si algo salió mal).
void Almacen::migrarEventosDeTexto() {
  uint32_t migrados = 0, saltados = 0, malos = 0;
  bool completo = true;
  File f = SD.open("/eventos.txt", FILE_READ);
  if (f) {
    const uint32_t subido = estado_.estado().hastaAcusado;
    while (f.available()) {
      String l = f.readStringUntil('\n');
      l.trim();
      if (!l.startsWith("{\"id\":")) continue;
      const uint32_t id = l.substring(6).toInt();
      if (id <= subido || id <= cola_.ultimoId()) {
        saltados++;
        continue;
      }
      sp::Evento e;
      if (!eventos_texto::deJson(l.c_str(), e)) {  // una línea cortada por un corte de energía
        malos++;
        continue;
      }
      esp_task_wdt_reset();  // con muchos pendientes tarda: cada agregar() vacía a la microSD
      if (!cola_.agregar(e)) {
        completo = false;
        break;
      }
      if (e.idEvento != id) Serial.printf("AVISO migración: el evento %lu quedó con el id %lu\n", (unsigned long)id, (unsigned long)e.idEvento);
      migrados++;
    }
    f.close();
  }
  Serial.printf("microSD: eventos.txt -> eventos.bin: %lu migrados, %lu ya subidos o repetidos, %lu líneas dañadas%s\n",
                (unsigned long)migrados, (unsigned long)saltados, (unsigned long)malos, completo ? "" : " (INCOMPLETO: se reintenta al arrancar)");
  if (!completo) return;
  for (const char *r : {"/eventos.txt", "/estado.txt"}) {
    if (!SD.exists(r)) continue;
    String destino = String(r).substring(0, String(r).length() - 4) + ".migrado";
    if (SD.exists(destino.c_str())) SD.remove(destino.c_str());
    SD.rename(r, destino.c_str());
  }
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
