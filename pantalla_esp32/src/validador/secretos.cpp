#include "secretos.h"

#include <Preferences.h>

namespace {
constexpr const char *ESPACIO = "subepe";
constexpr const char *K_HMAC = "k_hmac", *K_MQTT = "k_mqtt";
}  // namespace

bool Secretos::claveHmac(uint8_t salida[TAM_CLAVE_HMAC]) {
  Preferences p;
  if (!p.begin(ESPACIO, true)) return false;
  const size_t n = p.getBytes(K_HMAC, salida, TAM_CLAVE_HMAC);
  p.end();
  return n == TAM_CLAVE_HMAC;
}

bool Secretos::guardarClaveHmac(const uint8_t clave[TAM_CLAVE_HMAC]) {
  Preferences p;
  if (!p.begin(ESPACIO, false)) return false;
  const bool ok = p.putBytes(K_HMAC, clave, TAM_CLAVE_HMAC) == TAM_CLAVE_HMAC;
  p.end();
  return ok;
}

bool Secretos::guardarClaveHmacHex(const String &hex) {
  if (hex.length() != 2 * TAM_CLAVE_HMAC) return false;
  uint8_t k[TAM_CLAVE_HMAC];
  for (size_t i = 0; i < TAM_CLAVE_HMAC; i++) {
    char b[3] = {hex[2 * i], hex[2 * i + 1], 0};
    char *fin;
    k[i] = static_cast<uint8_t>(strtoul(b, &fin, 16));
    if (*fin) return false;
  }
  return guardarClaveHmac(k);
}

String Secretos::claveMqtt() {
  Preferences p;
  if (!p.begin(ESPACIO, true)) return "";
  String s = p.getString(K_MQTT, "");
  p.end();
  return s;
}

bool Secretos::guardarClaveMqtt(const String &clave) {
  Preferences p;
  if (!p.begin(ESPACIO, false)) return false;
  const bool ok = p.putString(K_MQTT, clave) == clave.length();
  p.end();
  return ok;
}
