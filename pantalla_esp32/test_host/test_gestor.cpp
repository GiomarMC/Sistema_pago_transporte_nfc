#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <CanalFalso.h>
#include <EnlaceFalso.h>
#include "gestor_enlaces.h"

class VecinosFalsos : public Vecinos {
 public:
  void iniciar(uint8_t canal) override { canalInicial = canal; }
  void atender() override { ++atenciones; }
  void pausar(bool si) override { pausado = si; ++cambios; }
  size_t cantidad() const override { return 2; }
  uint8_t canalInicial = 0;
  uint32_t atenciones = 0, cambios = 0;
  bool pausado = false;
};

int main() {
  EnlaceFalso wifi("wifi", 1);
  CanalFalso mqtt("mqtt-wifi", 1), ble("ble", 3);
  VecinosFalsos vecinos;
  GestorEnlaces gestor(wifi, mqtt, &ble, &vecinos);
  gestor.iniciar();
  assert(wifi.encendidos() == 1 && vecinos.canalInicial == 1);

  // Sin WiFi, BLE sirve la cola solo cuando el conductor la solicita.
  wifi.ponerEstado(EstadoEnlace::Conectando);
  gestor.atender(100, false);
  assert(gestor.activo() == nullptr);
  gestor.atender(101, true);
  assert(gestor.activo() == &ble && gestor.codigoEnlace() == 3);
  assert(gestor.iniciarIntercambio());

  // El WiFi recuperado espera diez segundos y no interrumpe el intercambio.
  wifi.ponerEstado(EstadoEnlace::Listo);
  gestor.atender(1000, true);
  gestor.atender(10999, true);
  assert(gestor.activo() == &ble);
  gestor.atender(11001, true);
  assert(gestor.activo() == &ble);
  gestor.terminarIntercambio();
  gestor.atender(11002, true);
  assert(gestor.activo() == &mqtt && strcmp(gestor.icono(), "W") == 0);

  // ESP-NOW sigue en paralelo y se pausa si el AP usa otro canal.
  gestor.atender(12000, true, 6);
  assert(vecinos.pausado && vecinos.atenciones >= 4);
  gestor.atender(12001, true, 1);
  assert(!vecinos.pausado && vecinos.cambios == 2);

  // La caída de WiFi permite cambiar a BLE al terminar el intercambio.
  assert(gestor.iniciarIntercambio());
  wifi.ponerEstado(EstadoEnlace::Error);
  mqtt.ponerListo(false);
  gestor.atender(12002, true);
  assert(gestor.activo() == &mqtt);
  gestor.terminarIntercambio();
  gestor.atender(12003, true);
  assert(gestor.activo() == &ble && strcmp(gestor.icono(), "BT") == 0);
  gestor.atender(12004, false);
  assert(gestor.activo() == nullptr);

  // Un enlace caído no puede iniciar un intercambio nuevo antes de atenderlo.
  wifi.ponerEstado(EstadoEnlace::Listo);
  mqtt.ponerListo(true);
  gestor.atender(12005, false);
  wifi.ponerEstado(EstadoEnlace::Error);
  assert(!gestor.iniciarIntercambio());

  // El gestor no consume eventos pendientes cuando no hay enlace.
  gestor.pendientes(45);
  assert(gestor.pendientes() == 45);
  wifi.ponerEstado(EstadoEnlace::Listo);
  mqtt.ponerListo(true);
  wifi.ponerSenal(-33);
  wifi.sumarBytes(120, 300);
  gestor.atender(13000, false);
  assert(gestor.activo() == &mqtt && gestor.senal() == -33);
  assert(gestor.consumoBytes() == 420 && gestor.pendientes() == 45);
  gestor.sincronizado(13000);
  assert(gestor.ultimaSincronizacionMs() == 13000);
  puts("M15 gestor: WiFi/BLE, histéresis, intercambio, ESP-NOW y telemetría OK");
}
