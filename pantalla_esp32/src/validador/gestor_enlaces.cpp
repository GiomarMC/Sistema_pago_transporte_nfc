#include "gestor_enlaces.h"

GestorEnlaces::GestorEnlaces(Enlace &wifi, CanalPaquetes &mqttWifi, Vecinos *vecinos)
    : wifi_(wifi), mqttWifi_(mqttWifi), vecinos_(vecinos) {}

void GestorEnlaces::iniciar() {
  if (vecinos_) vecinos_->iniciar(canalEspNow_);
  if (wifi_.estado() == EstadoEnlace::Apagado) wifi_.encender();
}

void GestorEnlaces::atender(uint32_t, uint8_t canalWifi) {
  wifi_.atender();
  mqttWifi_.atender();
  if (vecinos_) {
    const bool pausar = wifi_.estado() == EstadoEnlace::Listo && canalWifi != canalEspNow_;
    if (pausar != vecinosPausados_) { vecinos_->pausar(pausar); vecinosPausados_ = pausar; }
    vecinos_->atender();
  }
  // Durante un intercambio el canal no cambia: si el WiFi cae, se suelta al terminarlo
  if (enIntercambio_) return;
  activo_ = wifi_.estado() == EstadoEnlace::Listo && mqttWifi_.listo();
}

CanalPaquetes *GestorEnlaces::activo() const { return activo_ ? &mqttWifi_ : nullptr; }

bool GestorEnlaces::iniciarIntercambio() {
  if (enIntercambio_ || !activo() || !activo()->listo() || wifi_.estado() != EstadoEnlace::Listo) return false;
  enIntercambio_ = true;
  return true;
}

void GestorEnlaces::terminarIntercambio() { enIntercambio_ = false; }

const char *GestorEnlaces::icono() const { return activo_ ? "W" : "-"; }

uint8_t GestorEnlaces::codigoEnlace() const { return activo() ? activo()->codigoEnlace() : 0; }

int8_t GestorEnlaces::senal() const { return activo_ ? wifi_.senal() : 0; }

uint32_t GestorEnlaces::consumoBytes() const { return activo_ ? wifi_.bytesTx() + wifi_.bytesRx() : 0; }
