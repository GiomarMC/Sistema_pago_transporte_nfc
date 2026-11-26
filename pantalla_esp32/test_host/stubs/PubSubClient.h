#pragma once
#include <functional>
#include <stdint.h>
#include <string>
#include <vector>
#include "Client.h"

class PubSubClient {
 public:
  using Callback = std::function<void(char *, uint8_t *, unsigned int)>;
  static inline PubSubClient *ultimo = nullptr;
  static inline uint16_t buffer = 0, keepalive = 0, timeout = 0;
  static inline std::string usuario, clave, will, publicado;
  static inline bool limpio = false;
  static inline std::vector<std::string> suscripciones, temas;
  PubSubClient() { ultimo = this; }
  bool setBufferSize(uint16_t n) { buffer = n; return true; }
  PubSubClient &setKeepAlive(uint16_t n) { keepalive = n; return *this; }
  PubSubClient &setSocketTimeout(uint16_t n) { timeout = n; return *this; }
  PubSubClient &setCallback(Callback cb) { callback_ = cb; return *this; }
  PubSubClient &setClient(Client &) { return *this; }
  PubSubClient &setServer(const char *, uint16_t) { return *this; }
  bool connect(const char *, const char *u, const char *p, const char *w, uint8_t,
               bool, const char *, bool clean) {
    usuario = u; clave = p; will = w; limpio = clean; conectado_ = true; return true;
  }
  bool subscribe(const char *topic, uint8_t) { suscripciones.emplace_back(topic); return true; }
  bool publish(const char *topic, const char *payload, bool) {
    temas.emplace_back(topic); publicado = payload; return true;
  }
  bool publish(const char *topic, const uint8_t *payload, unsigned int n, bool) {
    temas.emplace_back(topic); publicado.assign(reinterpret_cast<const char *>(payload), n); return true;
  }
  static inline int vueltas = 0;  // cuántas veces se llamó a loop()
  bool loop() { ++vueltas; return conectado_; }
  bool connected() { return conectado_; }
  void disconnect() { conectado_ = false; }
  void inyectar(const char *topic, const uint8_t *datos, unsigned int n) {
    std::string t = topic; std::vector<uint8_t> d(datos, datos + n);
    callback_(t.data(), d.data(), n);
  }
 private:
  bool conectado_ = false;
  Callback callback_;
};
