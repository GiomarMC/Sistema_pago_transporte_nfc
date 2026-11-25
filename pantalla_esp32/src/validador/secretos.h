// Claves secretas del validador en la NVS de la ESP32: la clave maestra de las tarjetas (M2-01, #44), la clave HMAC con
// que firma sus paquetes y la contraseña MQTT (M2-02, para el modo sync=v2). NUNCA van a la microSD (que se puede sacar
// y copiar) ni al registro.
//
// Se cargan por el cable USB con puente_nfc.py --configurar [--clave-hmac ... --mqtt-clave ...] (la clave maestra va
// siempre); quien recibe la
// configuración (main.cpp) las separa de lo que se guarda en config.txt y las pasa a esta clase.
//
// OJO: la NVS de la ESP32 no está cifrada salvo que se active el cifrado de flash (M2-03, #46, aún sin hacer): quien
// tenga la placa en la mano puede leerlas.
#pragma once

#include <Arduino.h>

class Secretos {
 public:
  static constexpr size_t TAM_CLAVE_HMAC = 32;
  static constexpr size_t TAM_CLAVE_MAESTRA = 32;

  // La clave maestra de la que salen la contraseña y la clave de firma de cada tarjeta. false si no hay
  bool claveMaestra(uint8_t salida[TAM_CLAVE_MAESTRA]);
  bool guardarClaveMaestra(const uint8_t clave[TAM_CLAVE_MAESTRA]);

  // false si no hay una clave HMAC guardada
  bool claveHmac(uint8_t salida[TAM_CLAVE_HMAC]);
  bool guardarClaveHmac(const uint8_t clave[TAM_CLAVE_HMAC]);
  // Desde 64 caracteres hexadecimales; false si no lo son
  bool guardarClaveHmacHex(const String &hex);

  // 64 caracteres hexadecimales a 32 bytes; false si no lo son
  static bool deHex32(const String &hex, uint8_t salida[32]);

  String claveMqtt();  // "" si no hay
  bool guardarClaveMqtt(const String &clave);
  bool tieneClaveMqtt() { return claveMqtt().length() > 0; }
};
