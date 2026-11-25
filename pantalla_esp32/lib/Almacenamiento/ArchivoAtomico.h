// Un archivo que se reescribe entero sin que un corte de energía lo pueda dejar a medias (M3-04).
//
// Para /recargas, /tarifas y /consumo: contenido pequeño que se cambia de golpe. Desde #129 va en <base>.ab, un
// ArchivoRanuras (dos ranuras que se sobrescriben en su sitio, ~10 ms en la microSD en vez de ~390 ms).
//
// Formato anterior (se lee una vez para migrar y luego se borra): <base>.bin / .tmp / .bak, cada uno con la carga y
// un pie de 12 B: magia(4) | generación(4) | CRC32 de la carga y de los 8 primeros bytes del pie. Se usaba la copia
// válida de mayor generación de las tres.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <vector>

#include "ArchivoRanuras.h"
#include "Archivos.h"

class ArchivoAtomico {
 public:
  static constexpr size_t TAM_PIE = 12;

  // base: "/recargas" -> /recargas.ab (y, del formato anterior, .bin, .tmp y .bak). magia: 4 caracteres que
  // identifican el formato
  // mantenerAbierto: ver ArchivoRanuras (solo para lo que se guarda a menudo)
  ArchivoAtomico(SistemaArchivos &fs, const char *base, const char magia[4], bool mantenerAbierto = true);

  // Deja en `carga` el contenido de la copia vigente (de a lo sumo `maxCarga` bytes). false si no hay ninguna (carga
  // queda vacía). La primera vez con el formato anterior lo pasa a <base>.ab y borra los .bin/.tmp/.bak.
  bool cargar(std::vector<uint8_t> &carga, size_t maxCarga);
  // Escribe `carga` como la copia nueva. false si no se pudo: lo guardado antes sigue valiendo.
  bool guardar(const uint8_t *carga, size_t n);
  // Cierra el archivo, que queda abierto entre guardados (antes de desmontar la microSD)
  void soltar() { ranuras_.soltar(); }

  uint32_t generacion() const { return ranuras_.generacion(); }

 private:
  struct Copia {
    bool valida = false;
    uint32_t generacion = 0;
  };
  Copia leerViejo(const char *ruta, std::vector<uint8_t> *carga, size_t maxCarga);
  bool cargarViejo(std::vector<uint8_t> &carga, size_t maxCarga);  // la mejor de .bin/.tmp/.bak
  void borrarViejos();

  SistemaArchivos &fs_;
  ArchivoRanuras ranuras_;
  char ab_[40], bin_[40], tmp_[40], bak_[40];
  char magia_[4];
};
