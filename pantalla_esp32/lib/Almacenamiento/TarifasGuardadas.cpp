#include "TarifasGuardadas.h"

#include <string.h>

#include <vector>

namespace {

void pon32(uint8_t *p, uint32_t v) {
  for (int i = 0; i < 4; i++) p[i] = v >> (8 * i);
}

uint32_t lee32(const uint8_t *p) {
  return p[0] | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

}  // namespace

bool TarifasGuardadas::decodificar(const uint8_t *p, size_t largo, sp::Tarifas &t) {
  if (largo < TAM_CABECERA) return false;
  const uint8_t n = p[4];
  if (n > sp::MAX_TARIFAS || largo != TAM_CABECERA + n * TAM_TARIFA) return false;
  memset(&t, 0, sizeof(t));  // sin temporales: la estructura pesa ~2 KB
  t.version = lee32(p);
  t.n = n;
  for (size_t i = 0; i < n; i++) {
    const uint8_t *q = p + TAM_CABECERA + i * TAM_TARIFA;
    t.tarifas[i].codigo = q[0];
    t.tarifas[i].precio = lee32(q + 4);
    memcpy(t.tarifas[i].nombre, q + 8, 16);
    t.tarifas[i].nombre[16] = 0;
  }
  return true;
}

bool TarifasGuardadas::iniciar() {
  hayArchivo_ = false;
  memset(&tarifas_, 0, sizeof(tarifas_));
  std::vector<uint8_t> carga;
  if (!archivo_.cargar(carga, TAM_CABECERA + sp::MAX_TARIFAS * TAM_TARIFA)) return false;
  hayArchivo_ = decodificar(carga.data(), carga.size(), tarifas_);
  if (!hayArchivo_) memset(&tarifas_, 0, sizeof(tarifas_));
  return hayArchivo_;
}

void TarifasGuardadas::codificar(const sp::Tarifas &t, uint8_t *p) {
  memset(p, 0, TAM_CABECERA + t.n * TAM_TARIFA);
  pon32(p, t.version);
  p[4] = t.n;
  for (size_t i = 0; i < t.n; i++) {
    uint8_t *q = p + TAM_CABECERA + i * TAM_TARIFA;
    q[0] = t.tarifas[i].codigo;
    pon32(q + 4, t.tarifas[i].precio);
    strncpy(reinterpret_cast<char *>(q + 8), t.tarifas[i].nombre, 16);  // rellena con ceros
  }
}

bool TarifasGuardadas::guardar(const sp::Tarifas &t) {
  if (t.n > sp::MAX_TARIFAS) return false;
  std::vector<uint8_t> carga(TAM_CABECERA + t.n * TAM_TARIFA);
  codificar(t, carga.data());
  // Lo mismo que ya hay: no se vuelve a escribir (cada escritura cuesta ~0,4 s en la microSD)
  if (hayArchivo_ && t.version == tarifas_.version && t.n == tarifas_.n) {
    std::vector<uint8_t> actual(carga.size());
    codificar(tarifas_, actual.data());
    if (actual == carga) return true;
  }
  if (!archivo_.guardar(carga.data(), carga.size())) return false;
  tarifas_ = t;
  hayArchivo_ = true;
  return true;
}

bool TarifasGuardadas::tarifa(uint8_t codigo, sp::Tarifa &t) const {
  for (size_t i = 0; i < tarifas_.n; i++) {
    if (tarifas_.tarifas[i].codigo == codigo) {
      t = tarifas_.tarifas[i];
      return true;
    }
  }
  return false;
}
