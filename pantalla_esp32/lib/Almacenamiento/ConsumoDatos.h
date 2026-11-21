// Consumo de datos por canal y por día (M3-06): /consumo.bin.
//
// Cuenta los bytes enviados (tx) y recibidos (rx) por WiFi, BLE y ESP-NOW, uno por día (fecha
// AAAAMMDD en UTC, como las horas de los eventos) y conserva los últimos 31 días. De ahí salen
// bytesTxHoy y bytesRxHoy del paquete HOLA (C1 5.4): la suma de los tres canales de hoy.
//
// Los contadores de los enlaces (Enlace::bytesTx()/bytesRx()) cuentan desde el arranque; observar() los
// convierte en lo que pasó desde la vez anterior (y si un contador baja, el enlace se reinició: vale
// el valor nuevo entero). Sin hora (aún no hay fecha) lo que se cuenta espera y se suma al primer día
// que se conozca.
//
// El archivo son registros de 28 B (fecha, tx de los 3 canales, rx de los 3 canales; uint32
// little-endian) ordenados por fecha, dentro de ArchivoAtomico: un corte de energía deja siempre la
// copia anterior entera. Se guarda cada 5 minutos si hubo cambios (atender()), así que un corte de
// energía pierde como mucho los últimos 5 minutos de cuenta, nunca más ni un archivo roto.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <vector>

#include "ArchivoAtomico.h"

enum class CanalDatos : uint8_t { WIFI = 0, BLE = 1, ESPNOW = 2 };

struct DiaConsumo {
  uint32_t fecha = 0;  // AAAAMMDD
  uint32_t tx[3] = {0, 0, 0};
  uint32_t rx[3] = {0, 0, 0};
  bool operator==(const DiaConsumo &o) const {
    return fecha == o.fecha && tx[0] == o.tx[0] && tx[1] == o.tx[1] && tx[2] == o.tx[2] && rx[0] == o.rx[0] &&
           rx[1] == o.rx[1] && rx[2] == o.rx[2];
  }
};

class ConsumoDatos {
 public:
  static constexpr size_t N_CANALES = 3, MAX_DIAS = 31, TAM_REGISTRO = 28;
  static constexpr uint32_t INTERVALO_MS = 5UL * 60UL * 1000UL;
  static constexpr const char *BASE = "/consumo";

  explicit ConsumoDatos(SistemaArchivos &fs) : archivo_(fs, BASE, "CON1") {}

  // Carga lo que haya en disco. false si no había nada válido: arranca sin historial.
  bool iniciar();

  // AAAAMMDD (UTC) de una hora unix
  static uint32_t fechaDeUnix(uint32_t unix);
  // La fecha de hoy; 0 si aún no se sabe la hora. Al conocerla por primera vez, lo contado antes se suma a ese día.
  void fijarFecha(uint32_t fecha);

  // Suma bytes al día de hoy en ese canal
  void registrar(CanalDatos canal, uint32_t tx, uint32_t rx);
  // Con el total que lleva el contador del enlace desde su arranque: registra lo que creció desde la vez anterior
  void observar(CanalDatos canal, uint32_t txTotal, uint32_t rxTotal);

  // Hoy, sumados los tres canales (saturan en 0xFFFFFFFF): lo que va en HOLA
  void bytesHoy(uint32_t &tx, uint32_t &rx) const;
  void bytesHoyCanal(CanalDatos canal, uint32_t &tx, uint32_t &rx) const;

  // El historial, del día más antiguo al más nuevo
  size_t dias() const { return dias_.size(); }
  const DiaConsumo &dia(size_t i) const { return dias_[i]; }

  bool hayCambios() const { return sucio_; }
  // Guarda ahora lo que haya sin guardar. false si no se pudo (queda por guardar).
  bool guardar();
  // Desde loop(): guarda si pasaron INTERVALO_MS desde la última vez y hay cambios. true si guardó ahora.
  bool atender(uint32_t ahoraMs);

 private:
  DiaConsumo *hoy();
  const DiaConsumo *hoy() const;
  static void suma(uint32_t &a, uint32_t b) { a = (b > 0xFFFFFFFFu - a) ? 0xFFFFFFFFu : a + b; }

  ArchivoAtomico archivo_;
  std::vector<DiaConsumo> dias_;  // por fecha
  uint32_t fecha_ = 0;
  DiaConsumo sinFecha_;           // lo contado antes de conocer la hora
  bool visto_[N_CANALES] = {false, false, false};
  uint32_t ultimoTx_[N_CANALES] = {0, 0, 0}, ultimoRx_[N_CANALES] = {0, 0, 0};
  bool sucio_ = false;
  bool relojIniciado_ = false;
  uint32_t ultimoGuardadoMs_ = 0;
};
