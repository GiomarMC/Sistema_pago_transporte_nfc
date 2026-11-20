#include "TablaVecinos.h"

#include <string.h>

TablaVecinos::Cambio TablaVecinos::actualizar(const uint8_t mac[6], const sp::Baliza &baliza,
                                              uint32_t ahoraMs) {
  for (size_t i = 0; i < cantidad_; i++) {
    if (memcmp(vecinos_[i].mac, mac, 6) != 0) continue;
    vecinos_[i].baliza = baliza;
    vecinos_[i].ultimaMs = ahoraMs;
    return Cambio::ACTUALIZADO;
  }
  if (cantidad_ >= MAXIMO) return Cambio::LLENA;
  Vecino &nuevo = vecinos_[cantidad_++];
  nuevo = Vecino();  // el hueco puede traer datos de un vecino anterior (p. ej. su cuenta de envíos)
  memcpy(nuevo.mac, mac, 6);
  nuevo.baliza = baliza;
  nuevo.ultimaMs = ahoraMs;
  return Cambio::NUEVO;
}

size_t TablaVecinos::caducar(uint32_t ahoraMs, Vecino *perdidos) {
  size_t quitados = 0, quedan = 0;
  for (size_t i = 0; i < cantidad_; i++) {
    // La resta sin signo sigue valiendo cuando millis() da la vuelta a los ~49 días
    if ((uint32_t)(ahoraMs - vecinos_[i].ultimaMs) >= CADUCIDAD_MS) {
      if (perdidos) perdidos[quitados] = vecinos_[i];
      quitados++;
    } else {
      vecinos_[quedan++] = vecinos_[i];
    }
  }
  cantidad_ = quedan;
  return quitados;
}

bool TablaVecinos::esVecino(const uint8_t mac[6]) const {
  for (size_t i = 0; i < cantidad_; i++)
    if (memcmp(vecinos_[i].mac, mac, 6) == 0) return true;
  return false;
}

TablaVecinos::Reserva TablaVecinos::reservarEnvio(const uint8_t mac[6]) {
  for (size_t i = 0; i < cantidad_; i++) {
    if (memcmp(vecinos_[i].mac, mac, 6) != 0) continue;
    if (vecinos_[i].enviados >= MAX_MENSAJES_POR_ENCUENTRO) return Reserva::LIMITE;
    vecinos_[i].enviados++;
    return Reserva::CONCEDIDA;
  }
  return Reserva::NO_ES_VECINO;
}
