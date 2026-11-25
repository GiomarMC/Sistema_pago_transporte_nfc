// M3-01: conversión sp::Evento <-> línea JSON del sincronizador v1 y de /eventos.txt
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#include <EventosTexto.h>

using namespace eventos_texto;

namespace {

// Lo que escribía el Almacen de antes (String con los mismos campos), para comprobar que el formato no cambió
const char *VIAJE_ANTIGUO =
    "{\"id\":7,\"tipo\":\"viaje\",\"fecha\":\"2026-11-21T14:03:09+00:00\",\"tarjeta_id\":1234,\"uid\":\"04A1B2C3\","
    "\"monto\":130,\"saldo_final\":870,\"operacion\":42,\"contador\":9,\"recarga_hasta\":null,\"detalle\":null}";
const char *INCIDENCIA_ANTIGUA =
    "{\"id\":8,\"tipo\":\"fraude\",\"fecha\":\"2026-11-21T14:05:00+00:00\",\"tarjeta_id\":1234,\"uid\":\"04A1B2C3D4E5F6\","
    "\"monto\":null,\"saldo_final\":null,\"operacion\":null,\"contador\":null,\"recarga_hasta\":null,"
    "\"detalle\":\"operacion retrocede\"}";

void fechas() {
  char iso[26];
  isoDeUnix(0, iso);
  assert(strcmp(iso, "1970-01-01T00:00:00+00:00") == 0);
  isoDeUnix(1795000000, iso);
  // contra gmtime del sistema, en una barrida que incluye años bisiestos y los extremos
  for (uint64_t t = 0; t < 0xFFFFFFFFull; t += 86399 * 37 + 1234) {
    isoDeUnix(static_cast<uint32_t>(t), iso);
    time_t tt = static_cast<time_t>(t);
    struct tm tm;
    gmtime_r(&tt, &tm);
    char esperado[32];
    strftime(esperado, sizeof(esperado), "%Y-%m-%dT%H:%M:%S+00:00", &tm);
    assert(strcmp(iso, esperado) == 0);
    uint32_t vuelta;
    assert(unixDeIso(iso, vuelta) && vuelta == static_cast<uint32_t>(t));
  }
  uint32_t u;
  assert(unixDeIso("2024-02-29T23:59:59+00:00", u) && u == 1709251199u);
  assert(!unixDeIso("2026-13-01T00:00:00+00:00", u) && !unixDeIso("basura", u) && !unixDeIso("1969-12-31T23:59:59+00:00", u));
  printf("  fechas: ISO <-> unix coinciden con gmtime en toda la barrida, bisiestos incluidos OK\n");
}

void formato_y_ida_y_vuelta() {
  sp::Evento e;
  assert(deJson(VIAJE_ANTIGUO, e));
  assert(e.idEvento == 7 && e.tipo == sp::VIAJE && e.idTarjeta == 1234 && e.largoUid == 4 && e.uid[0] == 0x04 && e.uid[3] == 0xC3);
  assert(e.monto == 130 && e.saldoFinal == 870 && e.operacion == 42 && e.contador == 9 && e.recargaHasta == 0);
  char salida[512];
  assert(aJson(e, salida, sizeof(salida)) == strlen(VIAJE_ANTIGUO) && strcmp(salida, VIAJE_ANTIGUO) == 0);  // idéntico al de antes

  assert(deJson(INCIDENCIA_ANTIGUA, e));
  assert(e.tipo == sp::FRAUDE && e.largoUid == 7 && e.contador == sp::SIN_CONTADOR && e.monto == 0 && strcmp(e.detalle, "operacion retrocede") == 0);
  assert(aJson(e, salida, sizeof(salida)) > 0 && strcmp(salida, INCIDENCIA_ANTIGUA) == 0);

  // un viaje con recarga_hasta y contador 0, y un negativo
  sp::Evento v;
  memset(&v, 0, sizeof(v));
  v.idEvento = 100;
  v.tipo = sp::VIAJE;
  v.fecha = 1795000000;
  v.idTarjeta = 5;
  assert(uidDeHex("0102030405060708", v) && v.largoUid == 8);
  v.monto = 130;
  v.saldoFinal = -20;
  v.operacion = 65535;
  v.contador = 0;
  v.recargaHasta = 12;
  assert(aJson(v, salida, sizeof(salida)) > 0);
  sp::Evento w;
  assert(deJson(salida, w));
  assert(w.idEvento == 100 && w.saldoFinal == -20 && w.operacion == 65535 && w.contador == 0 && w.recargaHasta == 12 && w.fecha == 1795000000);
  assert(memcmp(w.uid, v.uid, 8) == 0 && w.largoUid == 8);
  // el evento binario no cambia al pasar por el texto
  uint8_t a[sp::TAM_EVENTO], b[sp::TAM_EVENTO];
  assert(sp::eventoABytes(v, a) && sp::eventoABytes(w, b) && memcmp(a, b, sizeof(a)) == 0);

  // sin tarjeta (UID vacío) y un detalle con comillas y barra
  sp::Evento x;
  memset(&x, 0, sizeof(x));
  x.idEvento = 3;
  x.tipo = sp::INCIDENCIA;
  x.fecha = 1795000000;
  x.contador = sp::SIN_CONTADOR;
  ponerDetalle(x, "dice \"hola\" a\\b");
  assert(aJson(x, salida, sizeof(salida)) > 0);
  sp::Evento y;
  assert(deJson(salida, y) && strcmp(y.detalle, "dice \"hola\" a\\b") == 0 && y.largoUid == 0 && y.contador == sp::SIN_CONTADOR);
  printf("  JSON: igual al de siempre, ida y vuelta sin cambiar un bit y detalle con comillas OK\n");
}

void detalle_y_uid() {
  sp::Evento e;
  memset(&e, 0, sizeof(e));
  ponerDetalle(e, "operación menor que la ya vista: saldo restaurado");
  assert(strlen(e.detalle) == 20 && e.detalle[7] == '?' && e.detalle[8] == '?' && e.detalle[9] == 'n');  // la ó (2 bytes) pasa a '??' y se corta a 20
  ponerDetalle(e, nullptr);
  assert(e.detalle[0] == '\0');
  assert(!uidDeHex("0G", e) && e.largoUid == 0 && !uidDeHex("123", e) && !uidDeHex("00112233445566778899AA", e));
  assert(uidDeHex("", e) && e.largoUid == 0 && uidDeHex("aBcD", e) && e.uid[0] == 0xAB && e.uid[1] == 0xCD);
  printf("  detalle de 20 caracteres ASCII y UID en hexadecimal OK\n");
}

void lineas_malas() {
  sp::Evento e;
  char corta[200];
  // una línea cortada por un corte de energía a mitad no se acepta (en ningún punto de corte)
  const size_t n = strlen(VIAJE_ANTIGUO);
  for (size_t k = 0; k < n; k++) {
    memcpy(corta, VIAJE_ANTIGUO, k);
    corta[k] = '\0';
    assert(!deJson(corta, e));
  }
  assert(!deJson("", e) && !deJson(nullptr, e) && !deJson("{}", e));
  assert(!deJson("{\"id\":0,\"tipo\":\"viaje\",\"fecha\":\"2026-11-21T14:03:09+00:00\"}", e));
  assert(!deJson("{\"id\":1,\"tipo\":\"otro\",\"fecha\":\"2026-11-21T14:03:09+00:00\"}", e));
  assert(!deJson("{\"id\":1,\"tipo\":\"viaje\",\"fecha\":\"mal\"}", e));
  // el texto del detalle no se confunde con un campo
  assert(deJson("{\"id\":9,\"tipo\":\"incidencia\",\"fecha\":\"2026-11-21T14:05:00+00:00\",\"tarjeta_id\":1,\"uid\":\"\","
                "\"monto\":null,\"saldo_final\":null,\"operacion\":null,\"contador\":null,\"recarga_hasta\":null,"
                "\"detalle\":\"x \\\"monto\\\":99\"}", e) && e.monto == 0);
  char pequeno[10];
  assert(aJson(e, pequeno, sizeof(pequeno)) == 0);
  printf("  líneas cortadas en cualquier punto, tipos y fechas inválidos y búfer pequeño se rechazan OK\n");
}

}  // namespace

int main() {
  fechas();
  formato_y_ida_y_vuelta();
  detalle_y_uid();
  lineas_malas();
  printf("M3-01 eventos <-> texto: formato JSON v1 sin cambios, ida y vuelta y migración de /eventos.txt OK\n");
}
