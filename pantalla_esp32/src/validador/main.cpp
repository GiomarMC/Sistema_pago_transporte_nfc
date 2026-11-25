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
#include <Preferences.h>
#include <WiFi.h>
#include <Formato.h>
#include <LectorPN532.h>
#include <LectorPuente.h>
#include <EnlaceWifi.h>
#include <PantallaUI.h>
#include <esp_system.h>
#include <esp_task_wdt.h>
#include <rom/ets_sys.h>
#include <string.h>

#include <BusI2C.h>
#include <CanalMqtt.h>
#include <TareaFondo.h>

#include <memory>

#include "almacen.h"
#include "almacen_sync_binario.h"
#include "clave_servidor.h"
#include "correccion_hora.h"
#include "gestor_enlaces.h"
#include "intercambio_vecinos.h"
#include "portal.h"
#include "radio_espnow.h"
#include "raices_tls.h"
#include "secretos.h"
#include "sincronizador.h"
#include "sincronizador_v2.h"

// Último paso en que entró el programa. Si el watchdog salta, dice dónde se quedó atascado
// (la traza de la pila que imprime el sistema no sirve: es la del propio watchdog).
static const char *volatile pasoActual = "arranque";
#ifdef M15_MEDIR_LOOP
// Al medir el loop también se anota qué paso fue el más largo (el que va desde que se marca hasta el siguiente PASO)
static uint32_t pasoDesdeUs = 0, pasoMaxUs = 0;
static const char *pasoMaxNombre = "";
static void marcarPaso(const char *texto) {
  const uint32_t ahora = micros();
  if (pasoDesdeUs && ahora - pasoDesdeUs > pasoMaxUs) {
    pasoMaxUs = ahora - pasoDesdeUs;
    pasoMaxNombre = pasoActual;
  }
  pasoDesdeUs = ahora;
  pasoActual = texto;
}
#define PASO(texto) marcarPaso(texto)
#else
#define PASO(texto) (pasoActual = (texto))
#endif
extern "C" void esp_task_wdt_isr_user_handler(void) {
  ets_printf("\n#WATCHDOG: el programa se bloqueó en el paso: %s\n", pasoActual);
}

