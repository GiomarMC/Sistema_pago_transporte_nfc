#include "TareaFondo.h"

bool TareaFondo::lanzar(Trabajo trabajo, void *arg) {
  if (!trabajo || trabajo_) return false;  // ya hay uno en marcha
  if (!tarea_ &&
      xTaskCreatePinnedToCore(&TareaFondo::bucle, nombre_, pila_, this, 1, &tarea_, 0) != pdPASS) {
    tarea_ = nullptr;
    return false;  // sin memoria para la pila: el llamador lo hace en el acto
  }
  arg_ = arg;
  trabajo_ = trabajo;
  xTaskNotifyGive(tarea_);
  return true;
}

uint32_t TareaFondo::pilaLibre() const { return tarea_ ? uxTaskGetStackHighWaterMark(tarea_) : 0; }

void TareaFondo::bucle(void *yo) {
  TareaFondo &t = *static_cast<TareaFondo *>(yo);
  for (;;) {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    const Trabajo trabajo = t.trabajo_;
    if (trabajo) trabajo(t.arg_);
    t.trabajo_ = nullptr;  // libre para el siguiente
  }
}
