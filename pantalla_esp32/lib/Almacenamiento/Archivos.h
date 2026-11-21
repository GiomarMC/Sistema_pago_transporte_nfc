// Capa mínima de archivos para el almacenamiento del validador (M3).
//
// Existe para poder probar la lógica de almacenamiento en el ordenador con un sistema de
// archivos falso que simula cortes de energía (test_host/fs_falso.h), y para que en la placa
// sea la microSD (ArchivosSD). El almacenamiento solo usa estas operaciones, y cuenta con
// que, como en FAT:
//   - lo escrito solo sobrevive a un corte de energía después de vaciar() (flush);
//   - renombrar falla si el destino existe, así que se borra antes;
//   - borrar y renombrar son operaciones sueltas: un corte puede ocurrir entre dos de ellas.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <memory>

class Archivo {
 public:
  virtual ~Archivo() = default;
  virtual size_t tamano() = 0;
  virtual bool leerEn(size_t posicion, void *destino, size_t n) = 0;  // exactamente n bytes, o false
  virtual bool agregar(const void *origen, size_t n) = 0;             // al final del archivo
  virtual bool vaciar() = 0;  // flush: lo escrito hasta aquí resiste un corte de energía
};

enum class Modo : uint8_t {
  LECTURA,  // debe existir
  NUEVO,    // lo crea, o lo vacía si existe
  ABRIR,    // lo crea si no existe; si existe, conserva su contenido y se agrega al final
};

class SistemaArchivos {
 public:
  virtual ~SistemaArchivos() = default;
  // nullptr si no se puede abrir
  virtual std::unique_ptr<Archivo> abrir(const char *ruta, Modo modo) = 0;
  virtual bool existe(const char *ruta) = 0;
  virtual bool borrar(const char *ruta) = 0;
  virtual bool renombrar(const char *de, const char *a) = 0;  // false si `a` ya existe
};
