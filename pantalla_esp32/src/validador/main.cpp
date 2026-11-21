/*
 * Validador de bus en la ESP32.
 *
 * Toda la lógica corre aquí: autentica la tarjeta, verifica las firmas, comprueba
 * la lista negra, descuenta la tarifa, graba el nuevo saldo, guarda el viaje en
 * la microSD y muestra el resultado en la pantalla. Es la misma lógica que
 * transporte/validador.py.
 *
 * El lector es un PN532 conectado a la ESP32 (LectorPN532): no necesita el
 * ordenador. La hora la da el servidor en cada sincronización por WiFi.
 *
 * El ordenador solo hace falta para configurar el validador. Con puente_nfc.py
 * abierto, la ESP32 recibe la configuración y la hora por el cable USB
 * (LectorPuente, que ya no se usa como lector).
 *
 * Cargar:  pio run -e validador -t upload
 * Primera vez (configura número de validador y clave maestra en la microSD):
 *          python3 puente_nfc.py --configurar --validador 103
 * Con sincronización por WiFi (sincronizador.cpp), además:
 *          python3 puente_nfc.py --configurar --validador 103 --wifi <red de 2,4 GHz>
 * Redes WiFi desde el celular (portal.cpp): mantener pulsado BOOT 3 s.
 */

#include <Arduino.h>
#include <Formato.h>
#include <LectorPN532.h>
#include <LectorPuente.h>
#include <EnlaceWifi.h>
#include <PantallaUI.h>
#include <esp_system.h>
#include <esp_task_wdt.h>
#include <rom/ets_sys.h>
#include <string.h>

#include "almacen.h"
#include "portal.h"
#include "raices_tls.h"
#include "sincronizador.h"

// Último paso en que entró el programa. Si el watchdog salta, dice dónde se quedó atascado
// (la traza de la pila que imprime el sistema no sirve: es la del propio watchdog).
static const char *volatile pasoActual = "arranque";
#define PASO(texto) (pasoActual = (texto))
extern "C" void esp_task_wdt_isr_user_handler(void) {
  ets_printf("\n#WATCHDOG: el programa se bloqueó en el paso: %s\n", pasoActual);
}