namespace {

// s: otro toque aquí no se cobra. Solo para pruebas de banco (M8-04: 30 viajes con una sola tarjeta) se puede bajar al
// compilar: PLATFORMIO_BUILD_FLAGS="-DMARGEN_DOBLE_TOQUE_PRUEBA=3". Nunca en un validador en servicio
#ifdef MARGEN_DOBLE_TOQUE_PRUEBA
constexpr uint32_t MARGEN_DOBLE_TOQUE = MARGEN_DOBLE_TOQUE_PRUEBA;
#else
constexpr uint32_t MARGEN_DOBLE_TOQUE = 60;
#endif
constexpr uint8_t PIN_BOOT = 0;                        // botón BOOT de la placa
// Si el bucle principal no avisa en este tiempo, la placa se reinicia sola e imprime
// dónde estaba atascada. Lo más largo que hace el validador sin dar una vuelta es una
// sincronización HTTPS (conexión 5 s + TLS hasta 10 s + respuesta 8 s, unos 25 s en el
// peor caso), así que 45 s deja margen sin ocultar un bloqueo de minutos.
constexpr uint32_t WATCHDOG_S = 45;
constexpr uint32_t PULSACION_PORTAL = 3000;            // ms pulsado para abrir el portal WiFi

// PN532 por I2C: SDA = P21, SCL = P22, IRQ = P32. La OLED, si la hay, va en su propio bus (SDA P25, SCL P33; #200)
constexpr uint8_t PIN_SDA = 21, PIN_SCL = 22;
LectorPN532 lector(Wire1, PIN_SDA, PIN_SCL, 32);  // Wire1: Wire es de la OLED (U8g2 solo sabe usar Wire)
LectorPuente puente(Serial);  // ordenador opcional: configuración y hora
Almacen almacen;

uint32_t baseUnix = 0, baseMillis = 0;
bool horaReal = false;  // true si la hora viene del servidor o del ordenador; false si es la estimada del arranque
uint32_t ahoraUnix() { return baseUnix ? baseUnix + (millis() - baseMillis) / 1000 : 0; }

// La placa no tiene reloj con batería: al apagarla pierde la hora. Para poder cobrar sin WiFi se guarda en la NVS
// la última hora conocida (como mucho cada 30 min, para no gastar la flash) y al arrancar se parte de ella. Queda
// por detrás de la real lo que estuvo apagada, así que es una hora ESTIMADA hasta que llegue la del servidor; los
// viajes que se cobren en ese tiempo llevan esa fecha. Un validador nuevo, que nunca ha tenido la hora, sí necesita
// conectarse una vez.
constexpr uint32_t PERIODO_GUARDAR_HORA_MS = 30UL * 60 * 1000;
uint32_t horaGuardada() {
  Preferences p;
  if (!p.begin("subepe", true)) return 0;
  const uint32_t h = p.getUInt("t_unix", 0);
  p.end();
  return h;
}
void guardarHora(uint32_t unix) {
  static uint32_t ultimoMs = 0;
  if (ultimoMs && millis() - ultimoMs < PERIODO_GUARDAR_HORA_MS) return;
  Preferences p;
  if (p.begin("subepe", false)) {
    p.putUInt("t_unix", unix);
    p.end();
    ultimoMs = millis();
  }
}
// Los eventos guardados con la hora estimada no salen de la placa hasta que llega la real, y salen con la fecha
// corregida (ver correccion_hora.h). Solo en RAM: si la placa se apaga antes, salen con la estimada.
CorreccionHora correccionHora;
void log(const String &texto);
void anotarEvento(const sp::Evento &e) {
  if (!horaReal) correccionHora.anotar(e.idEvento, baseUnix, baseMillis);
}

// Hora real (del servidor o del ordenador): `unix` era la hora en el instante `ms` (millis())
void fijarHora(uint32_t unix, uint32_t ms) {
  if (!correccionHora.hayHoraReal()) {
    correccionHora.horaReal(unix, ms);
    if (correccionHora.anotados())
      log("Hora real: " + String(correccionHora.anotados()) + " eventos cobrados con la hora estimada salen corregidos (" +
          String(correccionHora.delta()) + " s)");
  }
  baseUnix = unix;
  baseMillis = ms;
  horaReal = true;
  guardarHora(ahoraUnix());
}

void log(const String &texto) {
  Serial.print('#');
  Serial.println(texto);
}

const std::vector<RedWifi> &redesGuardadas() { return almacen.redes(); }
EnlaceWifi enlaceWifi(redesGuardadas, log, RAICES_TLS);
Sincronizador sincronizador(almacen, log, enlaceWifi);
Secretos secretos;
bool tieneClaveValidador() { return secretos.tieneClaveHmac(); }
PortalWifi portal(almacen, log, tieneClaveValidador);

// --- Modo sync=v2 (M15): SincronizadorV2 + GestorEnlaces sobre CanalMqtt. Solo si config.txt lo pide Y están las
// claves en la NVS; si no, sigue el v1. Los objetos son grandes (SincronizadorV2 lleva ~9 KB de búferes): van en
// el montón, nunca en la pila de loop().
constexpr uint32_t VERSION_FIRMWARE = 20261121;  // AAAAMMDD, para el HOLA
struct SyncV2 {
  uint8_t claveHmac[Secretos::TAM_CLAVE_HMAC];
  std::unique_ptr<VecinosEspNow> vecinos;       // solo con espnow=on en config.txt
  std::unique_ptr<RadioEspNow> radio;
  std::unique_ptr<IntercambioVecinos> intercambio;
  std::unique_ptr<CanalMqtt> canal;
  std::unique_ptr<GestorEnlaces> gestor;
  std::unique_ptr<AlmacenSyncBinario> almacenSync;
  std::unique_ptr<SincronizadorV2> sinc;
  String estado;  // lo que se ve en la pantalla de espera
  uint32_t horaVista = 0;
};
std::unique_ptr<SyncV2> v2;
TareaFondo tareaMqtt("mqtt");
constexpr uint32_t BUSCAR_TARJETA_MS = 0;  // esperarTarjeta(): con 0, un solo intento  // conecta MQTT/TLS sin parar loop() (#129)
uint8_t canalEspNowConfigurado = 1;  // espnow_canal: el de ESP-NOW sin WiFi
void alCanalDelWifi(uint8_t canal);  // más abajo, con el gestor ya creado
bool puedeEscanearWifi();

// subida=vecinos en config.txt (prueba de M8): la placa se conecta al WiFi como siempre, y así ESP-NOW queda en el
// canal del AP, pero no abre MQTT. Al gestor se le da este canal, que nunca está listo, y los eventos solo salen por
// un vecino de ESP-NOW. Sirve para probar el intercambio entre dos placas que tienen WiFi las dos.
class CanalSinServidor : public CanalPaquetes {
 public:
  const char *nombre() const override { return "ninguno"; }
  uint8_t codigoEnlace() const override { return 0; }
  bool listo() const override { return false; }
  void atender() override {}
  bool enviar(const uint8_t *, size_t) override { return false; }
  size_t recibir(uint8_t *, size_t) override { return 0; }
};
CanalSinServidor canalSinServidor;
String estadoMostrado;      // estado del WiFi que se ve en la pantalla de espera
String avisoClaves;         // "Falta clave validador"... en la pantalla de espera (ver revisarClaves())
uint32_t botonDesde = 0, ultimoSaludo = 0, ultimoIntentoLector = 0;

#ifdef M15_MEDIR_LOOP
struct MedirLoop {
  uint32_t inicio = micros();
  ~MedirLoop() {
    static uint32_t maximoUs = 0, ultimoReporte = 0;
    const uint32_t duracion = micros() - inicio;
    if (duracion > maximoUs) maximoUs = duracion;
    if (millis() - ultimoReporte >= 10000) {
      Serial.println("# M15 loop_max_us=" + String(maximoUs) + " paso_max_us=" + String(pasoMaxUs) + " (" + pasoMaxNombre + ")");
      maximoUs = 0;
      pasoMaxUs = 0;
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

// #246: la última tarjeta atendida y cuándo se retiró, para no volver a leerla al salir del campo
constexpr uint32_t IGNORAR_MISMA_TARJETA_MS = 2000;
struct {
  uint8_t uid[10];
  size_t len = 0;
  uint32_t retiradaMs = 0;
  bool ignorar = false;
} ultimaTarjeta;

// Con la tarjeta puesta no se vuelve al bucle: se avisa al watchdog aquí
void esperarRetirada() {
  PASO("loop: esperando que retiren la tarjeta");
  while (lector.conectado() && !lector.retirada(300)) {
    esp_task_wdt_reset();
    ui::actualizar();
  }
  ultimaTarjeta.retiradaMs = millis();
}

Resultado cobrar(const uint8_t *uid, size_t lenUid) {
  const String uidHex = aHex(uid, lenUid);
  uint32_t ahora = ahoraUnix();
  if (!almacen.listo()) return aviso("Error microSD", "No se puede cobrar");
  if (!ahora) return aviso("Sin hora", "Conecte el WiFi una vez");

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

  // Con la hora estimada del arranque (sin WiFi aún) la placa puede ir por detrás de la tarjeta, que lleva la fecha
  // del último cobro, firmada: el reloj no retrocede respecto a ella, o un doble toque tras un reinicio cobraría dos veces
  if (!horaReal && reg.fecha > ahora) {
    baseUnix += reg.fecha - ahora;
    ahora = reg.fecha;
  }
  // Un cobro de este validador que se escribió sin poder comprobarlo (#237): el registro de la tarjeta dice si llegó
  // a grabarse. Va antes del doble toque: así se recupera aunque hayan pasado más de 60 s
  CobroDudoso dudoso;
  switch (almacen.resolverCobroDudoso(cab.idTarjeta, reg.operacion, reg.validador, dudoso)) {
    case CobrosDudosos::Resultado::GRABADO:
      // Sin contador NFC: el de esta lectura es de otro uso de la tarjeta (y el servidor toma un contador repetido
      // por un clon). Con la fecha del cobro si era la real; si era la estimada, la de ahora, que se corrige como
      // cualquier otro evento
      almacen.registrarViaje(dudoso.fechaReal ? dudoso.fecha : ahora, cab.idTarjeta, uidHex, dudoso.monto,
                             dudoso.saldo, dudoso.operacion, sp::SIN_CONTADOR, dudoso.recargaHasta);
      log("Cobro recuperado: la operación " + String(dudoso.operacion) + " de " + uidHex +
          " sí se grabó en la tarjeta; viaje registrado");
      break;
    case CobrosDudosos::Resultado::INCIERTO:
      // La tarjeta pasó por otro validador o punto antes de volver: no se sabe si aquel cobro se grabó. El servidor
      // lo resuelve con los números de operación
      almacen.registrarIncidencia(ahora, cab.idTarjeta, uidHex, "cobro dudoso op " + String(dudoso.operacion), false);
      log("Cobro dudoso de " + uidHex + " (operación " + String(dudoso.operacion) +
          ") sin resolver: la tarjeta ya va por la " + String(reg.operacion));
      break;
    default:
      break;
  }

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
  bool relecturaFalla = false;
#ifdef PRUEBA_FALLA_RELECTURA
  // Solo pruebas de banco (#237): la relectura de cada N cobros "falla" con la escritura ya hecha, como si la tarjeta
  // se retirara justo al final
  static uint32_t cobrosIntentados = 0;
  relecturaFalla = ++cobrosIntentados % PRUEBA_FALLA_RELECTURA == 0;
#endif
  if (!lector.escribir(pagina, datos, tt::TAM_REGISTRO / 4) ||
      !lector.leer(pagina, pagina + tt::TAM_REGISTRO / 4 - 1, comprobacion) ||
      memcmp(datos, comprobacion, tt::TAM_REGISTRO) != 0 || relecturaFalla) {
    // No se sabe si la escritura terminó (puede que solo fallara la relectura). Se anota y, en la próxima lectura de
    // esta tarjeta, su registro dirá si quedó cobrada (#237). Si no terminó, el registro anterior sigue válido
    CobroDudoso d;
    d.idTarjeta = cab.idTarjeta;
    d.operacion = nuevo.operacion;
    d.fecha = ahora;
    d.fechaReal = horaReal;
    d.monto = t.precio;
    d.saldo = nuevo.saldo;
    d.recargaHasta = abono ? hasta : 0;
    almacen.anotarCobroDudoso(d);
    return aviso("Acerque de nuevo", "Lectura interrumpida");
  }

  if (!almacen.registrarViaje(ahora, cab.idTarjeta, uidHex, t.precio, nuevo.saldo, nuevo.operacion, contador,
                              abono ? hasta : 0)) {
    log("ERROR: el viaje se cobró pero no se pudo guardar en la microSD");
  }
  return pasa(nombre + " " + soles(t.precio), "Saldo " + soles(nuevo.saldo),
              abono ? "Recarga +" + soles(abono) : "");
}

// wifi=off en config.txt (se lee al arrancar): el validador no enciende el WiFi ni sincroniza. Para probar que cobra
// sin red (o usarlo así en un bus sin cobertura); se quita con wifi=on y reiniciando.
bool wifiApagado = false;

// El estado de la sincronización para la pantalla de espera ("WiFi OK", "Sin WiFi"...)
const String &estadoSync() {
  static const String apagado = "WiFi apagado";
  if (wifiApagado) return apagado;
  return v2 ? v2->estado : sincronizador.estado();
}
// Con sync=v2 en config.txt y sin la clave HMAC (o sin la contraseña MQTT) en la NVS, iniciarSyncV2() se queda en v1
// y eso solo se veía en el registro: se avisa también en la segunda línea de la pantalla de espera (#45). Nunca se
// muestra la clave, solo si está. Se llama al arrancar, al montar la microSD y al recibir la configuración.
void revisarClaves() {
  const bool hmac = secretos.tieneClaveHmac();
  const bool pideV2 = almacen.listo() && almacen.config("sync") == "v2";
  String aviso;
  if (pideV2 && !hmac) aviso = "Falta clave validador";
  else if (pideV2 && !secretos.tieneClaveMqtt()) aviso = "Falta clave MQTT";
  static bool primeraVez = true;  // se anota al arrancar y luego solo si cambia
  if (primeraVez || aviso != avisoClaves) {
    log(String("Clave de validador: ") + (hmac ? "OK" : "falta") + (aviso.length() ? " (pantalla: " + aviso + ")" : ""));
  }
  primeraVez = false;
  avisoClaves = aviso;
}

uint32_t horaDelServidor() { return v2 ? v2->sinc->horaServidor() : sincronizador.horaServidor(); }

void pantallaEspera() {
  const bool wifiLista = enlaceWifi.estado() == EstadoEnlace::Listo;
  ui::estadoCanal(wifiLista ? "W" : "-", enlaceWifi.senal(),
                  static_cast<uint16_t>(min(almacen.pendientes(), static_cast<uint32_t>(65535))));
  if (!almacen.listo()) {
    ui::espera("Error microSD", "Revise la tarjeta SD");
  } else if (!lector.conectado()) {
    // Casi siempre es el PN532 colgado tras reiniciarse la ESP32 con él encendido (cargar firmware, abrir el puerto
    // serie, EN, watchdog): retiene el bus y solo vuelve quitándole la corriente (#200)
    ui::espera("Sin lector", "Desenchufe 15 s");
  } else if (!almacen.configurado()) {
    ui::espera("Sin configurar", "puente --configurar");
  } else if (!ahoraUnix()) {
    ui::espera("Bus " + String(almacen.validador()) + "  Sin hora",
               avisoClaves.length() ? avisoClaves : String("Esperando el WiFi"));
  } else {
    const String &wifi = estadoSync();
    ui::espera("Bus " + String(almacen.validador()) + (wifi.length() ? "  " + wifi : ""),
               avisoClaves.length() ? avisoClaves : "Sin subir: " + String(almacen.pendientes()));
  }
}

// La clave maestra va en la NVS, no en la microSD (#44). Si config.txt aún la trae (de una versión anterior), se copia a
// la NVS, se comprueba la copia y se borra del archivo; luego el Almacen usa la de la NVS. Se llama al arrancar y al
// montar la microSD más tarde (si al arrancar no montó, la migración espera a ese momento).
void prepararClaveMaestra() {
  uint8_t k[Secretos::TAM_CLAVE_MAESTRA], copia[Secretos::TAM_CLAVE_MAESTRA];
  const String enConfig = almacen.config("clave_maestra");
  if (enConfig.length()) {
    if (!Secretos::deHex32(enConfig, k)) {
      log("ERROR: clave_maestra de config.txt no válida; no se pasa a la NVS");
    } else if (!secretos.guardarClaveMaestra(k) || !secretos.claveMaestra(copia) || memcmp(k, copia, sizeof(k)) != 0) {
      log("ERROR: no se pudo copiar la clave maestra a la NVS; se sigue usando la de config.txt");
      return;
    } else if (almacen.borrarClaveMaestraDeConfig()) {
      log("Clave maestra pasada de config.txt a la NVS (y borrada de la microSD)");
    } else {
      log("ERROR: la clave maestra ya está en la NVS, pero no se pudo borrar de config.txt");
    }
  }
  if (secretos.claveMaestra(k)) almacen.ponerClaveMaestra(k);
}

// Las claves (clave_maestra, clave_hmac, mqtt_clave) van a la NVS, no a la microSD: se sacan de la configuración antes
// de pasarla al Almacen, y no se imprimen
String separarSecretos(const String &config) {
  String resto;
  int inicio = 0;
  while (inicio <= (int)config.length()) {
    int fin = config.indexOf(';', inicio);
    if (fin < 0) fin = config.length();
    const String par = config.substring(inicio, fin);
    uint8_t k[Secretos::TAM_CLAVE_MAESTRA];
    if (par.startsWith("clave_maestra=")) {
      if (Secretos::deHex32(par.substring(14), k) && secretos.guardarClaveMaestra(k)) {
        almacen.ponerClaveMaestra(k);
        log("Clave maestra guardada en la NVS");
      } else {
        log("ERROR: clave_maestra no válida o no se pudo guardar");
      }
    } else if (par.startsWith("clave_hmac=")) {
      log(secretos.guardarClaveHmacHex(par.substring(11)) ? "Clave HMAC guardada en la NVS"
                                                          : "ERROR: clave_hmac no válida o no se pudo guardar");
    } else if (par.startsWith("mqtt_clave=")) {
      log(secretos.guardarClaveMqtt(par.substring(11)) ? "Contraseña MQTT guardada en la NVS"
                                                       : "ERROR: no se pudo guardar la contraseña MQTT");
    } else if (par.length()) {
      if (resto.length()) resto += ";";
      resto += par;
    }
    inicio = fin + 1;
  }
  return resto;
}

void guardarConfig(const String &configRecibida) {
  const String config = separarSecretos(configRecibida);
  log(almacen.guardarConfig(config) ? "Configuración guardada en la microSD"
                                    : "ERROR: no se pudo guardar la configuración");
  revisarClaves();
}

// El ordenador es opcional: con puente_nfc.py abierto envía la configuración
// (--configurar) y la hora. Se le saluda mientras falte alguna de las dos o
// mientras conteste. El saludo no espera (#129): se envía el HOLA y la respuesta
// se recoge en las vueltas siguientes (hasta 1,5 s), sin parar el cobro.
bool puenteEstaba = false;  // si el puente contestaba antes del saludo en curso
void atenderPuente() {
  String config;
  const LectorPuente::Saludo saludo = puente.revisarSaludo(config);
  if (saludo == LectorPuente::Saludo::ESPERANDO) return;
  if (saludo == LectorPuente::Saludo::NINGUNO) {
    // Los primeros segundos tras arrancar siempre se saluda: con la hora guardada (cobrar sin WiFi) ya no falta nada
    // y, si no, el puente no podría reconfigurar una placa que ya está configurada
    const bool ventanaInicial = millis() < 30000;
    const bool falta = !almacen.configurado() || !ahoraUnix() || ventanaInicial;
    if (!falta && !puente.conectado()) return;
    if (millis() - ultimoSaludo < (puente.conectado() ? 3000 : 10000)) return;
    ultimoSaludo = millis();
    puenteEstaba = puente.conectado();
    puente.empezarSaludo();
    return;
  }
  const bool estaba = puenteEstaba;
  if (saludo != LectorPuente::Saludo::RESPONDIO) {
    if (estaba) log("Ordenador desconectado");
    return;
  }
  if (config.length()) guardarConfig(config);
  if (!horaDelServidor()) {  // la del servidor tiene preferencia
    const uint32_t h = puente.horaUnix();
    if (h) {
      fijarHora(h, millis());
    }
  }
  if (!estaba) log("Ordenador conectado. Validador " + String(almacen.validador()) + ", eventos sin subir: " +
                   String(almacen.pendientes()));
  pantallaEspera();
}

void avisarBusLiberado(int pulsos) {
  if (pulsos > 0) log("Bus I2C del PN532: SDA estaba retenida a nivel bajo; liberada con " + String(pulsos) + " pulsos de reloj");
  else if (pulsos < 0) log("Bus I2C del PN532 bloqueado (SCL=" + String(digitalRead(PIN_SCL)) + ", SDA=" + String(digitalRead(PIN_SDA)) +
                           "): no se soltó; hay que quitar la corriente de la placa");
}

void iniciarLector() {
  // Si el bus quedó retenido (SDA baja con SCL libre), se suelta antes de intentarlo. Wire1 se cierra: liberarBusI2C()
  // toma los pines como GPIO y el siguiente begin() los vuelve a dar al I2C
  if (digitalRead(PIN_SDA) == 0 && digitalRead(PIN_SCL) == 1) {
    Wire1.end();
    avisarBusLiberado(liberarBusI2C(PIN_SDA, PIN_SCL));
  }
  if (lector.iniciar()) {
    log("Lector " + lector.version() + " listo");
  } else {
    log("ERROR: el PN532 no responde. Revise los cables y el interruptor (I2C: 1 en ON, 2 en OFF)");
  }
  ultimoIntentoLector = millis();
}

// El host del broker MQTT: mqtt_servidor de config.txt o, si no, el del servidor v1 ("https://host[:puerto]/...")
String hostMqtt() {
  String h = almacen.config("mqtt_servidor");
  if (h.length()) return h;
  h = almacen.config("servidor");
  const int a = h.indexOf("://");
  if (a >= 0) h = h.substring(a + 3);
  int fin = 0;
  while (fin < (int)h.length() && h[fin] != ':' && h[fin] != '/') fin++;
  return h.substring(0, fin);
}

// Si config.txt pide sync=v2, arma el sincronizador v2. Sin las claves o sin host, avisa y se queda en v1.
void iniciarSyncV2() {
  const String pedido = almacen.config("sync");
  if (pedido.length() && pedido != "v1" && pedido != "v2") log("sync desconocido; se mantiene v1");
  if (pedido != "v2") return;
  if (!almacen.listo() || !almacen.configurado() || !almacen.modoV2()) return;
  std::unique_ptr<SyncV2> nuevo(new SyncV2());
  const String clave = secretos.claveMqtt(), host = hostMqtt();
  if (!secretos.claveHmac(nuevo->claveHmac)) {
    log("sync=v2: falta la clave HMAC en la NVS (puente_nfc.py --configurar --clave-hmac); se mantiene v1");
    return;
  }
  if (!clave.length() || !host.length()) {
    log("sync=v2: falta la contraseña MQTT o el servidor (--mqtt-clave, --mqtt-servidor); se mantiene v1");
    return;
  }
  nuevo->canal.reset(new CanalMqtt(enlaceWifi, host.c_str(), almacen.validador(), clave.c_str(), &tareaMqtt));
  // ESP-NOW entre buses (M8): opcional, config.txt espnow=on [espnow_canal=N]. Comparte la radio con el WiFi: con el
  // WiFi conectado a un AP en otro canal que espnow_canal se pausa (por defecto el canal 1)
  uint8_t canalEspNow = 1;
  if (almacen.config("espnow") == "on") {
    const int c = almacen.config("espnow_canal").toInt();
    if (c >= 1 && c <= 13) canalEspNow = static_cast<uint8_t>(c);
    nuevo->vecinos.reset(new VecinosEspNow(log));
    // Por la interfaz AP y en largo alcance: balizas y reintentos en LR a 250 kbit/s (llegan más lejos), el primer
    // intento de cada fragmento a 1 Mbit/s (más rápido de cerca). En la AP no toca la conexión WiFi de la STA
    nuevo->vecinos->usarInterfazAp();
    // Sin WiFi, buscar a los vecinos en otros canales si en espnow_canal no hay ninguno (espnow_busqueda=off lo quita)
    nuevo->vecinos->buscarVecinos(almacen.config("espnow_busqueda") != "off");
    nuevo->radio.reset(new RadioEspNow(*nuevo->vecinos));
  }
  const bool soloVecinos = almacen.config("subida") == "vecinos";
  if (soloVecinos && !nuevo->vecinos) log("subida=vecinos sin espnow=on: no hay por dónde subir los eventos");
  CanalPaquetes &subida = soloVecinos ? static_cast<CanalPaquetes &>(canalSinServidor) : *nuevo->canal;
  nuevo->gestor.reset(new GestorEnlaces(enlaceWifi, subida, nullptr, nuevo->vecinos.get()));
  nuevo->gestor->canalEspNow(canalEspNow);
  canalEspNowConfigurado = canalEspNow;
  enlaceWifi.alElegirCanal(alCanalDelWifi);
  enlaceWifi.permitirEscaneo(puedeEscanearWifi);
  nuevo->almacenSync.reset(new AlmacenSyncBinario(almacen.cola(), almacen.estado(), almacen.paquetesAjenos(),
                                                  almacen.aplicador(), ahoraUnix, &almacen.firmados()));
  ConfigSyncV2 c;
  c.validador = almacen.validador();
  c.claveHmac = nuevo->claveHmac;
  c.clavePublicaServidor = CLAVE_SERVIDOR;
  c.versionFirmware = VERSION_FIRMWARE;
  c.ahoraUnix = ahoraUnix;
  c.correccion = &correccionHora;
  nuevo->sinc.reset(new SincronizadorV2(*nuevo->almacenSync, c));
  if (nuevo->radio) {
    nuevo->intercambio.reset(new IntercambioVecinos(*nuevo->radio, *nuevo->sinc, *nuevo->almacenSync, almacen.validador()));
    sp::Baliza inicial;
    inicial.validador = almacen.validador();
    nuevo->vecinos->publicar(inicial);  // hasta que IntercambioVecinos publique la suya
  }
  if (!wifiApagado) nuevo->gestor->iniciar();  // enciende el WiFi y, si hay, ESP-NOW
  else if (nuevo->vecinos) nuevo->vecinos->iniciar(canalEspNow);  // sin WiFi: solo ESP-NOW
  if (nuevo->vecinos) {
    using T = VecinosEspNow::Tasa;
    nuevo->vecinos->fijarTasas(T::ESTANDAR, T::LR250, T::LR250);
  }
  v2 = std::move(nuevo);
  log("Sincronización v2 activa: MQTT a " + host + " como validador " + String(almacen.validador()) +
      (v2->vecinos ? ", ESP-NOW en el canal " + String(canalEspNow) : String("")) +
      (soloVecinos ? ". subida=vecinos: sin MQTT, los eventos solo salen por ESP-NOW" : ""));
}

// Un turno de la sincronización v2. Solo se llama con el lector libre (sin tarjeta delante): conectar con el broker
// (TLS) bloquea un par de segundos, y paso() hace una operación en la microSD.
void servicioSyncV2() {
  if (!v2) return;
  const uint32_t ahora = millis();
  CanalPaquetes *canal = nullptr;
  if (!wifiApagado) {
    // El canal del AP al que está conectado el WiFi decide si ESP-NOW se pausa (solo anda en su canal)
    const uint8_t canalWifi = enlaceWifi.estado() == EstadoEnlace::Listo ? WiFi.channel() : v2->gestor->canalEspNow();
    if (v2->vecinos) {
      v2->vecinos->silenciar(enlaceWifi.conectando());
      v2->vecinos->permitirBusqueda(!enlaceWifi.conectando() && !enlaceWifi.escaneando());
    }
    v2->gestor->atender(ahora, false, canalWifi);
    canal = v2->gestor->activo();
  } else if (v2->vecinos) {
    v2->vecinos->atender();  // sin WiFi el gestor no corre: ESP-NOW se atiende aquí
  }
  uint32_t tx = 0, rx = 0;
  if (almacen.listo()) almacen.consumo().bytesHoy(tx, rx);
  v2->sinc->telemetria(enlaceWifi.senal(), tx, rx);
  v2->sinc->atender(canal, ahora);
  if (v2->sinc->horaServidor() && v2->sinc->horaServidor() != v2->horaVista) {
    v2->horaVista = v2->sinc->horaServidor();
    fijarHora(v2->sinc->horaServidor(), v2->sinc->millisHora());
    pantallaEspera();
  }
  if (v2->intercambio) v2->intercambio->atender(ahora, canal != nullptr, !horaReal);
  v2->almacenSync->paso(true);
  const String estado = canal ? "WiFi OK" : (enlaceWifi.estado() == EstadoEnlace::Listo ? "Sin servidor" : "Sin WiFi");
  if (estado != v2->estado) {
    v2->estado = estado;
    log("Sync v2: " + estado + (enlaceWifi.ultimoError().length() ? " (" + enlaceWifi.ultimoError() + ")" : ""));
    pantallaEspera();
  }
  // Una línea de diagnóstico cada minuto: sirve para ver por qué no sube (broker, firmas, nada que subir...)
  static uint32_t ultimoInforme = 0;
  if (ahora - ultimoInforme >= 60000) {
    ultimoInforme = ahora;
    log("Sync v2: " + estado + ", HOLA enviados " + String(v2->sinc->holasEnviados()) + ", paquetes de eventos " +
        String(v2->sinc->paquetesEventos()) + ", pendientes " + String(v2->sinc->pendientes()) + ", rechazados " +
        String(v2->sinc->rechazados()) + " (" + sp::describir(v2->sinc->ultimoRechazo()) + "), hora del servidor " +
        String(v2->sinc->horaServidor()) +
        (enlaceWifi.estado() == EstadoEnlace::Listo ? ", WiFi en el canal " + String(WiFi.channel()) : String("")) +
        ", última conexión MQTT " + String(v2->canal->ultimaConexionMs()) + " ms" +
        (v2->canal->conectando() ? " (conectando)" : "") + ", pila libre de la tarea " + String(tareaMqtt.pilaLibre()) + " B");
    if (v2->intercambio) {
      const IntercambioVecinos::Cuenta &c = v2->intercambio->cuenta();
      log("ESP-NOW: canal " + String(v2->vecinos->canal()) +
          (v2->vecinos->buscandoCanal() ? " (buscando vecinos)" : "") + ", saltos de canal " +
          String(v2->vecinos->saltosDeCanal()) + ", vecinos " + String(v2->vecinos->cantidad()) + ", pedidos de listas " + String(c.pedidosListas) +
          ", de acuses " + String(c.pedidosAcuses) + ", atendidos " + String(c.pedidosAtendidos) + ", EVENTOS entregados " +
          String(c.eventosEntregados) + " (abortados " + String(c.eventosAbortados) + "), LISTAS " + String(c.listasEntregadas) + ", ACUSES " + String(c.acusesEntregados) +
          ", recibidos " + String(c.recibidos) + ", ajenos de vecinos " + String(v2->sinc->ajenosRecibidos()) + ", pedidos de hora " + String(c.pedidosHora) +
          ", horas dadas " + String(c.horasEntregadas) + ", horas de vecino " + String(v2->sinc->horasDeVecino()));
    }
  }
}

// ESP-NOW sigue al WiFi (#67): antes de conectar a un AP se pasa a su canal, y al perderlo vuelve a espnow_canal
void alCanalDelWifi(uint8_t canal) {
  if (!v2 || !v2->vecinos) return;
  const uint8_t c = canal ? canal : canalEspNowConfigurado;
  v2->vecinos->cambiarCanal(c);
  v2->gestor->canalEspNow(c);  // el gestor solo pausa ESP-NOW si el WiFi está en otro canal que este
}
// No se busca WiFi a mitad de un mensaje de ESP-NOW: el escaneo saca la radio del canal ~1,6 s
bool puedeEscanearWifi() { return !(v2 && v2->vecinos && v2->vecinos->enviando()); }

// El circulito que gira en la pantalla de espera mientras se envían o reciben datos de la sincronización v2: paquetes
// C1 con el servidor o con un vecino, PEDIDOs de ESP-NOW y un mensaje fragmentado en curso. Las balizas (cada 2 s) y
// los keepalive de MQTT no cuentan: si no, giraría siempre. Se queda encendido un rato tras lo último para que se vea.
void indicarActividad() {
  static uint32_t firmaAnterior = 0, ultimaMs = 0;
  if (!v2) return;
  uint32_t firma = v2->sinc->holasEnviados() + v2->sinc->paquetesEventos() + v2->sinc->paquetesAjenos() +
                   v2->sinc->reenvios() + v2->sinc->paquetesRecibidos();
  bool enCurso = false;
  if (v2->intercambio) {
    const IntercambioVecinos::Cuenta &c = v2->intercambio->cuenta();
    firma += c.pedidosListas + c.pedidosAcuses + c.pedidosAtendidos + c.eventosEntregados + c.listasEntregadas +
             c.acusesEntregados;
    enCurso = v2->vecinos->enviando();
  }
  const uint32_t ahora = millis();
  if (firma != firmaAnterior || enCurso) {
    firmaAnterior = firma;
    ultimaMs = ahora;
  }
  ui::actividad(ultimaMs && ahora - ultimaMs < 1500);
}

// El portal necesita la radio y memoria libres para crear su red: antes de abrirlo se suelta la conexión MQTT/TLS
// (sus búferes ocupan decenas de KB) y se pausa ESP-NOW. Al cerrarse, el gestor de enlaces los reanuda solo.
void abrirPortal() {
  if (v2) {
    // Si está conectando en la otra tarea, se espera a que acabe (unos segundos como mucho: el TLS y el CONNECT tienen
    // sus límites) para soltar ya sus búferes; si no, desconectar() lo dejaría para un atender() que no llega con el
    // portal abierto
    for (uint32_t desde = millis(); v2->canal->conectando() && millis() - desde < 20000;) delay(10);
    v2->canal->desconectar();
    if (v2->vecinos) v2->vecinos->pausar(true);
  }
  portal.iniciar();
}

void reiniciarWifi() {
  if (v2) v2->canal->desconectar();
  // abrirPortal() pausó ESP-NOW directamente (el gestor no lo sabe y no lo reanudaba: quedaba parado hasta reiniciar)
  if (v2 && v2->vecinos) v2->vecinos->pausar(false);
  sincronizador.reiniciarWifi();
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
  // Con un búfer de salida grande, un log() deja el texto en él y vuelve: sin él, cada línea esperaba a salir por el
  // cable a 115200 baudios (el informe de cada minuto, ~650 B, paraba loop() 64 ms; #129)
  Serial.setTxBufferSize(2048);
  Serial.begin(115200);
  activarWatchdog();  // desde el principio: también vigila un cuelgue durante setup()
  registrarMotivoReinicio();
#ifdef MARGEN_DOBLE_TOQUE_PRUEBA
  log("PRUEBA DE BANCO: margen de doble toque de " + String(MARGEN_DOBLE_TOQUE) + " s (no usar en servicio)");
#endif
#ifdef PRUEBA_FALLA_RELECTURA
  log("PRUEBA DE BANCO: falla la relectura de 1 de cada " + String(PRUEBA_FALLA_RELECTURA) + " cobros (no usar en servicio)");
#endif
  // La configuración del WiFi (red, canal, protocolo) no se guarda en la flash: las redes están en la microSD y
  // WiFi.begin() se llama en cada intento de conexión (con una red inestable, cientos de escrituras por noche); y lo
  // guardado se cargaba con cualquier firmware (el largo alcance que dejó prueba_espnow escondía el portal)
  WiFi.persistent(false);
  PASO("setup: bus I2C");
  const int pulsosI2C = liberarBusI2C(PIN_SDA, PIN_SCL);  // antes de la OLED y del PN532 (#200); se avisa cuando ya hay registro
  PASO("setup: ui::iniciar");
  ui::iniciar();
  PASO("setup: pantalla Iniciando");
  ui::espera("Validador", "Iniciando...");
  pinMode(PIN_BOOT, INPUT_PULLUP);
  PASO("setup: hora guardada");
  if (const uint32_t h = horaGuardada()) {  // hora estimada para cobrar aunque no haya WiFi
    baseUnix = h;
    baseMillis = millis();
  }
  PASO("setup: microSD");
  almacen.alGuardarEvento(anotarEvento);  // antes de iniciar: así se anota todo evento nuevo
  almacen.iniciar();
  PASO("setup: clave maestra");
  if (almacen.listo()) prepararClaveMaestra();
  PASO("setup: lector PN532");
  avisarBusLiberado(pulsosI2C);
  iniciarLector();
  PASO("setup: modo sync");
  wifiApagado = almacen.config("wifi") == "off";
#ifdef WIFI_APAGADO_PRUEBA
  wifiApagado = true;  // compilación de prueba (-DWIFI_APAGADO_PRUEBA): sin tocar la configuración de la microSD
#endif
  if (wifiApagado) log("wifi=off: el WiFi no se enciende; solo se cobra (se guarda todo para subirlo después)");
  revisarClaves();
  iniciarSyncV2();
  PASO("setup: pantalla de espera");
  pantallaEspera();
  PASO("setup: portal");
  // Sin ninguna red guardada, el portal se abre solo (se cierra a los 10 min sin uso)
  if (almacen.listo() && almacen.configurado() && almacen.redes().empty()) abrirPortal();
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
  if (portal.activo()) {
    // Mientras se configura el WiFi no se cobra
    if (portal.atender()) {
      botonDesde = 0;
      reiniciarWifi();
      pantallaEspera();
    } else {
      // El puente reinicia la placa si deja de oírla: se le saluda de vez en cuando (sin esperar la respuesta; la
      // configuración que mande aquí se ignora, como antes)
      String config;
      if (puente.revisarSaludo(config) == LectorPuente::Saludo::NINGUNO && puente.conectado() &&
          millis() - ultimoSaludo > 3000) {
        puente.empezarSaludo();
        ultimoSaludo = millis();
      }
    }
    delay(2);
    return;
  }
  PASO("loop: botón BOOT");
  if (botonMantenido() && almacen.listo() && almacen.configurado()) {
    abrirPortal();
    return;
  }

  PASO("loop: ui::actualizar");
  indicarActividad();
  ui::actualizar();
  PASO("loop: sincronizador");
  if (!v2 && !wifiApagado && sincronizador.atender()) {  // con sync=v2 corre más abajo, con el lector libre
    if (sincronizador.horaServidor()) {  // la hora del servidor permite cobrar sin el puente
      fijarHora(sincronizador.horaServidor(), sincronizador.millisHora());
    }
    PASO("loop: pantalla tras sincronizar");
    pantallaEspera();  // actualiza "Sin subir"
  }
  if (estadoSync() != estadoMostrado) {
    estadoMostrado = estadoSync();
    PASO("loop: pantalla por cambio de estado");
    pantallaEspera();
  }

  PASO("loop: reintento de la microSD");
  if (almacen.reintentarMontaje()) {
    log("microSD montada tras reintentar");
    prepararClaveMaestra();
    revisarClaves();
    pantallaEspera();
    if (almacen.configurado() && almacen.redes().empty()) abrirPortal();  // como en setup()
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
    PASO("loop: sync v2 (sin lector)");
    servicioSyncV2();  // sin lector no hay tarjeta que esperar
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
  // Un solo intento de InListPassiveTarget por vuelta (~30 ms): antes se repetía durante 300 ms y loop() nunca bajaba
  // de 300 ms (#129). La búsqueda sigue igual de seguida; entre intento e intento se atiende lo demás.
  if (!lector.esperarTarjeta(BUSCAR_TARJETA_MS, uid, lenUid)) {
    if (!lector.conectado()) {
      log("ERROR: el PN532 dejó de responder");
      pantallaEspera();
    }
    PASO("loop: sync v2 y mantenimiento de la microSD");
    servicioSyncV2();         // sin tarjeta delante: conectar, intercambiar paquetes y un paso de la microSD
    almacen.mantenimiento();  // un paso de lo que haya pendiente (compactar la cola)
    return;
  }

  // La misma tarjeta que se acaba de retirar: al salir cruza el borde del campo y el lector la ve un instante, pero el
  // resto falla, y su "Tarjeta no válida" o "Lectura fallida" tapaba en pantalla el resultado bueno (#246). Se ignora
  // y se vuelve a esperar a que se retire
  if (ultimaTarjeta.ignorar && lenUid == ultimaTarjeta.len && memcmp(uid, ultimaTarjeta.uid, lenUid) == 0 &&
      millis() - ultimaTarjeta.retiradaMs < IGNORAR_MISMA_TARJETA_MS) {
    lector.terminar();
    esperarRetirada();
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

  memcpy(ultimaTarjeta.uid, uid, lenUid);
  ultimaTarjeta.len = lenUid;
  ultimaTarjeta.ignorar = r.tipo != 'A';  // tras "Acerque de nuevo" hay que poder volver a leerla enseguida
  esperarRetirada();
}
