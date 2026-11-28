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
  CanalFalso mqtt("mqtt-wifi", 1);
  VecinosFalsos vecinos;
  GestorEnlaces gestor(wifi, mqtt, &vecinos);
  gestor.iniciar();
  assert(wifi.encendidos() == 1 && vecinos.canalInicial == 1);

  // Sin WiFi no hay canal de subida.
  wifi.ponerEstado(EstadoEnlace::Conectando);
  gestor.atender(100);
  assert(gestor.activo() == nullptr && strcmp(gestor.icono(), "-") == 0 && gestor.codigoEnlace() == 0);
  assert(!gestor.iniciarIntercambio());

  // Con el WiFi listo, el canal se activa enseguida.
  wifi.ponerEstado(EstadoEnlace::Listo);
  gestor.atender(1000);
  assert(gestor.activo() == &mqtt && strcmp(gestor.icono(), "W") == 0 && gestor.codigoEnlace() == 1);

  // ESP-NOW sigue en paralelo y se pausa si el AP usa otro canal.
  gestor.atender(12000, 6);
  assert(vecinos.pausado && vecinos.atenciones >= 3);
  gestor.atender(12001, 1);
  assert(!vecinos.pausado && vecinos.cambios == 2);

  // Si el WiFi cae a mitad de un intercambio, el canal se suelta al terminarlo.
  assert(gestor.iniciarIntercambio());
  assert(!gestor.iniciarIntercambio());  // uno a la vez
  wifi.ponerEstado(EstadoEnlace::Error);
  mqtt.ponerListo(false);
  gestor.atender(12002);
  assert(gestor.activo() == &mqtt);
  gestor.terminarIntercambio();
  gestor.atender(12003);
  assert(gestor.activo() == nullptr);

  // Un enlace caído no puede iniciar un intercambio nuevo antes de atenderlo.
  wifi.ponerEstado(EstadoEnlace::Listo);
  mqtt.ponerListo(true);
  gestor.atender(12005);
  wifi.ponerEstado(EstadoEnlace::Error);
  assert(!gestor.iniciarIntercambio());

  // El gestor no consume eventos pendientes; la telemetría es la del WiFi.
  gestor.pendientes(45);
  assert(gestor.pendientes() == 45);
  wifi.ponerEstado(EstadoEnlace::Listo);
  wifi.ponerSenal(-33);
  wifi.sumarBytes(120, 300);
  gestor.atender(13000);
  assert(gestor.activo() == &mqtt && gestor.senal() == -33);
  assert(gestor.consumoBytes() == 420 && gestor.pendientes() == 45);
  gestor.sincronizado(13000);
  assert(gestor.ultimaSincronizacionMs() == 13000);
  puts("M15 gestor: WiFi, intercambio, ESP-NOW y telemetría OK");
}
