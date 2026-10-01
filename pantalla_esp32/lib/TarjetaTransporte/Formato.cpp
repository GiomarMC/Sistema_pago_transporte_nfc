#include "Formato.h"

#include <mbedtls/md.h>
#include <string.h>

namespace tt {

namespace {

void ponerU16(uint8_t *p, uint16_t v) { p[0] = v >> 8; p[1] = v; }
void ponerU32(uint8_t *p, uint32_t v) { p[0] = v >> 24; p[1] = v >> 16; p[2] = v >> 8; p[3] = v; }
uint16_t leerU16(const uint8_t *p) { return (uint16_t(p[0]) << 8) | p[1]; }
uint32_t leerU32(const uint8_t *p) {
  return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) | p[3];
}

// HMAC(clave, etiqueta + uid + extra + cuerpo), truncado a `largo` bytes
void firma(const uint8_t clave[32], const char *etiqueta, const uint8_t *uid, size_t lenUid,
           const uint8_t *extra, size_t lenExtra, const uint8_t *cuerpo, size_t lenCuerpo,
           uint8_t *salida, size_t largo) {
  uint8_t buf[3 + 10 + 4 + 16];
  size_t n = 0;
  memcpy(buf + n, etiqueta, 3); n += 3;
  memcpy(buf + n, uid, lenUid); n += lenUid;
  if (lenExtra) { memcpy(buf + n, extra, lenExtra); n += lenExtra; }
  memcpy(buf + n, cuerpo, lenCuerpo); n += lenCuerpo;
  uint8_t completo[32];
  hmacSha256(clave, 32, buf, n, completo);
  memcpy(salida, completo, largo);
}

// Comparación en tiempo constante (como hmac.compare_digest)
bool iguales(const uint8_t *a, const uint8_t *b, size_t n) {
  uint8_t d = 0;
  for (size_t i = 0; i < n; i++) d |= a[i] ^ b[i];
  return d == 0;
}

void cuerpoRegistro(const Registro &r, uint8_t c[16]) {
  ponerU32(c, (uint32_t)r.saldo);
  ponerU16(c + 4, r.operacion);
  ponerU32(c + 6, r.fecha);
  ponerU16(c + 10, r.validador);
  ponerU32(c + 12, r.recarga);
}

}  // namespace

const char *describir(Error e) {
  switch (e) {
    case Error::OK: return "ok";
    case Error::SIN_MARCA: return "la tarjeta no está emitida (sin marca 'TR')";
    case Error::VERSION_NO_SOPORTADA: return "versión de formato no soportada";
    case Error::FIRMA_CABECERA: return "firma de la cabecera inválida";
    case Error::SIN_REGISTRO_VALIDO: return "ningún registro de saldo válido";
  }
  return "?";
}

void hmacSha256(const uint8_t *clave, size_t lenClave, const uint8_t *datos, size_t lenDatos,
                uint8_t salida[32]) {
  mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), clave, lenClave, datos,
                  lenDatos, salida);
}

void claveFirma(const uint8_t maestra[32], const uint8_t *uid, size_t lenUid, uint8_t salida[32]) {
  uint8_t buf[3 + 10];
  memcpy(buf, "MAC", 3);
  memcpy(buf + 3, uid, lenUid);
  hmacSha256(maestra, 32, buf, 3 + lenUid, salida);
}

void contrasena(const uint8_t maestra[32], const uint8_t *uid, size_t lenUid, uint8_t pwd[4],
                uint8_t pack[2]) {
  uint8_t buf[3 + 10], d[32];
  memcpy(buf, "PWD", 3);
  memcpy(buf + 3, uid, lenUid);
  hmacSha256(maestra, 32, buf, 3 + lenUid, d);
  memcpy(pwd, d, 4);
  memcpy(pack, d + 4, 2);
}

void codificarCabecera(const Cabecera &c, const uint8_t *uid, size_t lenUid,
                       const uint8_t clave[32], uint8_t salida[TAM_CABECERA]) {
  uint8_t *b = salida;
  b[0] = 'T'; b[1] = 'R';
  b[2] = c.version; b[3] = c.estado;
  ponerU32(b + 4, c.idTarjeta);
  ponerU32(b + 8, c.idCuenta);
  b[12] = c.tarifa; b[13] = 0;
  ponerU16(b + 14, c.dias);
  firma(clave, "CAB", uid, lenUid, nullptr, 0, b, 16, b + 16, 8);
}

void codificarRegistro(const Registro &r, uint32_t idTarjeta, const uint8_t *uid, size_t lenUid,
                       const uint8_t clave[32], uint8_t salida[TAM_REGISTRO]) {
  uint8_t id[4];
  ponerU32(id, idTarjeta);
  cuerpoRegistro(r, salida);
  firma(clave, "REG", uid, lenUid, id, 4, salida, 16, salida + 16, 4);
}

Error decodificar(const uint8_t area[TAM_AREA], const uint8_t *uid, size_t lenUid,
                  const uint8_t clave[32], EstadoTarjeta &s) {
  const uint8_t *c = area;
  if (c[0] != 'T' || c[1] != 'R') return Error::SIN_MARCA;
  s.cab.version = c[2];
  s.cab.estado = c[3];
  s.cab.idTarjeta = leerU32(c + 4);
  s.cab.idCuenta = leerU32(c + 8);
  s.cab.tarifa = c[12];
  s.cab.dias = leerU16(c + 14);
  if (s.cab.version != VERSION) return Error::VERSION_NO_SOPORTADA;
  uint8_t esperada[8];
  firma(clave, "CAB", uid, lenUid, nullptr, 0, c, 16, esperada, 8);
  if (!iguales(esperada, c + 16, 8)) return Error::FIRMA_CABECERA;

  uint8_t id[4];
  ponerU32(id, s.cab.idTarjeta);
  s.vigente = -1;
  for (int i = 0; i < 2; i++) {
    const uint8_t *r = area + (PAG_REGISTRO[i] - PAG_INICIO) * 4;
    uint8_t f[4];
    firma(clave, "REG", uid, lenUid, id, 4, r, 16, f, 4);
    s.valido[i] = iguales(f, r + 16, 4);
    if (!s.valido[i]) continue;
    s.reg[i].saldo = (int32_t)leerU32(r);
    s.reg[i].operacion = leerU16(r + 4);
    s.reg[i].fecha = leerU32(r + 6);
    s.reg[i].validador = leerU16(r + 10);
    s.reg[i].recarga = leerU32(r + 12);
    // Igual que max() de Python: con empate gana el primero
    if (s.vigente < 0 || s.reg[i].operacion > s.reg[s.vigente].operacion) s.vigente = i;
  }
  return s.vigente < 0 ? Error::SIN_REGISTRO_VALIDO : Error::OK;
}

}  // namespace tt
