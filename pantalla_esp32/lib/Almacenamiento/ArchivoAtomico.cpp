#include "ArchivoAtomico.h"

#include <stdio.h>
#include <string.h>

#include "Crc32.h"

namespace {

constexpr size_t TROZO = 512;  // lo que se lee o escribe de una vez (un sector)

void pon32(uint8_t *p, uint32_t v) {
  for (int i = 0; i < 4; i++) p[i] = v >> (8 * i);
}

uint32_t lee32(const uint8_t *p) {
  return p[0] | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

}  // namespace

ArchivoAtomico::ArchivoAtomico(SistemaArchivos &fs, const char *base, const char magia[4]) : fs_(fs) {
  snprintf(bin_, sizeof(bin_), "%s.bin", base);
  snprintf(tmp_, sizeof(tmp_), "%s.tmp", base);
  snprintf(bak_, sizeof(bak_), "%s.bak", base);
  memcpy(magia_, magia, 4);
}

ArchivoAtomico::Copia ArchivoAtomico::leer(const char *ruta, std::vector<uint8_t> *carga, size_t maxCarga) {
  Copia c;
  if (!fs_.existe(ruta)) return c;  // evita el aviso de la microSD al abrir lo que no existe
  auto a = fs_.abrir(ruta, Modo::LECTURA);
  if (!a) return c;
  const size_t tam = a->tamano();
  if (tam < TAM_PIE || tam - TAM_PIE > maxCarga) return c;
  const size_t n = tam - TAM_PIE;
  uint8_t pie[TAM_PIE];
  if (!a->leerEn(n, pie, TAM_PIE) || memcmp(pie, magia_, 4) != 0) return c;

  std::vector<uint8_t> datos(n);
  uint32_t crc = 0;
  for (size_t pos = 0; pos < n; pos += TROZO) {
    const size_t k = n - pos < TROZO ? n - pos : TROZO;
    if (!a->leerEn(pos, datos.data() + pos, k)) return c;
    crc = crc32(datos.data() + pos, k, crc);
  }
  crc = crc32(pie, 8, crc);
  if (crc != lee32(pie + 8)) return c;

  c.valida = true;
  c.generacion = lee32(pie + 4);
  if (carga) *carga = std::move(datos);
  return c;
}

bool ArchivoAtomico::cargar(std::vector<uint8_t> &carga, size_t maxCarga) {
  carga.clear();
  const char *rutas[] = {bin_, tmp_, bak_};
  Copia mejor;
  const char *deDonde = nullptr;
  for (const char *ruta : rutas) {
    std::vector<uint8_t> datos;
    const Copia c = leer(ruta, &datos, maxCarga);
    if (c.valida && (!mejor.valida || c.generacion > mejor.generacion)) {
      mejor = c;
      deDonde = ruta;
      carga = std::move(datos);
    }
  }
  if (!mejor.valida) {
    generacion_ = 0;
    return false;
  }
  generacion_ = mejor.generacion;
  if (deDonde != bin_) {
    guardar(carga.data(), carga.size());  // la mejor pasa a .bin y la vieja a .bak
  } else if (fs_.existe(tmp_)) {
    fs_.borrar(tmp_);  // resto de un guardado que no llegó a terminar
  }
  return true;
}

bool ArchivoAtomico::guardar(const uint8_t *carga, size_t n) {
  const uint32_t generacion = generacion_ + 1;
  uint8_t pie[TAM_PIE];
  memcpy(pie, magia_, 4);
  pon32(pie + 4, generacion);
  pon32(pie + 8, crc32(pie, 8, crc32(carga, n)));

  {
    auto a = fs_.abrir(tmp_, Modo::NUEVO);
    if (!a) return false;
    for (size_t pos = 0; pos < n; pos += TROZO) {
      if (!a->agregar(carga + pos, n - pos < TROZO ? n - pos : TROZO)) return false;
    }
    if (!a->agregar(pie, TAM_PIE) || !a->vaciar()) return false;
  }
  if (fs_.existe(bak_) && !fs_.borrar(bak_)) return false;
  if (fs_.existe(bin_) && !fs_.renombrar(bin_, bak_)) return false;
  if (!fs_.renombrar(tmp_, bin_)) return false;
  generacion_ = generacion;
  return true;
}
