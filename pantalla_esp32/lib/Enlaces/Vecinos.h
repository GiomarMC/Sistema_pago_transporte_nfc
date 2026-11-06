// Contrato C2: ESP-NOW entre buses. No es un canal de subida.
//
// Funciona en paralelo a los demás, en el canal de radio 1. Si el WiFi STA está
// conectado a un AP en otro canal, se pausa. Implementación: VecinosEspNow (M8).
#pragma once

#include <stddef.h>
#include <stdint.h>

class Vecinos {
 public:
  // No figura en el texto de C2: sin él, borrar una implementación por un puntero a
  // Vecinos sería comportamiento indefinido (Enlace y CanalPaquetes ya lo tienen).
  virtual ~Vecinos() = default;
  virtual void iniciar(uint8_t canalWifi) = 0;  // 1 por defecto
  virtual void atender() = 0;
  virtual void pausar(bool si) = 0;     // cuando el WiFi STA usa otro canal
  virtual size_t cantidad() const = 0;  // vecinos vistos en los últimos 10 s
};
