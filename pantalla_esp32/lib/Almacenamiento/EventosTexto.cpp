#include "EventosTexto.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace eventos_texto {

namespace {

// Días desde 1970-01-01 de una fecha civil (H. Hinnant)
int64_t diasDesdeCivil(int64_t a, unsigned m, unsigned d) {
  a -= m <= 2;
  const int64_t era = (a >= 0 ? a : a - 399) / 400;
  const unsigned aa = static_cast<unsigned>(a - era * 400);
  const unsigned doy = (153 * (m > 2 ? m - 3 : m + 9) + 2) / 5 + d - 1;
  const unsigned doe = aa * 365 + aa / 4 - aa / 100 + doy;
  return era * 146097 + static_cast<int64_t>(doe) - 719468;
}

void civilDeDias(int64_t z, int &a, unsigned &m, unsigned &d) {
  z += 719468;
  const int64_t era = (z >= 0 ? z : z - 146096) / 146097;
  const unsigned doe = static_cast<unsigned>(z - era * 146097);
  const unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  const int64_t y = static_cast<int64_t>(yoe) + era * 400;
  const unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  const unsigned mp = (5 * doy + 2) / 153;
  d = doy - (153 * mp + 2) / 5 + 1;
  m = mp < 10 ? mp + 3 : mp - 9;
  a = static_cast<int>(y + (m <= 2));
}

// Lo que sigue a "clave": en la línea (primera aparición), o nullptr
const char *valor(const char *json, const char *clave) {
  char buscar[32];
  snprintf(buscar, sizeof(buscar), "\"%s\":", clave);
  const char *p = strstr(json, buscar);
  return p ? p + strlen(buscar) : nullptr;
}

bool esNulo(const char *v) { return v && strncmp(v, "null", 4) == 0; }

// Número entero (con signo) del valor; false si no hay un número
bool numero(const char *v, int64_t &n) {
  if (!v) return false;
  char *fin;
  n = strtoll(v, &fin, 10);
  return fin != v;
}

// Texto entre comillas del valor, sin escapes; false si no hay o no está cerrado
bool texto(const char *v, char *salida, size_t cap) {
  if (!v || *v != '"') return false;
  v++;
  size_t n = 0;
  while (*v && *v != '"') {
    char c = *v++;
    if (c == '\\' && *v) c = *v++;
    if (n + 1 < cap) salida[n++] = c;
  }
  if (*v != '"') return false;
  salida[n] = '\0';
  return true;
}

int nibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

}  // namespace

void isoDeUnix(uint32_t unix, char salida[26]) {
  const int64_t dias = unix / 86400;
  const uint32_t seg = unix % 86400;
  int a;
  unsigned m, d;
  civilDeDias(dias, a, m, d);
  snprintf(salida, 26, "%04d-%02u-%02uT%02u:%02u:%02u+00:00", a, m, d, seg / 3600, seg % 3600 / 60, seg % 60);
}

bool unixDeIso(const char *iso, uint32_t &unix) {
  int a, mo, d, h, mi, s;
  if (!iso || sscanf(iso, "%4d-%2d-%2dT%2d:%2d:%2d", &a, &mo, &d, &h, &mi, &s) != 6) return false;
  if (mo < 1 || mo > 12 || d < 1 || d > 31 || h > 23 || mi > 59 || s > 60 || a < 1970) return false;
  const int64_t t = diasDesdeCivil(a, mo, d) * 86400 + h * 3600 + mi * 60 + s;
  if (t < 0 || t > 0xFFFFFFFFll) return false;
  unix = static_cast<uint32_t>(t);
  return true;
}

void ponerDetalle(sp::Evento &e, const char *t) {
  memset(e.detalle, 0, sizeof(e.detalle));
  size_t n = 0;
  for (; t && *t && n < sizeof(e.detalle) - 1; t++) {
    const unsigned char c = static_cast<unsigned char>(*t);
    e.detalle[n++] = (c >= 32 && c < 127) ? static_cast<char>(c) : '?';
  }
}

bool uidDeHex(const char *hex, sp::Evento &e) {
  const size_t n = hex ? strlen(hex) : 0;
  memset(e.uid, 0, sizeof(e.uid));
  e.largoUid = 0;
  if (n % 2 != 0 || n / 2 > sizeof(e.uid)) return false;
  for (size_t i = 0; i < n / 2; i++) {
    const int a = nibble(hex[2 * i]), b = nibble(hex[2 * i + 1]);
    if (a < 0 || b < 0) {
      memset(e.uid, 0, sizeof(e.uid));
      return false;
    }
    e.uid[i] = static_cast<uint8_t>(a * 16 + b);
  }
  e.largoUid = static_cast<uint8_t>(n / 2);
  return true;
}

