#include "LectorPuente.h"

namespace {

String aHex(const uint8_t *d, size_t n) {
  static const char *H = "0123456789ABCDEF";
  String s;
  s.reserve(n * 2);
  for (size_t i = 0; i < n; i++) {
    s += H[d[i] >> 4];
    s += H[d[i] & 0x0F];
  }
  return s;
}

int nibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return -1;
}

bool deHex(const String &s, uint8_t *salida, size_t &n, size_t max) {
  if (s.length() % 2 || s.length() / 2 > max) return false;
  n = s.length() / 2;
  for (size_t i = 0; i < n; i++) {
    int a = nibble(s[2 * i]), b = nibble(s[2 * i + 1]);
    if (a < 0 || b < 0) return false;
    salida[i] = (a << 4) | b;
  }
  return true;
}

}  // namespace

bool LectorPuente::pedir(const String &peticion, String &respuesta, uint32_t espera) {
  while (s_.available()) s_.read();  // descarta restos de respuestas anteriores
  numero_++;
  const String prefijo = "<" + String(numero_) + " ";
  s_.print('>');
  s_.print(numero_);
  s_.print(' ');
  s_.println(peticion);
  String linea;
  uint32_t limite = millis() + espera;
  while ((int32_t)(millis() - limite) < 0) {
    while (s_.available()) {
      char c = s_.read();
      if (c == '\n') {
        linea.trim();
        // Solo vale la respuesta a ESTA petición; las demás se ignoran
        if (linea.startsWith(prefijo)) {
          respuesta = linea.substring(prefijo.length());
          conectado_ = true;
          return true;
        }
        linea = "";
      } else if (c != '\r' && linea.length() < 800) {
        linea += c;
      }
    }
    delay(1);
  }
  conectado_ = false;
  return false;
}

bool LectorPuente::saludar(String &config) {
  String r;
  if (!pedir("HOLA", r, 1500)) return false;
  config = r.startsWith("CFG ") ? r.substring(4) : "";
  return r.startsWith("OK") || r.startsWith("CFG");
}

bool LectorPuente::esperarTarjeta(uint32_t ms, uint8_t uid[10], size_t &lenUid) {
  String r;
  if (!pedir("ESPERA " + String(ms), r, ms + 3000)) return false;
  if (!r.startsWith("OK ")) return false;
  return deHex(r.substring(3), uid, lenUid, 10);
}

bool LectorPuente::comando(const uint8_t *cmd, size_t lenCmd, uint8_t *resp, size_t &lenResp) {
  String r;
  size_t max = lenResp;
  lenResp = 0;
  if (!pedir("CMD " + aHex(cmd, lenCmd), r)) return false;
  if (r == "OK") return true;
  if (!r.startsWith("OK ")) return false;
  return deHex(r.substring(3), resp, lenResp, max);
}

bool LectorPuente::escribir(uint8_t pagina, const uint8_t *datos, size_t paginas) {
  String r;
  return pedir("ESCRIBE " + String(pagina) + " " + aHex(datos, paginas * 4), r) && r == "OK";
}

void LectorPuente::terminar() {
  String r;
  pedir("FIN", r);
}

bool LectorPuente::retirada(uint32_t ms) {
  String r;
  return pedir("RETIRADA " + String(ms), r, ms + 3000) && r == "OK";
}

uint32_t LectorPuente::horaUnix() {
  String r;
  if (!pedir("HORA", r) || !r.startsWith("OK ")) return 0;
  return strtoul(r.substring(3).c_str(), nullptr, 10);
}
