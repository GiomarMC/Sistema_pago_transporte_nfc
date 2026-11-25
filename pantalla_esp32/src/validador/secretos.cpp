#include "secretos.h"

#include <Preferences.h>

namespace {
constexpr const char *ESPACIO = "subepe";
constexpr const char *K_HMAC = "k_hmac", *K_MQTT = "k_mqtt", *K_MAESTRA = "k_maestra";

bool leerBytes(const char *clave, uint8_t *salida, size_t n) {
  Preferences p;
  if (!p.begin(ESPACIO, true)) return false;
  const size_t leidos = p.getBytes(clave, salida, n);
  p.end();
  return leidos == n;
}

bool guardarBytes(const char *clave, const uint8_t *datos, size_t n) {
  Preferences p;
  if (!p.begin(ESPACIO, false)) return false;
  const bool ok = p.putBytes(clave, datos, n) == n;
  p.end();
  return ok;
}
}  // namespace

bool Secretos::deHex32(const String &hex, uint8_t salida[32]) {
  if (hex.length() != 64) return false;
  for (size_t i = 0; i < 32; i++) {
    char b[3] = {hex[2 * i], hex[2 * i + 1], 0};
    char *fin;
    salida[i] = static_cast<uint8_t>(strtoul(b, &fin, 16));
    if (*fin) return false;
  }
  return true;
}

bool Secretos::claveMaestra(uint8_t salida[TAM_CLAVE_MAESTRA]) { return leerBytes(K_MAESTRA, salida, TAM_CLAVE_MAESTRA); }

bool Secretos::guardarClaveMaestra(const uint8_t clave[TAM_CLAVE_MAESTRA]) {
  return guardarBytes(K_MAESTRA, clave, TAM_CLAVE_MAESTRA);
}

bool Secretos::claveHmac(uint8_t salida[TAM_CLAVE_HMAC]) { return leerBytes(K_HMAC, salida, TAM_CLAVE_HMAC); }

bool Secretos::guardarClaveHmac(const uint8_t clave[TAM_CLAVE_HMAC]) { return guardarBytes(K_HMAC, clave, TAM_CLAVE_HMAC); }

bool Secretos::guardarClaveHmacHex(const String &hex) {
  uint8_t k[TAM_CLAVE_HMAC];
  return deHex32(hex, k) && guardarClaveHmac(k);
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
