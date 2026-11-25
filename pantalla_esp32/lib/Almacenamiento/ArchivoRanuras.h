// Un contenido pequeño que se reescribe entero en ~10 ms y que un corte de energía no puede dejar a medias (#129).
//
// Sustituye al guardado por .tmp/.bak/renombrar (ArchivoAtomico y EstadoPersistente lo usaban), que en la microSD
// tardaba ~390 ms: cada búsqueda, creación, borrado o renombrado de un archivo toca el directorio de FAT y cuesta
// 30-100 ms. Escribir en un archivo que ya está abierto, sin cambiar su tamaño, cuesta ~10 ms (medido en la 106 con
// prueba_tiempos_sd).
//
// Un solo archivo con dos ranuras del mismo tamaño (`tamRanura`, múltiplo de 512):
//   ranura = magia(4) | generación(4) | largo(4) | crc32(4) | carga (largo bytes) | relleno
//   crc32 = de los 12 primeros bytes y de la carga.
// guardar() escribe la carga en la ranura que NO tiene la copia vigente, en su sitio, y la vacía (flush): un corte
// a mitad deja esa ranura con el CRC mal y la otra, la anterior, intacta. Al cargar se usa la ranura válida de mayor
// generación. Con mantenerAbierto, el archivo se deja abierto entre guardados (abrirlo cuesta otros ~30-50 ms); sin
// él, se abre y se cierra en cada guardado (~57 ms). Cada archivo abierto a la vez ocupa un hueco de los que la
// microSD reserva al montar, de 4 KB cada uno (#129: con 10 huecos, a TLS no le quedaba memoria): solo lo que se
// guarda a menudo se queda abierto.
//
// Si la carga no cabe en la ranura (o el archivo no existe o está roto), el archivo se rehace con el tamaño que haga
// falta por <ruta>n (temporal): se escribe entero, se vacía, se borra el viejo y se renombra. Es lento, pero pasa al
// crearlo y cuando la carga crece de potencia de dos; un corte en medio deja el viejo o el temporal completo, y
// cargar() usa la copia válida más nueva de los dos.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <memory>
#include <vector>

#include "Archivos.h"

class ArchivoRanuras {
 public:
  static constexpr size_t TAM_CABECERA = 16;
  static constexpr size_t TAM_MINIMO = 512;  // tamaño mínimo de una ranura (un sector)

  // ruta: p. ej. "/estado.ab" (el temporal es "/estado.abn"). magia: 4 caracteres que identifican el formato
  ArchivoRanuras(SistemaArchivos &fs, const char *ruta, const char magia[4], bool mantenerAbierto = true);

  // Deja en `carga` la copia vigente (de a lo sumo maxCarga bytes) y abre el archivo para los guardados. false si
  // no hay ninguna copia válida (carga queda vacía).
  bool cargar(std::vector<uint8_t> &carga, size_t maxCarga);
  // Escribe `carga` como la copia nueva. false si no se pudo: la vigente sigue valiendo. (Si la tarjeta llegó a
  // grabar la ranura entera antes de dar el error, al arrancar se puede leer la nueva: las dos son copias válidas.)
  bool guardar(const uint8_t *carga, size_t n);
  // Si existe el archivo o su temporal (para saber si hay que migrar desde el formato anterior)
  bool hayArchivo();
  // Cierra el archivo (antes de desmontar la microSD); el siguiente guardar() lo vuelve a abrir
  void soltar() { abierto_.reset(); }

  uint32_t generacion() const { return generacion_; }
  size_t tamRanura() const { return tamRanura_; }

 private:
  struct Copia {
    bool valida = false;
    uint32_t generacion = 0;
  };
  // Lee la ranura `i` de `a` (de tamaño `tam`); deja la carga en `carga` si no es nullptr
  Copia leerRanura(Archivo &a, size_t i, size_t tam, std::vector<uint8_t> *carga, size_t maxCarga);
  bool abrir();  // abre ruta_ en EDITAR y comprueba su tamaño
  bool rehacer(const uint8_t *carga, size_t n);
  void cabecera(uint32_t generacion, const uint8_t *carga, size_t n, uint8_t c[TAM_CABECERA]) const;

  SistemaArchivos &fs_;
  char ruta_[40], temporal_[41];
  char magia_[4];
  std::unique_ptr<Archivo> abierto_;
  size_t tamRanura_ = 0;   // 0: el archivo no está abierto o no sirve (se rehace en el próximo guardar)
  uint32_t generacion_ = 0;
  int vigente_ = -1;       // ranura con la copia vigente; -1 si ninguna
  bool mantenerAbierto_;
};
