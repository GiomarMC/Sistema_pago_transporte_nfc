#include "BuscadorCanal.h"

namespace {
// Después del canal en que se está y de espnow_canal: los tres canales sin solape, que son los que más usan los
// routers, y luego el resto. Dentro de cada grupo el orden se baraja en cada vuelta: con un orden fijo, dos buses que
// buscan a la vez daban la vuelta al mismo paso, uno siempre un canal por delante del otro, y medido en la simulación
// a veces no coincidían en media hora
const uint8_t SIN_SOLAPE[] = {1, 6, 11};
const uint8_t RESTO[] = {2, 3, 4, 5, 7, 8, 9, 10, 12, 13};
}  // namespace

uint32_t BuscadorCanal::aleatorio() {  // xorshift32: basta para desfasar a dos buses
  azar_ ^= azar_ << 13;
  azar_ ^= azar_ >> 17;
  azar_ ^= azar_ << 5;
  return azar_;
}

uint32_t BuscadorCanal::espera(bool enBase) {
  const uint32_t fija = enBase ? EN_BASE_MS : POR_CANAL_MS;
  return fija - VARIACION_MS + aleatorio() % (2 * VARIACION_MS + 1);
}

void BuscadorCanal::armar(uint8_t primero) {
  cuantos_ = 0;
  auto anadir = [this](uint8_t c) {
    if (!valido(c)) return;
    for (uint8_t i = 0; i < cuantos_; i++)
      if (orden_[i] == c) return;
    orden_[cuantos_++] = c;
  };
  auto barajados = [&](const uint8_t *grupo, uint8_t n) {
    uint8_t copia[ULTIMO_CANAL];
    for (uint8_t i = 0; i < n; i++) copia[i] = grupo[i];
    for (uint8_t i = n - 1; i > 0; i--) {  // Fisher-Yates
      const uint8_t j = aleatorio() % (i + 1);
      const uint8_t x = copia[i];
      copia[i] = copia[j];
      copia[j] = x;
    }
    for (uint8_t i = 0; i < n; i++) anadir(copia[i]);
  };
  anadir(primero);
  anadir(base_);
  barajados(SIN_SOLAPE, sizeof(SIN_SOLAPE));
  barajados(RESTO, sizeof(RESTO));
}

void BuscadorCanal::empezar(uint32_t ahoraMs, uint8_t actual) {
  armar(actual);
  indice_ = 0;
  buscando_ = true;
  enPausa_ = false;
  desdeMs_ = ahoraMs;
  esperaMs_ = espera(orden_[0] == base_);
}

uint8_t BuscadorCanal::atender(uint32_t ahoraMs, uint8_t actual, size_t vecinos, bool permitido) {
  if (vecinos > 0) {
    reiniciar();  // al volver a quedarse sin vecinos se empieza desde el canal de entonces
    return 0;
  }
  if (!permitido) {
    enPausa_ = true;  // el recorrido se queda donde iba
    return 0;
  }
  if (!buscando_) {
    empezar(ahoraMs, actual);
    return 0;
  }
  if (enPausa_) {  // p. ej. el WiFi buscó redes (~2 s, la radio pasó por todos los canales): este canal, entero otra vez
    enPausa_ = false;
    desdeMs_ = ahoraMs;
    return 0;
  }
  if ((uint32_t)(ahoraMs - desdeMs_) < esperaMs_) return 0;
  indice_++;
  if (indice_ >= cuantos_) {  // otra vuelta: desde espnow_canal, con otro orden
    armar(base_);
    indice_ = 0;
    vueltas_++;
  }
  desdeMs_ = ahoraMs;
  const uint8_t siguiente = orden_[indice_];
  esperaMs_ = espera(siguiente == base_);
  if (siguiente == actual) return 0;
  saltos_++;
  return siguiente;
}

uint8_t BuscadorCanal::pista(uint32_t ahoraMs, uint8_t actual, uint8_t canal, size_t vecinos, bool permitido) {
  // Con vecinos en este canal no se va detrás de otro: se perdería a los que ya se oyen
  if (!permitido || vecinos > 0 || !valido(canal) || canal == actual) return 0;
  if (!buscando_) empezar(ahoraMs, actual);
  // Se espera ahí lo de un canal cualquiera: la próxima baliza de ese vecino llega en 2,5 s como mucho. Si no llega,
  // se sigue con el recorrido donde iba
  desdeMs_ = ahoraMs;
  esperaMs_ = espera(false);
  saltos_++;
  return canal;
}
