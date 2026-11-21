// Contrato C2: ESP-NOW entre buses (M8-01). Descubre buses cercanos con balizas.
//
// Cada bus difunde una BALIZA (C1 5.10) cada 2 s ± 500 ms en el canal de radio 1 y
// mantiene la tabla de los vecinos que oye; se olvidan tras 10 s sin balizas. No envía
// datos: la transferencia de paquetes es M8-02.
//
// ESP-NOW y el WiFi comparten la radio. Con el WiFi STA conectado a un punto de acceso
// en otro canal, hay que llamar a pausar(true) (lo hace el GestorEnlaces). iniciar()
// fija el canal de la radio, así que debe llamarse con el WiFi sin conectar o ya en ese
// canal.
//
// Uso:
//   VecinosEspNow vecinos(log);
//   vecinos.publicar(baliza);     // qué anuncia este bus; se puede repetir cuando cambie
//   vecinos.iniciar(1);
//   ... en cada loop():  vecinos.atender();
#pragma once

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

#include <Fragmentos.h>

#include "TablaVecinos.h"
#include "Vecinos.h"

class VecinosEspNow final : public Vecinos {
 public:
  using Registro = void (*)(const String &);

  static constexpr uint32_t BALIZA_MS = 2000;     // periodo entre balizas
  static constexpr uint32_t VARIACION_MS = 500;   // ± al azar, para que dos buses no choquen siempre

  explicit VecinosEspNow(Registro log = nullptr) : log_(log) {}
  ~VecinosEspNow() override;

  // --- Vecinos ---------------------------------------------------------------------
  void iniciar(uint8_t canalWifi) override;  // 0 se toma como 1
  void atender() override;                   // en cada loop(); no bloquea
  void pausar(bool si) override;
  size_t cantidad() const override { return tabla_.cantidad(); }

  // --- Además del contrato ---------------------------------------------------------
  // Lo que este bus anuncia (su número de validador, versiones, pendientes...)
  void publicar(const sp::Baliza &datos) { propia_ = datos; }
  const Vecino &vecino(size_t i) const { return tabla_.en(i); }  // i < cantidad()
  bool activo() const { return iniciado_ && !pausado_; }
  uint32_t balizasEnviadas() const { return enviadas_; }
  uint32_t balizasRecibidas() const { return recibidas_; }
  uint32_t erroresDeEnvio() const { return erroresEnvio_; }
  uint32_t balizasPerdidas() const { return perdidasCola_; }  // llegaron con la cola llena

 private:
  struct Recibida {
    uint8_t mac[6];
    uint8_t datos[sp::TAM_BALIZA];
  };

  static void alRecibir(const uint8_t *mac, const uint8_t *datos, int largo);
  void fijarCanal();
  void enviarBaliza();
  void programarSiguiente(uint32_t ahoraMs, bool primera);
  void anotar(const String &texto) const;
  static String macTexto(const uint8_t mac[6]);

  Registro log_;
  TablaVecinos tabla_;
  sp::Baliza propia_;
  QueueHandle_t cola_ = nullptr;  // balizas de la radio hacia atender(), que corren en tareas distintas
  uint8_t canal_ = 1;
  bool iniciado_ = false, pausado_ = false;
  uint32_t proximaMs_ = 0;
  volatile uint32_t recibidas_ = 0, perdidasCola_ = 0;
  uint32_t enviadas_ = 0, erroresEnvio_ = 0;

  static VecinosEspNow *instancia_;  // el aviso de la radio es una función suelta
};
