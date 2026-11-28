// Diversificación de claves AES-128 de NXP AN10922 para la NTAG 424 DNA (M2-04, #47).
// Traducción exacta de transporte/tarjeta/an10922.py (M1-05, #39): con la misma maestra y el mismo UID, el firmware
// y el PC deben obtener la misma clave, o el validador no podrá autenticar las tarjetas emitidas en el PC.
//
//   M = UID(7) || "SubePe" || ranura(1) || versión(1) || 00     (16 bytes)
//   K = AES-CMAC(maestra de la ranura, 01h || M)                (CMAC estándar, RFC 4493)
//
// - Ranuras 0, 1, 2 y 4. K3 no se diversifica (AN12196 §3.4.2).
// - Versión 01h-FFh, la misma que el KeyVer grabado con ChangeKey; 00h es la clave de fábrica.
// - UID de 7 bytes en orden de anticolisión (UID0 = 04h primero). Maestra AES-128 de 16 bytes.
//
// Funciones puras: no leen claves ni configuración. Solo dependen de mbedtls (sin Arduino), así que también
// compilan en el ordenador (test_host/test_claves424.cpp).
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace tt {

constexpr size_t TAM_CLAVE_AES = 16;
constexpr size_t TAM_UID_424 = 7;
constexpr size_t TAM_M = 16;

enum class ErrorClave { OK, MAESTRA, UID, RANURA, VERSION, LARGO_M };
const char *describir(ErrorClave e);

// AES-CMAC de 16 bytes (RFC 4493) sobre mbedtls_aes_crypt_ecb. El núcleo Arduino-ESP32 2.0.17 no trae
// mbedtls_cipher_cmac (MBEDTLS_CMAC_C desactivado). También la usará M1-07 (#41).
void cmacAes128(const uint8_t clave[TAM_CLAVE_AES], const uint8_t *datos, size_t largo,
                uint8_t mac[TAM_CLAVE_AES]);

// Los 16 bytes de M del contrato. `version` es más ancho que un byte solo para poder rechazar valores > FFh.
ErrorClave entradaM(const uint8_t *uid, size_t lenUid, uint8_t ranura, unsigned version, uint8_t m[TAM_M]);

// AN10922 §2.2 para AES-128 con 16 <= |M| <= 31, donde equivale al CMAC estándar de 01h || M.
ErrorClave an10922Aes128(const uint8_t maestra[TAM_CLAVE_AES], const uint8_t *m, size_t lenM,
                         uint8_t salida[TAM_CLAVE_AES]);

// Clave de la ranura `ranura` de la tarjeta `uid`. Si devuelve un error, `salida` queda a ceros.
ErrorClave diversificarAes128(const uint8_t *maestra, size_t lenMaestra, const uint8_t *uid, size_t lenUid,
                              uint8_t ranura, unsigned version, uint8_t salida[TAM_CLAVE_AES]);

}  // namespace tt
