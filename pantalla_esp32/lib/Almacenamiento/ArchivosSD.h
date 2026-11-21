// Archivos en la microSD de la ESP32 (M3), para el almacenamiento del validador.
//
// Todos los nombres van dentro de una carpeta (p. ej. "/" para la raíz o "/m3prueba" para
// no mezclarse con otros datos). Solo existe en la placa: usa la biblioteca SD de Arduino.
#pragma once

#ifdef ARDUINO

#include <Arduino.h>

#include "Archivos.h"

class ArchivosSD : public SistemaArchivos {
 public:
  // carpeta: "" o "/" para la raíz; si no, p. ej. "/m3prueba" (se crea en iniciar())
  explicit ArchivosSD(const char *carpeta = "") : carpeta_(carpeta) {}

  // La microSD ya debe estar montada (SD.begin). Crea la carpeta si hace falta.
  bool iniciar();

  std::unique_ptr<Archivo> abrir(const char *ruta, Modo modo) override;
  bool existe(const char *ruta) override;
  bool borrar(const char *ruta) override;
  bool renombrar(const char *de, const char *a) override;

 private:
  bool completa(const char *ruta, char *salida, size_t n) const;
  String carpeta_;
};

#endif  // ARDUINO
