#include "cobros_dudosos.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void CobrosDudosos::quitar(size_t i) {
  for (size_t j = i + 1; j < n_; j++) lista_[j - 1] = lista_[j];
  n_--;
}

void CobrosDudosos::anotar(const CobroDudoso &c) {
  for (size_t i = 0; i < n_; i++) {
    if (lista_[i].idTarjeta == c.idTarjeta) {
      quitar(i);
      break;
    }
  }
  if (n_ == MAXIMO) quitar(0);
  lista_[n_++] = c;
}

CobrosDudosos::Resultado CobrosDudosos::resolver(uint32_t idTarjeta, uint16_t operacion, uint16_t validador,
                                                 uint16_t yo, CobroDudoso &c) {
  for (size_t i = 0; i < n_; i++) {
    if (lista_[i].idTarjeta != idTarjeta) continue;
    c = lista_[i];
    quitar(i);
    if (operacion == c.operacion && validador == yo) return Resultado::GRABADO;
    if (operacion < c.operacion) return Resultado::NO_GRABADO;
    return Resultado::INCIERTO;
  }
  return Resultado::NINGUNO;
}

size_t CobrosDudosos::aTexto(char *buf, size_t max) const {
  size_t largo = 0;
  if (max == 0) return 0;
  buf[0] = '\0';
  for (size_t i = 0; i < n_; i++) {
    const CobroDudoso &c = lista_[i];
    const int n = snprintf(buf + largo, max - largo, "%lu %u %lu %u %ld %ld %lu\n", (unsigned long)c.idTarjeta,
                           (unsigned)c.operacion, (unsigned long)c.fecha, c.fechaReal ? 1u : 0u, (long)c.monto,
                           (long)c.saldo, (unsigned long)c.recargaHasta);
    if (n < 0 || (size_t)n >= max - largo) {
      buf[0] = '\0';
      return 0;
    }
    largo += (size_t)n;
  }
  return largo;
}

void CobrosDudosos::deTexto(const char *texto) {
  n_ = 0;
  const char *p = texto;
  while (p && *p) {
    const char *fin = strchr(p, '\n');
    char linea[96];
    const size_t largo = fin ? (size_t)(fin - p) : strlen(p);
    if (largo < sizeof(linea)) {
      memcpy(linea, p, largo);
      linea[largo] = '\0';
      unsigned long id, fecha, recarga;
      unsigned op, real;
      long monto, saldo;
      char sobra;
      if (sscanf(linea, "%lu %u %lu %u %ld %ld %lu %c", &id, &op, &fecha, &real, &monto, &saldo, &recarga, &sobra) ==
              7 &&
          id != 0 && op <= 0xFFFF && real <= 1) {
        CobroDudoso c;
        c.idTarjeta = id;
        c.operacion = (uint16_t)op;
        c.fecha = fecha;
        c.fechaReal = real == 1;
        c.monto = monto;
        c.saldo = saldo;
        c.recargaHasta = recarga;
        anotar(c);
      }
    }
    p = fin ? fin + 1 : nullptr;
  }
}
