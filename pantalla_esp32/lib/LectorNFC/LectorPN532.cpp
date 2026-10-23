#include "LectorPN532.h"

namespace {

constexpr uint8_t DIRECCION = 0x24;  // dirección I2C del PN532 (7 bits)
constexpr uint8_t HOST = 0xD4, PN532 = 0xD5;

// Comandos del PN532 (manual de usuario, cap. 7)
constexpr uint8_t GET_FIRMWARE_VERSION = 0x02, SAM_CONFIGURATION = 0x14, RF_CONFIGURATION = 0x32,
                  IN_DATA_EXCHANGE = 0x40, IN_COMMUNICATE_THRU = 0x42, IN_LIST_PASSIVE_TARGET = 0x4A,
                  IN_RELEASE = 0x52;
constexpr uint8_t WRITE_ULTRALIGHT = 0xA2;
constexpr uint8_t ACK_TARJETA = 0x0A;  // ACK de 4 bits de la tarjeta; otro valor = NAK

constexpr uint32_t ESPERA_ACK = 30;        // ms
constexpr uint32_t ESPERA_RESPUESTA = 300;  // ms; una escritura en la tarjeta tarda ~5 ms
constexpr size_t MAX_DATOS = 96;            // FAST_READ del área del sistema = 72 bytes

}  // namespace

bool LectorPN532::enviarTrama(const uint8_t *cmd, size_t n) {
  const uint8_t len = n + 1;  // + TFI
  uint8_t suma = HOST;
  bus_.beginTransmission(DIRECCION);
  const uint8_t cabecera[] = {0x00, 0x00, 0xFF, len, (uint8_t)(0x100 - len), HOST};
  bus_.write(cabecera, sizeof(cabecera));
  for (size_t i = 0; i < n; i++) {
    bus_.write(cmd[i]);
    suma += cmd[i];
  }
  bus_.write((uint8_t)(0x100 - suma));
  bus_.write((uint8_t)0x00);
  return bus_.endTransmission() == 0;
}

bool LectorPN532::leerListo(uint8_t *buf, size_t n, uint32_t espera) {
  // IRQ baja cuando el PN532 tiene datos listos. Leer antes de tiempo hace que
  // retenga el reloj del bus y la lectura falle.
  const uint32_t inicio = millis();
  while (true) {
    if (digitalRead(irq_) == LOW) {
      if (bus_.requestFrom(DIRECCION, (uint8_t)(n + 1)) == n + 1 && (bus_.read() & 0x01)) {
        for (size_t i = 0; i < n; i++) buf[i] = bus_.read();
        return true;
      }
      while (bus_.available()) bus_.read();
    }
    if (millis() - inicio > espera) return false;
    delay(1);
  }
}

void LectorPN532::abortar() {
  // Un ACK del ordenador cancela el comando en curso
  static const uint8_t ACK[] = {0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00};
  bus_.beginTransmission(DIRECCION);
  bus_.write(ACK, sizeof(ACK));
  bus_.endTransmission();
  delay(2);
}

int LectorPN532::intercambiar(const uint8_t *cmd, size_t n, uint8_t *resp, size_t max, uint32_t espera) {
  // Recién encendido, el PN532 duerme y rechaza el primer mensaje mientras despierta
  bool enviado = false;
  for (int i = 0; i < 3 && !enviado; i++) {
    enviado = enviarTrama(cmd, n);
    if (!enviado) delay(20);
  }
  uint8_t ack[6];
  if (!enviado || !leerListo(ack, sizeof(ack), ESPERA_ACK) ||
      memcmp(ack, "\x00\x00\xFF\x00\xFF\x00", sizeof(ack)) != 0) {
    conectado_ = false;
    return -1;
  }
  // Trama: 00 00 FF LEN LCS D5 código datos... DCS 00
  uint8_t r[MAX_DATOS + 9];
  const size_t leer = min(sizeof(r), max + 9);
  if (!leerListo(r, leer, espera)) {
    abortar();
    conectado_ = false;
    return -1;
  }
  const uint8_t len = r[3];
  if (r[0] != 0x00 || r[1] != 0x00 || r[2] != 0xFF || (uint8_t)(len + r[4]) != 0 || len < 2 ||
      len + 7u > leer || r[5] != PN532 || r[6] != cmd[0] + 1) {
    conectado_ = false;
    return -1;
  }
  uint8_t suma = 0;
  for (size_t i = 0; i <= len; i++) suma += r[5 + i];  // datos + DCS
  if (suma != 0) {
    conectado_ = false;
    return -1;
  }
  const size_t datos = len - 2;
  memcpy(resp, r + 7, datos);
  conectado_ = true;
  return datos;
}

