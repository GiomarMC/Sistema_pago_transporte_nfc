// Recargas pendientes de aplicar (M3-04): /recargas.bin.
//
// Son las recargas que el servidor tiene sin aplicar (C1 5.5, entradas de LISTAS de tipo 2). Cada
// una es (cuenta, seq, monto) y el cobro las suma a la tarjeta en orden de seq, sin saltarse
// ninguna. El archivo guarda los registros de 12 B (cuenta, seq, monto; uint32 y int32
// little-endian), ordenados por cuenta y seq y sin repetir, con el pie de ArchivoAtomico; así un
// corte de energía deja siempre la lista de antes o la de después (nunca a medias). Se leen
// enteros en RAM: con MAX_RECARGAS son 48 KB como mucho, y lo normal son unas decenas.
//
// aplicar() es idempotente, como la lista negra: PENDIENTE de una (cuenta, seq) ya presente solo
// pone el monto que traiga, y APLICADA de una ausente no hace nada. De las entradas repetidas
// vale la última.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <vector>

#include <ProtocoloSP.h>

#include "ArchivoAtomico.h"

struct Recarga {
  uint32_t cuenta, seq;
  int32_t monto;  // céntimos
  bool operator==(const Recarga &o) const { return cuenta == o.cuenta && seq == o.seq && monto == o.monto; }
};

class Recargas {
 public:
  static constexpr size_t TAM_REGISTRO = 12;
  static constexpr size_t MAX_RECARGAS = 4096;
  static constexpr const char *BASE = "/recargas";

  explicit Recargas(SistemaArchivos &fs) : archivo_(fs, BASE, "REC1") {}

  // Carga lo que haya en disco. false si no había ninguna copia válida: arranca vacía.
  bool iniciar();

  // Aplica las entradas de tipo RECARGA de un paquete LISTAS (las de BLOQUEO no son de aquí).
  // Con versionDesde = 0 es una foto completa: se parte de la lista vacía. Guarda una sola vez, y
  // solo si algo cambió. false si no se pudo (o si pasaría de MAX_RECARGAS): queda como estaba.
  bool aplicar(const sp::Listas &listas);

  // Sustituye todo por la lista dada (en cualquier orden; de las repetidas vale la última). Para
  // quien recibe la foto completa de una sola vez. false si no cabe o no se pudo guardar.
  bool reemplazar(std::vector<Recarga> nuevas);

  // Las recargas de `cuenta` con seq = ultima+1, ultima+2... (correlativas): suma y hasta qué seq.
  // Sin ninguna: total = 0 y hasta = ultima.
  void pendientes(uint32_t cuenta, uint32_t ultima, int32_t &total, uint32_t &hasta) const;

  size_t cantidad() const { return recargas_.size(); }
  const std::vector<Recarga> &todas() const { return recargas_; }

 private:
  bool guardar(const std::vector<Recarga> &r);

  ArchivoAtomico archivo_;
  std::vector<Recarga> recargas_;  // ordenadas por (cuenta, seq)
};
