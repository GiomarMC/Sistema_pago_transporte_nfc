// Comprueba en la ESP32 que el formato y las firmas en C++ coinciden byte a byte
// con la implementación Python (vectores.h). Ejecutar:  pio test -e validador
#include <Arduino.h>
#include <Formato.h>
#include <string.h>
#include <unity.h>

#include "vectores.h"

using namespace vectores;

void test_clave_firma() {
  uint8_t k[32];
  tt::claveFirma(MAESTRA, UID, 7, k);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(CLAVE_FIRMA, k, 32);
}

void test_contrasena_y_pack() {
  uint8_t pwd[4], pack[2];
  tt::contrasena(MAESTRA, UID, 7, pwd, pack);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(PWD, pwd, 4);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(PACK, pack, 2);
}

void test_decodifica_area_python() {
  tt::EstadoTarjeta e;
  TEST_ASSERT_EQUAL(int(tt::Error::OK), int(tt::decodificar(AREA, UID, 7, CLAVE_FIRMA, e)));
  TEST_ASSERT_EQUAL_UINT32(4, e.cab.idTarjeta);
  TEST_ASSERT_EQUAL_UINT32(1, e.cab.idCuenta);
  TEST_ASSERT_EQUAL_UINT8(2, e.cab.tarifa);
  TEST_ASSERT_EQUAL_UINT16(DIAS, e.cab.dias);
  TEST_ASSERT_EQUAL(1, e.vigente);
  TEST_ASSERT_EQUAL_INT32(1110, e.registro().saldo);
  TEST_ASSERT_EQUAL_UINT16(5, e.registro().operacion);
  TEST_ASSERT_EQUAL_UINT16(101, e.registro().validador);
  TEST_ASSERT_EQUAL_UINT32(2, e.registro().recarga);
  TEST_ASSERT_EQUAL_INT32(1190, e.reg[0].saldo);
}

void test_recodifica_igual_que_python() {
  tt::EstadoTarjeta e;
  tt::decodificar(AREA, UID, 7, CLAVE_FIRMA, e);
  uint8_t cab[24], ra[20], rb[20];
  tt::codificarCabecera(e.cab, UID, 7, CLAVE_FIRMA, cab);
  tt::codificarRegistro(e.reg[0], e.cab.idTarjeta, UID, 7, CLAVE_FIRMA, ra);
  tt::codificarRegistro(e.reg[1], e.cab.idTarjeta, UID, 7, CLAVE_FIRMA, rb);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(AREA, cab, 24);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(AREA + 32, ra, 20);
  TEST_ASSERT_EQUAL_HEX8_ARRAY(AREA + 52, rb, 20);
}

void test_registro_cortado_usa_el_anterior() {
  uint8_t area[72];
  memcpy(area, AREA, 72);
  area[60] ^= 0x01;  // escritura interrumpida en el registro B
  tt::EstadoTarjeta e;
  TEST_ASSERT_EQUAL(int(tt::Error::OK), int(tt::decodificar(area, UID, 7, CLAVE_FIRMA, e)));
  TEST_ASSERT_EQUAL(0, e.vigente);
  TEST_ASSERT_EQUAL_INT32(1190, e.registro().saldo);
}

void test_saldo_alterado_se_detecta() {
  uint8_t area[72];
  memcpy(area, AREA, 72);
  area[35] ^= 0xFF;  // saldo de A
  area[55] ^= 0xFF;  // saldo de B
  tt::EstadoTarjeta e;
  TEST_ASSERT_EQUAL(int(tt::Error::SIN_REGISTRO_VALIDO),
                    int(tt::decodificar(area, UID, 7, CLAVE_FIRMA, e)));
}

void test_datos_copiados_a_otra_tarjeta() {
  const uint8_t otroUid[7] = {0x04, 0x12, 0xBD, 0x11, 0xCE, 0x2A, 0x81};
  tt::EstadoTarjeta e;
  TEST_ASSERT_EQUAL(int(tt::Error::FIRMA_CABECERA),
                    int(tt::decodificar(AREA, otroUid, 7, CLAVE_FIRMA, e)));
}

void setup() {
  delay(2000);
  UNITY_BEGIN();
  RUN_TEST(test_clave_firma);
  RUN_TEST(test_contrasena_y_pack);
  RUN_TEST(test_decodifica_area_python);
  RUN_TEST(test_recodifica_igual_que_python);
  RUN_TEST(test_registro_cortado_usa_el_anterior);
  RUN_TEST(test_saldo_alterado_se_detecta);
  RUN_TEST(test_datos_copiados_a_otra_tarjeta);
  UNITY_END();
}

void loop() {}
