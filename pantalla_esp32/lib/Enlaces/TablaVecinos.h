// Tabla de buses vecinos (M8-01): quién se ve por ESP-NOW y cuándo se vio por última vez.
//
// Es lógica pura, sin radio ni Arduino, para poder probarla en el ordenador. Un vecino se
// identifica por su MAC (el número de validador de una baliza no está firmado y no sirve
// para distinguirlo). Se olvida si pasan CADUCIDAD_MS sin recibir su baliza.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <Fragmentos.h>

struct Vecino {
  uint8_t mac[6] = {};
  sp::Baliza baliza;
  uint32_t ultimaMs = 0;  // millis() de la última baliza recibida
};

class TablaVecinos {
 public:
  static constexpr size_t MAXIMO = 10;
  static constexpr uint32_t CADUCIDAD_MS = 10000;
  enum class Cambio : uint8_t { NUEVO, ACTUALIZADO, LLENA };

  // Anota una baliza recibida. LLENA si ya hay MAXIMO vecinos y este no es uno de ellos:
  // se ignora hasta que caduque alguno (no se desaloja a un vecino activo por otro).
  Cambio actualizar(const uint8_t mac[6], const sp::Baliza &baliza, uint32_t ahoraMs);
  // Elimina los vecinos que llevan CADUCIDAD_MS o más sin baliza. Copia los eliminados en
  // `perdidos` (de tamaño MAXIMO) si no es nullptr, y devuelve cuántos fueron.
  size_t caducar(uint32_t ahoraMs, Vecino *perdidos = nullptr);
  void vaciar() { cantidad_ = 0; }

  size_t cantidad() const { return cantidad_; }
  const Vecino &en(size_t i) const { return vecinos_[i]; }  // i < cantidad()

 private:
  Vecino vecinos_[MAXIMO];
  size_t cantidad_ = 0;
};
