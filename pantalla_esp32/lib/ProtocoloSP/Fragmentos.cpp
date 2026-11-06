#include "Fragmentos.h"

#include <string.h>

namespace sp {

namespace {

uint16_t lee16(const uint8_t *p) { return p[0] | (uint16_t(p[1]) << 8); }

uint32_t lee32(const uint8_t *p) {
  return p[0] | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

}  // namespace

uint8_t contarFragmentos(size_t largo) {
  if (largo == 0) return 0;
  size_t n = (largo + DATOS_POR_FRAGMENTO - 1) / DATOS_POR_FRAGMENTO;
  return n <= MAX_FRAGMENTOS ? n : 0;
}

size_t armarFragmento(const uint8_t *mensaje, size_t largo, uint16_t emisor, uint32_t idMensaje,
                      uint8_t indice, uint8_t trama[TAM_TRAMA_MAXIMO]) {
  uint8_t total = contarFragmentos(largo);
  if (total == 0 || indice >= total) return 0;
  size_t desde = size_t(indice) * DATOS_POR_FRAGMENTO;
  size_t n = largo - desde < DATOS_POR_FRAGMENTO ? largo - desde : DATOS_POR_FRAGMENTO;
  trama[0] = 'E';
  trama[1] = 'N';
  trama[2] = FRAGMENTO;
  trama[3] = 0;
  trama[4] = emisor;
  trama[5] = emisor >> 8;
  for (int i = 0; i < 4; i++) trama[6 + i] = idMensaje >> (8 * i);
  trama[10] = indice;
  trama[11] = total;
  trama[12] = n;
  memcpy(trama + TAM_CABECERA_FRAGMENTO, mensaje + desde, n);
  return TAM_CABECERA_FRAGMENTO + n;
}

void Reensamblador::descartar() {
  if (recibidos_ != 0) descartados_++;
  recibidos_ = 0;
  total_ = 0;
}

void Reensamblador::revisar(uint32_t ahoraMs) {
  if (recibidos_ != 0 && ahoraMs - inicioMs_ > ESPERA_FRAGMENTOS_MS) descartar();
}

Reensamblador::Resultado Reensamblador::recibir(const uint8_t *trama, size_t largo,
                                                uint32_t ahoraMs) {
  revisar(ahoraMs);
  if (largo < TAM_CABECERA_FRAGMENTO || largo > TAM_TRAMA_MAXIMO || trama[0] != 'E' ||
      trama[1] != 'N' || trama[2] != FRAGMENTO)
    return Resultado::INVALIDA;
  uint16_t emisor = lee16(trama + 4);
  uint32_t idMensaje = lee32(trama + 6);
  uint8_t indice = trama[10], total = trama[11], n = trama[12];
  bool ultimo = indice + 1 == total;
  if (total == 0 || total > MAX_FRAGMENTOS || indice >= total || n == 0 ||
      n > DATOS_POR_FRAGMENTO || largo != TAM_CABECERA_FRAGMENTO + n ||
      (!ultimo && n != DATOS_POR_FRAGMENTO))
    return Resultado::INVALIDA;

  bool mismo = recibidos_ != 0 && emisor == emisor_ && idMensaje == idMensaje_;
  if (mismo && total != total_) return Resultado::INVALIDA;
  if (!mismo) {  // empieza otro mensaje: el anterior (si lo había) se pierde
    descartar();
    emisor_ = emisor;
    idMensaje_ = idMensaje;
    total_ = total;
    inicioMs_ = ahoraMs;
  }
  memcpy(datos_ + size_t(indice) * DATOS_POR_FRAGMENTO, trama + TAM_CABECERA_FRAGMENTO, n);
  recibidos_ |= 1u << indice;
  if (ultimo) largo_ = size_t(indice) * DATOS_POR_FRAGMENTO + n;

  if (recibidos_ != (1u << total_) - 1) return Resultado::INCOMPLETO;
  recibidos_ = 0;  // listo: el siguiente fragmento empieza un mensaje nuevo
  total_ = 0;
  return Resultado::COMPLETO;
}

}  // namespace sp