namespace {

constexpr uint32_t MARGEN_DOBLE_TOQUE = 60;            // s: otro toque aquí no se cobra
constexpr uint8_t PIN_BOOT = 0;                        // botón BOOT de la placa
// Si el bucle principal no avisa en este tiempo, la placa se reinicia sola e imprime
// dónde estaba atascada. Lo más largo que hace el validador sin dar una vuelta es una
// sincronización HTTPS (conexión 5 s + TLS hasta 10 s + respuesta 8 s, unos 25 s en el
// peor caso), así que 45 s deja margen sin ocultar un bloqueo de minutos.
constexpr uint32_t WATCHDOG_S = 45;
constexpr uint32_t PULSACION_PORTAL = 3000;            // ms pulsado para abrir el portal WiFi

// PN532 por I2C: SDA = P21, SCL = P22, IRQ = P32
LectorPN532 lector(Wire, 21, 22, 32);
LectorPuente puente(Serial);  // ordenador opcional: configuración y hora
Almacen almacen;

uint32_t baseUnix = 0, baseMillis = 0;
uint32_t ahoraUnix() { return baseUnix ? baseUnix + (millis() - baseMillis) / 1000 : 0; }

void log(const String &texto) {
  Serial.print('#');
  Serial.println(texto);
}

const std::vector<RedWifi> &redesGuardadas() { return almacen.redes(); }
EnlaceWifi enlaceWifi(redesGuardadas, log, RAICES_TLS);
Sincronizador sincronizador(almacen, log, enlaceWifi);
PortalWifi portal(almacen, log);
String estadoMostrado;      // estado del WiFi que se ve en la pantalla de espera
uint32_t botonDesde = 0, ultimoSaludo = 0, ultimoIntentoLector = 0;

#ifdef M15_MEDIR_LOOP
struct MedirLoop {
  uint32_t inicio = micros();
  ~MedirLoop() {
    static uint32_t maximoUs = 0, ultimoReporte = 0;
    const uint32_t duracion = micros() - inicio;
    if (duracion > maximoUs) maximoUs = duracion;
    if (millis() - ultimoReporte >= 10000) {
      Serial.println("# M15 loop_max_us=" + String(maximoUs));
      maximoUs = 0;
      ultimoReporte = millis();
    }
  }
};
#endif

// true cuando BOOT lleva PULSACION_PORTAL ms pulsado
bool botonMantenido() {
  if (digitalRead(PIN_BOOT) != LOW) {
    botonDesde = 0;
    return false;
  }
  if (!botonDesde) botonDesde = millis();
  return millis() - botonDesde >= PULSACION_PORTAL;
}

String soles(int32_t c) {
  String s = c < 0 ? "-" : "";
  c = abs(c);
  char dec[3];
  snprintf(dec, sizeof(dec), "%02ld", (long)(c % 100));
  return s + "S/ " + String(c / 100) + "." + dec;
}

// El detalle de un evento son 20 caracteres ASCII como mucho (C1 5.3)
const char *detalleAlterada(tt::Error err) {
  switch (err) {
    case tt::Error::SIN_MARCA: return "alterada: sin marca";
    case tt::Error::VERSION_NO_SOPORTADA: return "alterada: version";
    case tt::Error::FIRMA_CABECERA: return "alterada: firma";
    case tt::Error::SIN_REGISTRO_VALIDO: return "alterada: sin saldo";
    default: return "tarjeta alterada";
  }
}

String aHex(const uint8_t *d, size_t n) {
  String s;
  char b[3];
  for (size_t i = 0; i < n; i++) {
    snprintf(b, sizeof(b), "%02X", d[i]);
    s += b;
  }
  return s;
}

struct Resultado {
  char tipo;  // 'P' pasa, 'R' rechazo, 'A' aviso (reintentar)
  String l1, l2, l3;
};

Resultado pasa(const String &a, const String &b, const String &c = "") { return {'P', a, b, c}; }
Resultado rechazo(const String &a, const String &b) { return {'R', a, b, ""}; }
Resultado aviso(const String &a, const String &b) { return {'A', a, b, ""}; }

// Escribe el estado "bloqueada" en la propia tarjeta: así la rechazan también los
// validadores que aún no tienen la lista negra actualizada.
void grabarBloqueo(tt::Cabecera cab, const uint8_t *uid, size_t lenUid, const uint8_t clave[32]) {
  uint8_t buf[tt::TAM_CABECERA];
  cab.estado = tt::BLOQUEADA;
  tt::codificarCabecera(cab, uid, lenUid, clave, buf);
  lector.escribir(tt::PAG_CABECERA, buf, tt::TAM_CABECERA / 4);
}

Resultado cobrar(const uint8_t *uid, size_t lenUid) {
  const String uidHex = aHex(uid, lenUid);
  const uint32_t ahora = ahoraUnix();
  if (!almacen.listo()) return aviso("Error microSD", "No se puede cobrar");
  if (!ahora) return aviso("Sin hora", "Conecte el WiFi");

  uint8_t pwd[4], pack[2], packTarjeta[2];
  tt::contrasena(almacen.claveMaestra(), uid, lenUid, pwd, pack);
  if (!lector.autenticar(pwd, packTarjeta)) return rechazo("Tarjeta no valida", "No emitida por el sistema");
  if (memcmp(pack, packTarjeta, 2) != 0) return rechazo("Tarjeta no valida", "No reconocida");

  uint8_t clave[32], area[tt::TAM_AREA];
  tt::claveFirma(almacen.claveMaestra(), uid, lenUid, clave);
  if (!lector.leer(tt::PAG_INICIO, tt::PAG_FIN, area)) return aviso("Acerque de nuevo", "Lectura fallida");
  tt::EstadoTarjeta e;
  tt::Error err = tt::decodificar(area, uid, lenUid, clave, e);
  if (err != tt::Error::OK) {
    almacen.registrarIncidencia(ahora, 0, uidHex, detalleAlterada(err), true);
    return rechazo("Tarjeta no valida", "Datos alterados");
  }
  uint32_t contador = 0;
  if (!lector.contador(contador)) return aviso("Acerque de nuevo", "Lectura fallida");
  const tt::Cabecera &cab = e.cab;
  const tt::Registro &reg = e.registro();

  if (cab.estado == tt::BLOQUEADA) return rechazo("Tarjeta bloqueada", "Acuda a atencion");
  if (almacen.bloqueada(cab.idTarjeta)) {
    grabarBloqueo(cab, uid, lenUid, clave);
    almacen.registrarIncidencia(ahora, cab.idTarjeta, uidHex,
                                "tarjeta bloqueada", false);
    return rechazo("Tarjeta bloqueada", "Acuda a atencion");
  }
  if (reg.operacion < almacen.ultimaOperacion(cab.idTarjeta)) {
    almacen.registrarIncidencia(ahora, cab.idTarjeta, uidHex, "operacion retrocede", true);  // saldo restaurado de una copia
    return rechazo("Tarjeta no valida", "Acuda a atencion");
  }

  Tarifa t;
  if (!almacen.tarifa(cab.tarifa, t)) return rechazo("Tarifa desconocida", "Codigo " + String(cab.tarifa));
  String nombre = t.nombre;
  if (nombre.length()) nombre.setCharAt(0, toupper(nombre[0]));

  if (reg.validador == almacen.validador() && ahora >= reg.fecha && ahora - reg.fecha < MARGEN_DOBLE_TOQUE) {
    // Ya cobrado aquí hace un momento. Si la verificación de aquel cobro falló
    // (tarjeta retirada justo al final), el viaje se registra ahora.
    if (!almacen.existeViaje(cab.idTarjeta, reg.operacion)) {
      almacen.registrarViaje(ahora, cab.idTarjeta, uidHex, t.precio, reg.saldo, reg.operacion, contador,
                             reg.recarga);
    }
    return pasa("Ya pagado", "Saldo " + soles(reg.saldo));
  }

  // Recargas remotas (Yape, web...) que aún no llegaron a esta tarjeta
  int32_t abono = 0;
  uint32_t hasta = reg.recarga;
  almacen.recargasPendientes(cab.idCuenta, reg.recarga, abono, hasta);
  const int32_t disponible = reg.saldo + abono;
  if (disponible < t.precio) return rechazo("Saldo insuficiente", "Saldo " + soles(disponible));

  tt::Registro nuevo;
  nuevo.saldo = disponible - t.precio;
  nuevo.operacion = reg.operacion + 1;
  nuevo.fecha = ahora;
  nuevo.validador = almacen.validador();
  nuevo.recarga = hasta;
  uint8_t datos[tt::TAM_REGISTRO], comprobacion[tt::TAM_REGISTRO];
  tt::codificarRegistro(nuevo, cab.idTarjeta, uid, lenUid, clave, datos);
  const uint8_t pagina = tt::PAG_REGISTRO[e.ranuraLibre()];
  if (!lector.escribir(pagina, datos, tt::TAM_REGISTRO / 4) ||
      !lector.leer(pagina, pagina + tt::TAM_REGISTRO / 4 - 1, comprobacion) ||
      memcmp(datos, comprobacion, tt::TAM_REGISTRO) != 0) {
    // El registro anterior sigue intacto: no se cobró
    return aviso("Acerque de nuevo", "Lectura interrumpida");
  }

  if (!almacen.registrarViaje(ahora, cab.idTarjeta, uidHex, t.precio, nuevo.saldo, nuevo.operacion, contador,
                              abono ? hasta : 0)) {
    log("ERROR: el viaje se cobró pero no se pudo guardar en la microSD");
  }
  return pasa(nombre + " " + soles(t.precio), "Saldo " + soles(nuevo.saldo),
              abono ? "Recarga +" + soles(abono) : "");
}

void pantallaEspera() {
  const bool wifiLista = enlaceWifi.estado() == EstadoEnlace::Listo;
  ui::estadoCanal(wifiLista ? "W" : "-", enlaceWifi.senal(),
                  static_cast<uint16_t>(min(almacen.pendientes(), static_cast<uint32_t>(65535))));
  if (!almacen.listo()) {
    ui::espera("Error microSD", "Revise la tarjeta SD");
  } else if (!lector.conectado()) {
    ui::espera("Sin lector", "Revise el PN532");
  } else if (!almacen.configurado()) {
    ui::espera("Sin configurar", "puente --configurar");
  } else if (!ahoraUnix()) {
    ui::espera("Bus " + String(almacen.validador()) + "  Sin hora", "Esperando el WiFi");
  } else {
    const String &wifi = sincronizador.estado();
    ui::espera("Bus " + String(almacen.validador()) + (wifi.length() ? "  " + wifi : ""),
               "Sin subir: " + String(almacen.pendientes()));
  }
}

void guardarConfig(const String &config) {
  log(almacen.guardarConfig(config) ? "Configuración guardada en la microSD"
                                    : "ERROR: no se pudo guardar la configuración");
}

// El ordenador es opcional: con puente_nfc.py abierto envía la configuración
// (--configurar) y la hora. Se le saluda mientras falte alguna de las dos o
// mientras conteste; cada saludo sin respuesta espera 1,5 s.
void atenderPuente() {
  const bool falta = !almacen.configurado() || !ahoraUnix();
  if (!falta && !puente.conectado()) return;
  if (millis() - ultimoSaludo < (puente.conectado() ? 3000 : 10000)) return;
  ultimoSaludo = millis();
  const bool estaba = puente.conectado();
  String config;
  if (!puente.saludar(config)) {
    if (estaba) log("Ordenador desconectado");
    return;
  }
  if (config.length()) guardarConfig(config);
  if (!sincronizador.horaServidor()) {  // la del servidor tiene preferencia
    const uint32_t h = puente.horaUnix();
    if (h) {
      baseUnix = h;
      baseMillis = millis();
    }
  }
  if (!estaba) log("Ordenador conectado. Validador " + String(almacen.validador()) + ", eventos sin subir: " +
                   String(almacen.pendientes()));
  pantallaEspera();
}

void iniciarLector() {
  if (lector.iniciar()) {
    log("Lector " + lector.version() + " listo");
  } else {
    log("ERROR: el PN532 no responde. Revise los cables y el interruptor (I2C: 1 en ON, 2 en OFF)");
  }
  ultimoIntentoLector = millis();
}

void comprobarModoSync() {
  static String modoAvisado;
  const String pedido = almacen.config("sync");
  if (pedido == modoAvisado) return;
  modoAvisado = pedido;
  if (pedido == "v2") log("sync=v2 aún no disponible: falta SincronizadorV2 y los enlaces; se mantiene v1");
  else if (pedido.length() && pedido != "v1") log("sync desconocido; se mantiene v1");
}

// Por qué arrancó la placa; un reinicio del watchdog o por caída de tensión se ve aquí
void registrarMotivoReinicio() {
  const char *motivo = "desconocido";
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: motivo = "encendido"; break;
    case ESP_RST_SW: motivo = "reinicio por programa"; break;
    case ESP_RST_PANIC: motivo = "error del programa (panic)"; break;
    case ESP_RST_INT_WDT: motivo = "WATCHDOG de interrupciones"; break;
    case ESP_RST_TASK_WDT: motivo = "WATCHDOG: el programa se bloqueó"; break;
    case ESP_RST_WDT: motivo = "WATCHDOG de hardware"; break;
    case ESP_RST_BROWNOUT: motivo = "CAÍDA DE TENSIÓN (brownout)"; break;
    case ESP_RST_DEEPSLEEP: motivo = "salida de sueño profundo"; break;
    case ESP_RST_EXT: motivo = "pin EN (botón o puerto serie)"; break;
    default: break;
  }
  log(String("Arranque: ") + motivo);
}

