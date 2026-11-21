// Un archivo que se reescribe entero sin que un corte de energía lo pueda dejar a medias (M3-04).
//
// Para /recargas.bin y /tarifas.bin: contenido pequeño que se cambia de golpe. Al contenido
// ("carga") se le agrega un pie de 12 B: magia(4) | generación(4) | CRC32 de la carga y de los
// 8 primeros bytes del pie. La carga va al principio, así que el archivo empieza con ella tal
// cual (p. ej. el cuerpo de TARIFAS de C1).
//
// El guardado sigue los pasos de EstadoPersistente, con <base>.bin / .tmp / .bak:
//   1. se escribe <base>.tmp entero y se vacía (flush);   un corte: .bin y .bak intactos
//   2. se borra .bak;                                      un corte: .bin intacto
//   3. .bin pasa a .bak;                                   un corte: .tmp completo y .bak
//   4. .tmp pasa a .bin.
// Al cargar se usa la copia válida (magia y CRC) de mayor generación de las tres.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <vector>

#include "Archivos.h"

class ArchivoAtomico {
 public:
  static constexpr size_t TAM_PIE = 12;

  // base: "/recargas" -> /recargas.bin, .tmp y .bak. magia: 4 caracteres que identifican el formato
  ArchivoAtomico(SistemaArchivos &fs, const char *base, const char magia[4]);

  // Deja en `carga` el contenido de la mejor copia válida (de a lo sumo `maxCarga` bytes).
  // false si no hay ninguna (carga queda vacía). Si la mejor no era .bin, o sobró un .tmp,
  // deja los archivos en orden.
  bool cargar(std::vector<uint8_t> &carga, size_t maxCarga);
  // Escribe `carga` como la copia nueva. false si no se pudo: lo guardado antes sigue valiendo.
  bool guardar(const uint8_t *carga, size_t n);

  uint32_t generacion() const { return generacion_; }

 private:
  struct Copia {
    bool valida = false;
    uint32_t generacion = 0;
  };
  Copia leer(const char *ruta, std::vector<uint8_t> *carga, size_t maxCarga);

  SistemaArchivos &fs_;
  char bin_[40], tmp_[40], bak_[40];
  char magia_[4];
  uint32_t generacion_ = 0;
};
