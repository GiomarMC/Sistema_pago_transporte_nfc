// Tarifas del servidor (M3-04): /tarifas.bin.
//
// El archivo guarda tal cual el cuerpo del paquete TARIFAS de C1 5.6 (versión uint32, n uint8,
// 3 bytes en cero y n tarifas de 24 B: código, 3 en cero, precio uint32, nombre de 16 B rellenado
// con ceros), seguido del pie de ArchivoAtomico. Así un corte de energía deja siempre las tarifas
// de antes o las de después. El paquete TARIFAS es siempre la lista completa, de modo que
// guardar() sustituye todo.
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include <ProtocoloSP.h>

#include "ArchivoAtomico.h"

class TarifasGuardadas {
 public:
  static constexpr size_t TAM_CABECERA = 8, TAM_TARIFA = 24;
  static constexpr const char *BASE = "/tarifas";

  // Se guarda solo al llegar TARIFAS: no se queda abierto (ver ArchivoRanuras)
  explicit TarifasGuardadas(SistemaArchivos &fs) : archivo_(fs, BASE, "TAR1", false) { memset(&tarifas_, 0, sizeof(tarifas_)); }
  // Cierra el archivo, que queda abierto entre guardados (antes de desmontar la microSD)
  void soltar() { archivo_.soltar(); }

  // Carga lo que haya en disco. false si no había ninguna copia válida: arranca sin tarifas.
  bool iniciar();
  // Guarda la lista completa; si es idéntica a la guardada no escribe nada. false si no se pudo (o si es incoherente): queda como estaba.
  bool guardar(const sp::Tarifas &tarifas);

  // false si el código no existe
  bool tarifa(uint8_t codigo, sp::Tarifa &t) const;
  uint32_t version() const { return tarifas_.version; }
  size_t cantidad() const { return tarifas_.n; }

 private:
  static void codificar(const sp::Tarifas &t, uint8_t *p);  // el cuerpo de TARIFAS: 8 + n x 24 bytes
  static bool decodificar(const uint8_t *p, size_t n, sp::Tarifas &t);

  ArchivoAtomico archivo_;
  bool hayArchivo_ = false;  // tarifas_ es lo que hay guardado
  sp::Tarifas tarifas_;  // se pone a cero en iniciar()
};