bool LectorPN532::iniciar() {
  pinMode(irq_, INPUT_PULLUP);
  bus_.begin(sda_, scl_, 100000);
  bus_.setTimeOut(1000);
  // Despertar: una transmisión vacía y una pausa
  bus_.beginTransmission(DIRECCION);
  delay(20);
  bus_.endTransmission();
  delay(100);

  uint8_t r[8];
  const uint8_t version[] = {GET_FIRMWARE_VERSION};
  // Si la ESP32 se reinició a mitad de un comando, el PN532 puede tardar en
  // atender el primero: se reintenta
  int n = -1;
  for (int i = 0; i < 3 && (n < 4 || r[0] != 0x32); i++) {
    if (i) {
      abortar();
      delay(100);
    }
    n = intercambiar(version, sizeof(version), r, sizeof(r), ESPERA_RESPUESTA);
  }
  if (n < 4 || r[0] != 0x32) {
    conectado_ = false;
    return false;
  }
  version_ = "PN532 v" + String(r[1]) + "." + String(r[2]);
  // Modo normal, IRQ activa
  const uint8_t sam[] = {SAM_CONFIGURATION, 0x01, 0x14, 0x01};
  // Pocos reintentos al buscar tarjeta: así InListPassiveTarget vuelve enseguida
  // aunque no haya ninguna (por defecto reintenta para siempre)
  const uint8_t reintentos[] = {RF_CONFIGURATION, 0x05, 0xFF, 0x01, 0x02};
  return intercambiar(sam, sizeof(sam), r, sizeof(r), ESPERA_RESPUESTA) >= 0 &&
         intercambiar(reintentos, sizeof(reintentos), r, sizeof(r), ESPERA_RESPUESTA) >= 0;
}

int LectorPN532::activar(uint8_t uid[10], size_t &lenUid) {
  // Una tarjeta ISO 14443A a 106 kbps
  const uint8_t buscar[] = {IN_LIST_PASSIVE_TARGET, 0x01, 0x00};
  uint8_t r[24];
  const int n = intercambiar(buscar, sizeof(buscar), r, sizeof(r), ESPERA_RESPUESTA);
  if (n < 0) return -1;
  // NbTg | Tg | SENS_RES(2) | SEL_RES | largo del UID | UID
  if (n < 6 || r[0] != 1 || r[5] > 10 || n < 6 + r[5]) return 0;
  lenUid = r[5];
  memcpy(uid, r + 6, lenUid);
  return 1;
}

bool LectorPN532::reiniciarCampo() {
  uint8_t r[4];
  const uint8_t apagar[] = {RF_CONFIGURATION, 0x01, 0x00};
  if (intercambiar(apagar, sizeof(apagar), r, sizeof(r), ESPERA_RESPUESTA) < 0) return false;
  delay(5);  // la tarjeta se queda sin energía y se reinicia
  // InListPassiveTarget vuelve a encender el campo
  return true;
}

bool LectorPN532::reactivar() {
  uint8_t uid[10];
  size_t lenUid;
  return reiniciarCampo() && activar(uid, lenUid) == 1;
}

bool LectorPN532::esperarTarjeta(uint32_t ms, uint8_t uid[10], size_t &lenUid) {
  if (!conectado_) return false;
  const uint32_t inicio = millis();
  do {
    const int r = activar(uid, lenUid);
    if (r != 0) return r == 1;
  } while (millis() - inicio < ms);
  return false;
}

bool LectorPN532::comando(const uint8_t *cmd, size_t lenCmd, uint8_t *resp, size_t &lenResp) {
  const size_t max = lenResp;
  lenResp = 0;
  uint8_t trama[24], r[MAX_DATOS + 1];
  if (lenCmd + 1 > sizeof(trama)) return false;
  trama[0] = IN_COMMUNICATE_THRU;
  memcpy(trama + 1, cmd, lenCmd);
  const int n = intercambiar(trama, lenCmd + 1, r, sizeof(r), ESPERA_RESPUESTA);
  if (n < 0) return false;
  // r[0] = estado (0 = correcto). Un solo byte de datos es el ACK/NAK de 4 bits
  // de la tarjeta: tras un NAK la tarjeta queda muda y hay que reactivarla.
  if (n < 2 || (r[0] & 0x3F) != 0 || (n == 2 && r[1] != ACK_TARJETA) || (size_t)(n - 1) > max) {
    reactivar();
    return false;
  }
  if (n == 2) return true;  // ACK sin datos
  lenResp = n - 1;
  memcpy(resp, r + 1, lenResp);
  return true;
}

bool LectorPN532::escribir(uint8_t pagina, const uint8_t *datos, size_t paginas) {
  for (size_t i = 0; i < paginas; i++) {
    const uint8_t trama[] = {IN_DATA_EXCHANGE, 0x01, WRITE_ULTRALIGHT, (uint8_t)(pagina + i),
                             datos[4 * i], datos[4 * i + 1], datos[4 * i + 2], datos[4 * i + 3]};
    uint8_t r[4];
    const int n = intercambiar(trama, sizeof(trama), r, sizeof(r), ESPERA_RESPUESTA);
    if (n < 1 || (r[0] & 0x3F) != 0) {
      if (n >= 0) reactivar();
      return false;
    }
  }
  return true;
}

void LectorPN532::terminar() {
  uint8_t r[4];
  const uint8_t liberar[] = {IN_RELEASE, 0x00};
  intercambiar(liberar, sizeof(liberar), r, sizeof(r), ESPERA_RESPUESTA);
}

bool LectorPN532::retirada(uint32_t ms) {
  // Se reinicia el campo antes de buscar: una tarjeta ya liberada (HALT) no
  // contestaría y parecería retirada estando aún sobre el lector
  const uint32_t inicio = millis();
  do {
    uint8_t uid[10];
    size_t lenUid;
    if (!reiniciarCampo()) return false;
    const int r = activar(uid, lenUid);
    if (r < 0) return false;
    if (r == 0) return true;
    delay(50);
  } while (millis() - inicio < ms);
  return false;
}
