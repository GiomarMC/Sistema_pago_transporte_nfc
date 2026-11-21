// Claves secretas del validador en la NVS de la ESP32 (M2-02, para el modo sync=v2): la clave HMAC con que firma sus
// paquetes y la contraseña MQTT. NUNCA van a la microSD (que se puede sacar y copiar) ni al registro.
//
// Se cargan por el cable USB con puente_nfc.py --configurar --clave-hmac ... --mqtt-clave ...; quien recibe la
// configuración (main.cpp) las separa de lo que se guarda en config.txt y las pasa a esta clase.
//
// OJO: la NVS de la ESP32 no está cifrada salvo que se active el cifrado de flash (M2-03, #46, aún sin hacer): quien
// tenga la placa en la mano puede leerlas.
#pragma once

#include <Arduino.h>

class Secretos {
 public:
  static constexpr size_t TAM_CLAVE_HMAC = 32;

  // false si no hay una clave HMAC guardada
  bool claveHmac(uint8_t salida[TAM_CLAVE_HMAC]);
  bool guardarClaveHmac(const uint8_t clave[TAM_CLAVE_HMAC]);
  // Desde 64 caracteres hexadecimales; false si no lo son
  bool guardarClaveHmacHex(const String &hex);

  String claveMqtt();  // "" si no hay
  bool guardarClaveMqtt(const String &clave);
  bool tieneClaveMqtt() { return claveMqtt().length() > 0; }
};
