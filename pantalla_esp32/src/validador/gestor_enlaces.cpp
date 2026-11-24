#include "gestor_enlaces.h"

namespace {
constexpr uint32_t HISTERESIS_MS = 10000;
}

GestorEnlaces::GestorEnlaces(Enlace &wifi, CanalPaquetes &mqttWifi, CanalPaquetes *ble,
                             Vecinos *vecinos)
    : wifi_(wifi), mqttWifi_(mqttWifi), ble_(ble), vecinos_(vecinos) {}

void GestorEnlaces::iniciar() {
  if (vecinos_) vecinos_->iniciar(canalEspNow_);
  if (wifi_.estado() == EstadoEnlace::Apagado) wifi_.encender();
}

void GestorEnlaces::atender(uint32_t ahoraMs, bool bleSolicitaCola, uint8_t canalWifi) {
  wifi_.atender();
  mqttWifi_.atender();
  if (ble_) ble_->atender();
  if (vecinos_) {
    const bool pausar = wifi_.estado() == EstadoEnlace::Listo && canalWifi != canalEspNow_;
    if (pausar != vecinosPausados_) { vecinos_->pausar(pausar); vecinosPausados_ = pausar; }
    vecinos_->atender();
  }

  bool listo[2] = {wifi_.estado() == EstadoEnlace::Listo && mqttWifi_.listo(),
                   ble_ && ble_->listo() && bleSolicitaCola};
  for (uint8_t i = 0; i < 2; ++i) {
    if (listo[i] && !anteriorListo_[i]) listoDesde_[i] = ahoraMs;
    anteriorListo_[i] = listo[i];
  }

  if (enIntercambio_) return;
  if (activo_ < 0 || !listo[activo_]) {
    activo_ = -1;
    for (int8_t i = 0; i < 2; ++i) if (listo[i]) { activo_ = i; break; }
    return;
  }
  // Solo una mejora de prioridad exige 10 s continuos. Una caída permite
  // conmutar enseguida, una vez terminado el intercambio anterior.
  for (int8_t i = 0; i < activo_; ++i) {
    if (listo[i] && (uint32_t)(ahoraMs - listoDesde_[i]) >= HISTERESIS_MS) {
      activo_ = i;
      break;
    }
  }
}

CanalPaquetes *GestorEnlaces::activo() const {
  switch (activo_) {
    case 0: return &mqttWifi_;
    case 1: return ble_;
    default: return nullptr;
  }
}

bool GestorEnlaces::iniciarIntercambio() {
  if (enIntercambio_ || !activo() || !activo()->listo() ||
      (activo_ == 0 && wifi_.estado() != EstadoEnlace::Listo)) return false;
  enIntercambio_ = true;
  return true;
}

void GestorEnlaces::terminarIntercambio() { enIntercambio_ = false; }

const char *GestorEnlaces::icono() const {
  switch (activo_) {
    case 0: return "W";
    case 1: return "BT";
    default: return "-";
  }
}

uint8_t GestorEnlaces::codigoEnlace() const { return activo() ? activo()->codigoEnlace() : 0; }

int8_t GestorEnlaces::senal() const {
  if (activo_ == 0) return wifi_.senal();
  return 0;
}

uint32_t GestorEnlaces::consumoBytes() const {
  if (activo_ == 0) return wifi_.bytesTx() + wifi_.bytesRx();
  return 0;
}
