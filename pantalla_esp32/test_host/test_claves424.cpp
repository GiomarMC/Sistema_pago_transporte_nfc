// M2-04 (#47): diversificación AN10922 en el ordenador, con los mismos vectores que la prueba de la placa
// (test/test_claves424). No sustituye a esa prueba: el mbedtls del ordenador no es el del núcleo de la ESP32.
#include <stdio.h>
#include <string.h>

#include "Claves424.h"
#include "vectores.h"

using namespace vectores;

namespace {

int fallos = 0;

void comprobar(bool bien, const char *que, const char *caso) {
  if (!bien) {
    fprintf(stderr, "FALLA %s: %s\n", que, caso);
    fallos++;
  }
}

const char *nombre(tt::ErrorClave e) {
  switch (e) {
    case tt::ErrorClave::OK: return "OK";
    case tt::ErrorClave::MAESTRA: return "MAESTRA";
    case tt::ErrorClave::UID: return "UID";
    case tt::ErrorClave::RANURA: return "RANURA";
    case tt::ErrorClave::VERSION: return "VERSION";
    case tt::ErrorClave::LARGO_M: return "LARGO_M";
  }
  return "?";
}

}  // namespace

int main() {
  for (size_t i = 0; i < N_CMACS; i++) {
    uint8_t mac[16];
    tt::cmacAes128(CMACS[i].clave, CMACS[i].mensaje, CMACS[i].largo, mac);
    comprobar(memcmp(mac, CMACS[i].mac, 16) == 0, "cmac", "RFC 4493");
  }
  for (size_t i = 0; i < N_OFICIALES; i++) {
    uint8_t k[16];
    const tt::ErrorClave e = tt::an10922Aes128(OFICIALES[i].maestra, OFICIALES[i].m, OFICIALES[i].largoM, k);
    comprobar(e == tt::ErrorClave::OK && memcmp(k, OFICIALES[i].clave, 16) == 0, "oficial", OFICIALES[i].fuente);
  }
  for (size_t i = 0; i < N_PROPIOS; i++) {
    const Propio &p = PROPIOS[i];
    uint8_t m[16], k[16];
    comprobar(tt::entradaM(p.uid, 7, p.ranura, p.version, m) == tt::ErrorClave::OK && memcmp(m, p.m, 16) == 0,
              "entradaM", p.nombre);
    comprobar(tt::diversificarAes128(p.maestra, 16, p.uid, 7, p.ranura, p.version, k) == tt::ErrorClave::OK &&
                  memcmp(k, p.clave, 16) == 0,
              "diversificar", p.nombre);
  }
  const uint8_t ceros[16] = {0};
  for (size_t i = 0; i < N_INVALIDOS; i++) {
    const Invalido &x = INVALIDOS[i];
    uint8_t k[16];
    memset(k, 0xAA, sizeof(k));
    const tt::ErrorClave e = tt::diversificarAes128(x.maestra, x.largoMaestra, x.uid, x.largoUid, x.ranura,
                                                    x.version, k);
    comprobar(strcmp(nombre(e), x.error) == 0, "error esperado", x.motivo);
    comprobar(memcmp(k, ceros, 16) == 0, "salida a ceros", x.motivo);
  }
  const uint8_t m[32] = {0};
  uint8_t k[16];
  comprobar(tt::an10922Aes128(PROPIOS[0].maestra, m, 15, k) == tt::ErrorClave::LARGO_M, "largo", "M de 15 bytes");
  comprobar(tt::an10922Aes128(PROPIOS[0].maestra, m, 32, k) == tt::ErrorClave::LARGO_M, "largo", "M de 32 bytes");
  comprobar(tt::an10922Aes128(PROPIOS[0].maestra, m, 31, k) == tt::ErrorClave::OK, "largo", "M de 31 bytes");

  if (fallos) return 1;
  printf("claves424: %zu CMAC, %zu oficiales, %zu propios y %zu inválidos OK\n", N_CMACS, N_OFICIALES, N_PROPIOS,
         N_INVALIDOS);
  return 0;
}