size_t aJson(const sp::Evento &e, char *salida, size_t capacidad) {
  char fecha[26];
  isoDeUnix(e.fecha, fecha);
  char uid[2 * sizeof(e.uid) + 1] = {0};
  for (size_t i = 0; i < e.largoUid && i < sizeof(e.uid); i++) snprintf(uid + 2 * i, 3, "%02X", e.uid[i]);
  const char *tipo = e.tipo == sp::VIAJE ? "viaje" : e.tipo == sp::FRAUDE ? "fraude" : "incidencia";
  int n;
  if (e.tipo == sp::VIAJE) {
    char recarga[16] = "null", contador[16] = "null";
    if (e.recargaHasta) snprintf(recarga, sizeof(recarga), "%lu", (unsigned long)e.recargaHasta);
    if (e.contador != sp::SIN_CONTADOR) snprintf(contador, sizeof(contador), "%lu", (unsigned long)e.contador);
    n = snprintf(salida, capacidad,
                 "{\"id\":%lu,\"tipo\":\"viaje\",\"fecha\":\"%s\",\"tarjeta_id\":%lu,\"uid\":\"%s\",\"monto\":%ld,"
                 "\"saldo_final\":%ld,\"operacion\":%u,\"contador\":%s,\"recarga_hasta\":%s,\"detalle\":null}",
                 (unsigned long)e.idEvento, fecha, (unsigned long)e.idTarjeta, uid, (long)e.monto, (long)e.saldoFinal,
                 (unsigned)e.operacion, contador, recarga);
  } else {
    // el detalle es ASCII (ponerDetalle) y solo hay que escapar las comillas y la barra
    char det[2 * sizeof(e.detalle)];
    size_t k = 0;
    for (size_t i = 0; i < sizeof(e.detalle) && e.detalle[i]; i++) {
      if (e.detalle[i] == '"' || e.detalle[i] == '\\') det[k++] = '\\';
      det[k++] = e.detalle[i];
    }
    det[k] = '\0';
    n = snprintf(salida, capacidad,
                 "{\"id\":%lu,\"tipo\":\"%s\",\"fecha\":\"%s\",\"tarjeta_id\":%lu,\"uid\":\"%s\",\"monto\":null,"
                 "\"saldo_final\":null,\"operacion\":null,\"contador\":null,\"recarga_hasta\":null,\"detalle\":\"%s\"}",
                 (unsigned long)e.idEvento, tipo, fecha, (unsigned long)e.idTarjeta, uid, det);
  }
  return (n > 0 && static_cast<size_t>(n) < capacidad) ? static_cast<size_t>(n) : 0;
}

bool deJson(const char *linea, sp::Evento &e) {
  if (!linea || strncmp(linea, "{\"id\":", 6) != 0) return false;
  const size_t largo = strlen(linea);
  if (largo < 2 || linea[largo - 1] != '}') return false;  // una línea cortada por un corte de energía
  memset(&e, 0, sizeof(e));
  int64_t n;
  char t[32];
  if (!numero(valor(linea, "id"), n) || n < 1 || n > 0xFFFFFFFFll) return false;
  e.idEvento = static_cast<uint32_t>(n);
  if (!texto(valor(linea, "tipo"), t, sizeof(t))) return false;
  if (strcmp(t, "viaje") == 0) e.tipo = sp::VIAJE;
  else if (strcmp(t, "incidencia") == 0) e.tipo = sp::INCIDENCIA;
  else if (strcmp(t, "fraude") == 0) e.tipo = sp::FRAUDE;
  else return false;
  if (!texto(valor(linea, "fecha"), t, sizeof(t)) || !unixDeIso(t, e.fecha)) return false;
  if (numero(valor(linea, "tarjeta_id"), n) && n >= 0) e.idTarjeta = static_cast<uint32_t>(n);
  if (texto(valor(linea, "uid"), t, sizeof(t))) uidDeHex(t, e);  // un UID raro se deja vacío
  const char *v = valor(linea, "monto");
  if (!esNulo(v) && numero(v, n)) e.monto = static_cast<int32_t>(n);
  v = valor(linea, "saldo_final");
  if (!esNulo(v) && numero(v, n)) e.saldoFinal = static_cast<int32_t>(n);
  v = valor(linea, "operacion");
  if (!esNulo(v) && numero(v, n)) e.operacion = static_cast<uint16_t>(n);
  v = valor(linea, "contador");
  e.contador = (!esNulo(v) && numero(v, n)) ? static_cast<uint32_t>(n) : sp::SIN_CONTADOR;
  v = valor(linea, "recarga_hasta");
  if (!esNulo(v) && numero(v, n)) e.recargaHasta = static_cast<uint32_t>(n);
  char det[128];
  if (texto(valor(linea, "detalle"), det, sizeof(det))) ponerDetalle(e, det);
  return true;
}

}  // namespace eventos_texto
