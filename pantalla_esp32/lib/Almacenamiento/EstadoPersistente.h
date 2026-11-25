// Estado interno del validador que no se puede perder ni repetir (M3-02): /estado.ab.
//
// Guarda siguiente_id, hasta_acusado, las versiones de listas y tarifas y la secuencia de
// los paquetes. Desde #129 va en un ArchivoRanuras: dos ranuras con CRC32 y generación que se
// sobrescriben por turnos, en su sitio (~10 ms en la microSD en vez de ~390 ms). Un corte de
// energía a mitad de un guardado deja la ranura que se escribía con el CRC mal y la anterior
// entera; al arrancar se usa la válida más nueva.
//
// Formato anterior (se lee una vez para migrar y luego se borra): /estado.bin, /estado.tmp y
// /estado.bak, de 32 B cada uno, escritos por .tmp + renombrar; se usaba el válido más nuevo.
//
// La secuencia de paquetes nunca se repite: se reserva por bloques (se guarda por delante de
// la que se entrega), y tras un corte se continúa desde lo reservado, saltándose los números
// que no se llegaron a usar. Saltar números no importa (C1 5.1: creciente y sin repetirse).
// Para siguiente_id no vale saltar (el servidor exige ids contiguos): lo mantiene ColaEventos.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "ArchivoRanuras.h"
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
  static constexpr size_t TAM_CARGA = 20;  // los cinco campos de Estado, 4 B cada uno
  static constexpr const char *RANURAS = "/estado.ab";
  // Del formato anterior (solo para migrar)
  static constexpr const char *ARCHIVO = "/estado.bin", *TEMPORAL = "/estado.tmp", *ANTERIOR = "/estado.bak";

  explicit EstadoPersistente(SistemaArchivos &fs) : fs_(fs), ranuras_(fs, RANURAS, "EST2") {}

  // Recupera el estado de lo que haya en disco. false si no había ninguna copia válida: se
  // arranca con el estado inicial (validador nuevo, o tarjeta borrada).
  bool cargar();
  const Estado &estado() const { return estado_; }
  uint32_t generacion() const { return ranuras_.generacion(); }
  // Cierra el archivo, que queda abierto entre guardados (antes de desmontar la microSD)
  void soltar() { ranuras_.soltar(); }

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
  Copia leerViejo(const char *ruta);
  void borrarViejos();

  SistemaArchivos &fs_;
  ArchivoRanuras ranuras_;
  Estado estado_;
  uint32_t secuenciaActual_ = 0;  // la última entregada
};
