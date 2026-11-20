#include "EmisorFragmentos.h"

#include <string.h>

bool EmisorFragmentos::iniciar(const uint8_t *mensaje, size_t largo, uint16_t emisor,
                               uint32_t idMensaje) {
  if (estado_ != Estado::LIBRE || mensaje == nullptr || largo == 0 || largo > MAX_MENSAJE) return false;
  const uint8_t total = sp::contarFragmentos(largo);
  if (total == 0) return false;
  memcpy(mensaje_, mensaje, largo);
  largo_ = largo;
  emisor_ = emisor;
  id_ = idMensaje;
  total_ = total;
  indice_ = 0;
  intentos_ = 0;
  fin_ = Fin::NINGUNO;
  estado_ = Estado::LISTO;
  return true;
}

void EmisorFragmentos::cancelar() {
  estado_ = Estado::LIBRE;
  fin_ = Fin::NINGUNO;
}

size_t EmisorFragmentos::siguienteTrama(uint8_t trama[sp::TAM_TRAMA_MAXIMO], uint32_t ahoraMs) {
  if (estado_ != Estado::LISTO) return 0;
  const size_t n = sp::armarFragmento(mensaje_, largo_, emisor_, id_, indice_, trama);
  if (n == 0) {  // no debería pasar: el mensaje se validó en iniciar()
    estado_ = Estado::LIBRE;
    fin_ = Fin::ABORTADO;
    return 0;
  }
  envioMs_ = ahoraMs;
  estado_ = Estado::ESPERANDO;
  return n;
}

void EmisorFragmentos::resultado(bool llego, uint32_t ahoraMs) {
  (void)ahoraMs;
  if (estado_ != Estado::ESPERANDO) return;
  if (llego) {
    intentos_ = 0;
    if (++indice_ >= total_) {
      estado_ = Estado::LIBRE;
      fin_ = Fin::ENTREGADO;
    } else {
      estado_ = Estado::LISTO;
    }
    return;
  }
  if (intentos_ >= REINTENTOS) {  // el primer intento y los 3 reintentos fallaron
    estado_ = Estado::LIBRE;
    fin_ = Fin::ABORTADO;
    return;
  }
  intentos_++;
  reintentos_++;
  estado_ = Estado::LISTO;  // se vuelve a mandar el mismo fragmento
}

void EmisorFragmentos::revisar(uint32_t ahoraMs) {
  if (estado_ == Estado::ESPERANDO && (uint32_t)(ahoraMs - envioMs_) >= ESPERA_CONFIRMACION_MS)
    resultado(false, ahoraMs);
}
