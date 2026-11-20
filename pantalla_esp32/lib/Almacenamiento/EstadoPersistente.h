// Estado interno del validador que no se puede perder ni repetir (M3-02): /estado.bin.
//
// Guarda siguiente_id, hasta_acusado, las versiones de listas y tarifas y la secuencia de
// los paquetes, con un CRC32. Se escribe de forma que un corte de energía en cualquier
// punto deja al menos una copia entera:
//
//   1. se escribe /estado.tmp y se vacía (flush);   un corte aquí: .bin y .bak intactos
//   2. se borra /estado.bak;                         un corte aquí: .bin intacto, .tmp nuevo
//   3. /estado.bin pasa a /estado.bak;               un corte aquí: .tmp nuevo y .bak anterior
//   4. /estado.tmp pasa a /estado.bin.
//
// Cada copia lleva un número de generación; al arrancar se usa la válida (CRC) más nueva de
// las tres, así que no importa en qué paso se cortó. Si /estado.bin se corrompe, se usa la
// anterior (/estado.bak).
//
// La secuencia de paquetes nunca se repite: se reserva por bloques (se guarda por delante de
// la que se entrega), y tras un corte se continúa desde lo reservado, saltándose los números
// que no se llegaron a usar. Saltar números no importa (C1 5.1: creciente y sin repetirse).
// Para siguiente_id no vale saltar (el servidor exige ids contiguos): lo mantiene ColaEventos.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "Archivos.h"

struct Estado {
  uint32_t siguienteId = 1;
  uint32_t hastaAcusado = 0;
  uint32_t versionListas = 0;
  uint32_t versionTarifas = 0;
  uint32_t secuenciaPaquetes = 0;  // lo reservado en disco; la que se entregó nunca la supera
};

class EstadoPersistente {
 public:
  static constexpr uint32_t RESERVA_SECUENCIA = 32;
  static constexpr size_t TAM_REGISTRO = 32;
  static constexpr const char *ARCHIVO = "/estado.bin", *TEMPORAL = "/estado.tmp", *ANTERIOR = "/estado.bak";

  explicit EstadoPersistente(SistemaArchivos &fs) : fs_(fs) {}

  // Recupera el estado de lo que haya en disco. false si no había ninguna copia válida: se
  // arranca con el estado inicial (validador nuevo, o tarjeta borrada).
  bool cargar();
  const Estado &estado() const { return estado_; }
  uint32_t generacion() const { return generacion_; }

  // Cada uno guarda en disco si el valor cambió; false si no se pudo guardar
  bool fijarSiguienteId(uint32_t id);
  bool fijarHastaAcusado(uint32_t id);
  bool fijarVersiones(uint32_t listas, uint32_t tarifas);

  // La secuencia siguiente para un paquete propio; 0 si no se pudo reservar
  uint32_t siguienteSecuencia();

  bool guardar();  // escribe el estado actual (los fijar...() ya lo hacen)

 private:
  struct Copia {
    bool valida = false;
    uint32_t generacion = 0;
    Estado estado;
  };
  Copia leer(const char *ruta);

  SistemaArchivos &fs_;
  Estado estado_;
  uint32_t generacion_ = 0;
  uint32_t secuenciaActual_ = 0;  // la última entregada
};
