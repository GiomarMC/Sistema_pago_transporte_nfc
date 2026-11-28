#pragma once

#include <stdint.h>

#include <CanalPaquetes.h>
#include <Enlace.h>
#include <Vecinos.h>

// Orquesta C2: el canal de subida (MQTT sobre WiFi) y ESP-NOW en paralelo. Sin BLE (descartado el 27 nov).
// El sincronizador debe llamar iniciarIntercambio()/terminarIntercambio() en
// los límites de un intercambio completo de paquetes C1.
class GestorEnlaces {
 public:
  GestorEnlaces(Enlace &wifi, CanalPaquetes &mqttWifi, Vecinos *vecinos = nullptr);

  void iniciar();
  // Canal de radio de ESP-NOW (1 por defecto). Con el WiFi conectado a un AP en otro canal, ESP-NOW se pausa.
  void canalEspNow(uint8_t canal) { canalEspNow_ = canal ? canal : 1; }
  uint8_t canalEspNow() const { return canalEspNow_; }
  // canalWifi: el canal del AP al que está conectado el WiFi (1 si no hay)
  void atender(uint32_t ahoraMs, uint8_t canalWifi = 1);
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
  Vecinos *vecinos_;
  uint8_t canalEspNow_ = 1;
  bool activo_ = false;  // el WiFi es el canal de subida
  bool enIntercambio_ = false, vecinosPausados_ = false;
  uint16_t pendientes_ = 0;
  uint32_t ultimaSincronizacionMs_ = 0;
};
