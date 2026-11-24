// TrabajoEnFondo con una tarea de FreeRTOS (#129). La tarea se crea con el primer trabajo y queda dormida esperando
// el siguiente (crear y borrar tareas cada vez fragmentaría el montón). Va en el núcleo 0, el de la radio, con la
// prioridad de loop(): loop() sigue en el núcleo 1 sin perder tiempo de CPU mientras se hace el handshake TLS.
#pragma once

#include <Arduino.h>

#include "TrabajoEnFondo.h"

class TareaFondo final : public TrabajoEnFondo {
 public:
  // pilaBytes: la del handshake TLS de WiFiClientSecure con mbedTLS (la de loop() es de 8 KB y bastaba)
  explicit TareaFondo(const char *nombre = "fondo", uint32_t pilaBytes = 10240) : nombre_(nombre), pila_(pilaBytes) {}
  bool lanzar(Trabajo trabajo, void *arg) override;
  // Lo que nunca llegó a usarse de la pila, en bytes (0 si la tarea no existe): para ajustar pilaBytes
  uint32_t pilaLibre() const;

 private:
  static void bucle(void *yo);
  const char *nombre_;
  uint32_t pila_;
  TaskHandle_t tarea_ = nullptr;
  volatile Trabajo trabajo_ = nullptr;
  void *volatile arg_ = nullptr;
};
