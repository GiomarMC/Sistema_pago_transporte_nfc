#include "Claves424.h"

#include <mbedtls/aes.h>
#include <mbedtls/platform_util.h>
#include <string.h>

namespace tt {

namespace {

constexpr uint8_t SISTEMA[6] = {'S', 'u', 'b', 'e', 'P', 'e'};
constexpr uint8_t DIV_AES128 = 0x01;  // constante de diversificación de AES-128 (AN10922 §2.2)
constexpr uint8_t RESERVADO = 0x00;
constexpr size_t BLOQUE = 16;

// Desplaza 1 bit a la izquierda y, si se cayó un 1, aplica Rb = 87h (RFC 4493 §2.3)
void subclave(const uint8_t entrada[BLOQUE], uint8_t salida[BLOQUE]) {
  const uint8_t acarreo = entrada[0] >> 7;
  for (size_t i = 0; i < BLOQUE - 1; i++) salida[i] = uint8_t(entrada[i] << 1) | (entrada[i + 1] >> 7);
  salida[BLOQUE - 1] = uint8_t(entrada[BLOQUE - 1] << 1) ^ (acarreo ? 0x87 : 0x00);
}

bool ranuraValida(uint8_t ranura) { return ranura == 0 || ranura == 1 || ranura == 2 || ranura == 4; }

}  // namespace

const char *describir(ErrorClave e) {
  switch (e) {
    case ErrorClave::OK: return "ok";
    case ErrorClave::MAESTRA: return "la maestra AES-128 tiene 16 bytes";
    case ErrorClave::UID: return "el UID tiene 7 bytes";
    case ErrorClave::RANURA: return "se diversifican las claves 0, 1, 2 y 4 (K3 no)";
    case ErrorClave::VERSION: return "la versión va de 01h a FFh (00h es la clave de fábrica)";
    case ErrorClave::LARGO_M: return "M debe tener de 16 a 31 bytes";
  }
  return "?";
}

void cmacAes128(const uint8_t clave[TAM_CLAVE_AES], const uint8_t *datos, size_t largo,
                uint8_t mac[TAM_CLAVE_AES]) {
  mbedtls_aes_context aes;
  mbedtls_aes_init(&aes);
  mbedtls_aes_setkey_enc(&aes, clave, 128);

  uint8_t l[BLOQUE] = {0}, k1[BLOQUE], k2[BLOQUE];
  mbedtls_aes_crypt_ecb(&aes, MBEDTLS_AES_ENCRYPT, l, l);  // L = AES(K, 0^128)
  subclave(l, k1);
  subclave(k1, k2);

  // n bloques; el último va con K1 si está completo, o relleno con 80h 00… y K2 si no (o si no hay datos)
  const size_t n = largo == 0 ? 1 : (largo + BLOQUE - 1) / BLOQUE;
  const bool completo = largo != 0 && largo % BLOQUE == 0;
  const size_t enUltimo = largo - (n - 1) * BLOQUE;
  uint8_t ultimo[BLOQUE] = {0};
  if (enUltimo) memcpy(ultimo, datos + (n - 1) * BLOQUE, enUltimo);
  if (!completo) ultimo[enUltimo] = 0x80;
  const uint8_t *k = completo ? k1 : k2;
  for (size_t i = 0; i < BLOQUE; i++) ultimo[i] ^= k[i];

  uint8_t x[BLOQUE] = {0};
  for (size_t b = 0; b + 1 < n; b++) {
    for (size_t i = 0; i < BLOQUE; i++) x[i] ^= datos[b * BLOQUE + i];
    mbedtls_aes_crypt_ecb(&aes, MBEDTLS_AES_ENCRYPT, x, x);
  }
  for (size_t i = 0; i < BLOQUE; i++) x[i] ^= ultimo[i];
  mbedtls_aes_crypt_ecb(&aes, MBEDTLS_AES_ENCRYPT, x, mac);

  mbedtls_aes_free(&aes);
  mbedtls_platform_zeroize(l, sizeof(l));
  mbedtls_platform_zeroize(k1, sizeof(k1));
  mbedtls_platform_zeroize(k2, sizeof(k2));
  mbedtls_platform_zeroize(ultimo, sizeof(ultimo));
  mbedtls_platform_zeroize(x, sizeof(x));
}

ErrorClave entradaM(const uint8_t *uid, size_t lenUid, uint8_t ranura, unsigned version, uint8_t m[TAM_M]) {
  if (uid == nullptr || lenUid != TAM_UID_424) return ErrorClave::UID;
  if (!ranuraValida(ranura)) return ErrorClave::RANURA;
  if (version < 0x01 || version > 0xFF) return ErrorClave::VERSION;
  memcpy(m, uid, TAM_UID_424);
  memcpy(m + TAM_UID_424, SISTEMA, sizeof(SISTEMA));
  m[13] = ranura;
  m[14] = uint8_t(version);
  m[15] = RESERVADO;
  return ErrorClave::OK;
}

ErrorClave an10922Aes128(const uint8_t maestra[TAM_CLAVE_AES], const uint8_t *m, size_t lenM,
                         uint8_t salida[TAM_CLAVE_AES]) {
  if (lenM < TAM_M || lenM > 31) return ErrorClave::LARGO_M;
  uint8_t d[32];
  d[0] = DIV_AES128;
  memcpy(d + 1, m, lenM);
  cmacAes128(maestra, d, 1 + lenM, salida);
  mbedtls_platform_zeroize(d, sizeof(d));
  return ErrorClave::OK;
}

ErrorClave diversificarAes128(const uint8_t *maestra, size_t lenMaestra, const uint8_t *uid, size_t lenUid,
                              uint8_t ranura, unsigned version, uint8_t salida[TAM_CLAVE_AES]) {
  memset(salida, 0, TAM_CLAVE_AES);
  if (maestra == nullptr || lenMaestra != TAM_CLAVE_AES) return ErrorClave::MAESTRA;
  uint8_t m[TAM_M];
  ErrorClave e = entradaM(uid, lenUid, ranura, version, m);
  if (e == ErrorClave::OK) e = an10922Aes128(maestra, m, TAM_M, salida);
  mbedtls_platform_zeroize(m, sizeof(m));
  return e;
}

}  // namespace tt
