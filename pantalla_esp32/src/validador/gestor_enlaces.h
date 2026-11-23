#pragma once

#include <stdint.h>

#include <CanalPaquetes.h>
#include <Enlace.h>
#include <Vecinos.h>

// Orquesta C2 sin conocer la implementación concreta de WiFi o BLE.
// El sincronizador debe llamar iniciarIntercambio()/terminarIntercambio() en
// los límites de un intercambio completo de paquetes C1.
class GestorEnlaces {
 public:
  GestorEnlaces(Enlace &wifi, CanalPaquetes &mqttWifi, CanalPaquetes *ble = nullptr,
                Vecinos *vecinos = nullptr);

  void iniciar();
  // Canal de radio de ESP-NOW (1 por defecto). Con el WiFi conectado a un AP en otro canal, ESP-NOW se pausa.
  void canalEspNow(uint8_t canal) { canalEspNow_ = canal ? canal : 1; }
  uint8_t canalEspNow() const { return canalEspNow_; }
  // canalWifi: el canal del AP al que está conectado el WiFi (1 si no hay)
  void atender(uint32_t ahoraMs, bool bleSolicitaCola, uint8_t canalWifi = 1);
  CanalPaquetes *activo() const;
  bool iniciarIntercambio();
  void terminarIntercambio();
  bool enIntercambio() const { return enIntercambio_; }

  const char *icono() const;
  uint8_t codigoEnlace() const;
  int8_t senal() const;
  uint32_t consumoBytes() const;
  void pendientes(uint16_t cantidad) { pendientes_ = cantidad; }
  uint16_t pendientes() const { return pendientes_; }
  void sincronizado(uint32_t ahoraMs) { ultimaSincronizacionMs_ = ahoraMs; }
  uint32_t ultimaSincronizacionMs() const { return ultimaSincronizacionMs_; }

 private:
  Enlace &wifi_;
  CanalPaquetes &mqttWifi_;
  CanalPaquetes *ble_;
  Vecinos *vecinos_;
  uint8_t canalEspNow_ = 1;
  int8_t activo_ = -1;  // 0 WiFi, 1 BLE
  bool enIntercambio_ = false, vecinosPausados_ = false;
  bool anteriorListo_[2] = {};
  uint32_t listoDesde_[2] = {};
  uint16_t pendientes_ = 0;
  uint32_t ultimaSincronizacionMs_ = 0;
};
