// Comprueba en la ESP32 que la diversificación AN10922 en C++ (M2-04, #47) da las mismas claves que Python
// (M1-05, #39), con los vectores de docs/contratos/vectores_an10922.json. Ejecutar:  pio test -e validador
// El mismo recorrido sin placa: test_host/test_claves424.cpp (sh test_host/run.sh).
#include <Arduino.h>
#include <Claves424.h>
#include <string.h>
#include <unity.h>

#include "vectores.h"

using namespace vectores;

namespace {

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

void test_cmac_rfc4493() {
  for (size_t i = 0; i < N_CMACS; i++) {
    uint8_t mac[16];
    tt::cmacAes128(CMACS[i].clave, CMACS[i].mensaje, CMACS[i].largo, mac);
    TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(CMACS[i].mac, mac, 16, "RFC 4493");
  }
}

void test_vector_oficial_an10922() {
  for (size_t i = 0; i < N_OFICIALES; i++) {
    uint8_t k[16];
    TEST_ASSERT_EQUAL_STRING("OK", nombre(tt::an10922Aes128(OFICIALES[i].maestra, OFICIALES[i].m,
                                                            OFICIALES[i].largoM, k)));
    TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(OFICIALES[i].clave, k, 16, OFICIALES[i].fuente);
  }
}

void test_vectores_propios() {
  for (size_t i = 0; i < N_PROPIOS; i++) {
    const Propio &p = PROPIOS[i];
    uint8_t m[16], k[16];
    TEST_ASSERT_EQUAL_STRING_MESSAGE("OK", nombre(tt::entradaM(p.uid, 7, p.ranura, p.version, m)), p.nombre);
    TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(p.m, m, 16, p.nombre);
    TEST_ASSERT_EQUAL_STRING_MESSAGE(
        "OK", nombre(tt::diversificarAes128(p.maestra, 16, p.uid, 7, p.ranura, p.version, k)), p.nombre);
    TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(p.clave, k, 16, p.nombre);
  }
}

void test_invalidos_con_su_error() {
  const uint8_t ceros[16] = {0};
  for (size_t i = 0; i < N_INVALIDOS; i++) {
    const Invalido &x = INVALIDOS[i];
    uint8_t k[16];
    memset(k, 0xAA, sizeof(k));
    TEST_ASSERT_EQUAL_STRING_MESSAGE(
        x.error, nombre(tt::diversificarAes128(x.maestra, x.largoMaestra, x.uid, x.largoUid, x.ranura, x.version, k)),
        x.motivo);
    TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(ceros, k, 16, x.motivo);  // nada a medias en la salida
  }
}

void test_largo_de_m() {
  const uint8_t m[32] = {0};
  uint8_t k[16];
  TEST_ASSERT_EQUAL_STRING("LARGO_M", nombre(tt::an10922Aes128(PROPIOS[0].maestra, m, 15, k)));
  TEST_ASSERT_EQUAL_STRING("LARGO_M", nombre(tt::an10922Aes128(PROPIOS[0].maestra, m, 32, k)));
  TEST_ASSERT_EQUAL_STRING("OK", nombre(tt::an10922Aes128(PROPIOS[0].maestra, m, 31, k)));
}

void test_tiempo_diversificar() {
  // Informativo: cuánto cuesta derivar una clave en la placa (el validador derivará K1 y K2 en cada lectura de 424)
  uint8_t k[16];
  const int veces = 100;
  const uint32_t t0 = micros();
  for (int i = 0; i < veces; i++)
    tt::diversificarAes128(PROPIOS[0].maestra, 16, PROPIOS[0].uid, 7, 1, 1, k);
  const uint32_t us = (micros() - t0) / veces;
  char msg[48];
  snprintf(msg, sizeof(msg), "diversificarAes128: %u us por clave", unsigned(us));
  TEST_MESSAGE(msg);
  TEST_ASSERT_LESS_THAN_UINT32(5000, us);
}

void setup() {
  delay(2000);
  UNITY_BEGIN();
  RUN_TEST(test_cmac_rfc4493);
  RUN_TEST(test_vector_oficial_an10922);
  RUN_TEST(test_vectores_propios);
  RUN_TEST(test_invalidos_con_su_error);
  RUN_TEST(test_largo_de_m);
  RUN_TEST(test_tiempo_diversificar);
  UNITY_END();
}

void loop() {}
