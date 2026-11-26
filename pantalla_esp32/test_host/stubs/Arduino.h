#pragma once
#include <stdint.h>
#include <string>

class String {
 public:
  String(const char *s = "") : valor_(s ? s : "") {}
  bool isEmpty() const { return valor_.empty(); }
  size_t length() const { return valor_.length(); }
  const char *c_str() const { return valor_.c_str(); }
 private:
  std::string valor_;
};

extern uint32_t relojFalso;
inline uint32_t millis() { return relojFalso; }
inline void delay(uint32_t) {}