void activarWatchdog() {
  // El framework ya lo trae encendido con 5 s y solo vigila la tarea ociosa de la CPU 0;
  // se reconfigura y se suscribe el bucle principal (esta tarea) para vigilarlo a él
  if (esp_task_wdt_init(WATCHDOG_S, true) != ESP_OK) log("AVISO: no se pudo configurar el watchdog");
  if (esp_task_wdt_add(nullptr) != ESP_OK) log("AVISO: no se pudo suscribir el bucle al watchdog");
}

}  // namespace

void setup() {
  Serial.begin(115200);
  activarWatchdog();  // desde el principio: también vigila un cuelgue durante setup()
  registrarMotivoReinicio();
  PASO("setup: ui::iniciar");
  ui::iniciar();
  PASO("setup: pantalla Iniciando");
  ui::espera("Validador", "Iniciando...");
  pinMode(PIN_BOOT, INPUT_PULLUP);
  PASO("setup: microSD");
  almacen.iniciar();
  PASO("setup: lector PN532");
  iniciarLector();
  PASO("setup: modo sync");
  comprobarModoSync();
  PASO("setup: pantalla de espera");
  pantallaEspera();
  PASO("setup: portal");
  // Sin ninguna red guardada, el portal se abre solo (se cierra a los 10 min sin uso)
  if (almacen.listo() && almacen.configurado() && almacen.redes().empty()) portal.iniciar();
}

void loop() {
  esp_task_wdt_reset();  // el bucle sigue vivo
#ifdef WATCHDOG_PRUEBA
  // Solo para comprobar el watchdog (-DWATCHDOG_PRUEBA): a los 60 s se bloquea a propósito
  if (millis() > 60000) {
    log("PRUEBA: bloqueo a propósito, el watchdog debe reiniciar la placa");
    while (true) delay(1000);
  }
#endif
#ifdef M15_MEDIR_LOOP
  MedirLoop medirLoop;
#endif
  PASO("loop: portal");
  comprobarModoSync();
  if (portal.activo()) {
    // Mientras se configura el WiFi no se cobra
    if (portal.atender()) {
      botonDesde = 0;
      sincronizador.reiniciarWifi();
      pantallaEspera();
    } else if (puente.conectado() && millis() - ultimoSaludo > 3000) {
      // El puente reinicia la placa si deja de oírla: se le saluda de vez en cuando
      String config;
      puente.saludar(config);
      ultimoSaludo = millis();
    }
    delay(2);
    return;
  }
  PASO("loop: botón BOOT");
  if (botonMantenido() && almacen.listo() && almacen.configurado()) {
    portal.iniciar();
    return;
  }

  PASO("loop: ui::actualizar");
  ui::actualizar();
  PASO("loop: sincronizador");
  if (sincronizador.atender()) {
    if (sincronizador.horaServidor()) {  // la hora del servidor permite cobrar sin el puente
      baseUnix = sincronizador.horaServidor();
      baseMillis = sincronizador.millisHora();
    }
    PASO("loop: pantalla tras sincronizar");
    pantallaEspera();  // actualiza "Sin subir"
  }
  if (sincronizador.estado() != estadoMostrado) {
    estadoMostrado = sincronizador.estado();
    PASO("loop: pantalla por cambio de estado");
    pantallaEspera();
  }

  PASO("loop: reintento de la microSD");
  if (almacen.reintentarMontaje()) {
    log("microSD montada tras reintentar");
    pantallaEspera();
    if (almacen.configurado() && almacen.redes().empty()) portal.iniciar();  // como en setup()
  }

  PASO("loop: consumo de datos");
  if (almacen.listo()) {
    ConsumoDatos &consumo = almacen.consumo();
    const uint32_t hora = ahoraUnix();
    consumo.fijarFecha(hora ? ConsumoDatos::fechaDeUnix(hora) : 0);
    consumo.observar(CanalDatos::WIFI, enlaceWifi.bytesTx(), enlaceWifi.bytesRx());
    if (consumo.atender(millis())) {  // cada 5 minutos, si hubo tráfico
      uint32_t tx, rx;
      consumo.bytesHoy(tx, rx);
      log("Consumo guardado: hoy tx " + String(tx) + " B, rx " + String(rx) + " B (el enlace WiFi lleva " +
          String(enlaceWifi.bytesTx()) + "/" + String(enlaceWifi.bytesRx()) + " desde el arranque)");
    }
  }

  PASO("loop: puente (ordenador)");
  atenderPuente();

  if (!lector.conectado()) {
    if (millis() - ultimoIntentoLector > 3000) {
      PASO("loop: reintento del lector PN532");
      iniciarLector();
      PASO("loop: pantalla tras reintentar el lector");
      pantallaEspera();
    }
    delay(10);
    return;
  }
  if (!almacen.listo() || !almacen.configurado()) {
    delay(10);
    return;
  }

  uint8_t uid[10];
  size_t lenUid = 0;
  PASO("loop: esperar tarjeta");
  if (!lector.esperarTarjeta(300, uid, lenUid)) {
    if (!lector.conectado()) {
      log("ERROR: el PN532 dejó de responder");
      pantallaEspera();
    }
    PASO("loop: mantenimiento de la microSD");
    almacen.mantenimiento();  // sin tarjeta delante: un paso de lo que haya pendiente (compactar la cola)
    return;
  }

  uint32_t inicio = millis();
  PASO("loop: cobrar");
  Resultado r = cobrar(uid, lenUid);
  lector.terminar();
  ui::resultado(r.tipo, r.l1, r.l2, r.l3);
  const char *etiqueta = r.tipo == 'P' ? "PASA" : r.tipo == 'R' ? "RECHAZADA" : "REINTENTE";
  log(String(etiqueta) + " " + aHex(uid, lenUid) + " | " + r.l1 + " | " + r.l2 +
      (r.l3.length() ? " | " + r.l3 : "") + " (" + String(millis() - inicio) + " ms)");
  pantallaEspera();

  // Con la tarjeta puesta no se vuelve al bucle: se avisa al watchdog aquí
  PASO("loop: esperando que retiren la tarjeta");
  while (lector.conectado() && !lector.retirada(300)) {
    esp_task_wdt_reset();
    ui::actualizar();
  }
}
