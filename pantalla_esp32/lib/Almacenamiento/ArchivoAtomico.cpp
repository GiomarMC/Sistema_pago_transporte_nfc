#include "ArchivoAtomico.h"

#include <stdio.h>
#include <string.h>

#include "Crc32.h"

namespace {

constexpr size_t TROZO = 512;  // lo que se lee o escribe de una vez (un sector)

uint32_t lee32(const uint8_t *p) {
  return p[0] | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

}  // namespace

namespace {
const char *rutaAb(char *buf, size_t n, const char *base) {
  snprintf(buf, n, "%s.ab", base);
  return buf;
}
}  // namespace

ArchivoAtomico::ArchivoAtomico(SistemaArchivos &fs, const char *base, const char magia[4], bool mantenerAbierto)
    : fs_(fs), ranuras_(fs, rutaAb(ab_, sizeof(ab_), base), magia, mantenerAbierto) {
  snprintf(bin_, sizeof(bin_), "%s.bin", base);
  snprintf(tmp_, sizeof(tmp_), "%s.tmp", base);
  snprintf(bak_, sizeof(bak_), "%s.bak", base);
  memcpy(magia_, magia, 4);
}

ArchivoAtomico::Copia ArchivoAtomico::leerViejo(const char *ruta, std::vector<uint8_t> *carga, size_t maxCarga) {
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

bool ArchivoAtomico::cargarViejo(std::vector<uint8_t> &carga, size_t maxCarga) {
  carga.clear();
  const char *rutas[] = {bin_, tmp_, bak_};
  Copia mejor;
  for (const char *ruta : rutas) {
    std::vector<uint8_t> datos;
    const Copia c = leerViejo(ruta, &datos, maxCarga);
    if (c.valida && (!mejor.valida || c.generacion > mejor.generacion)) {
      mejor = c;
      carga = std::move(datos);
    }
  }
  return mejor.valida;
}

void ArchivoAtomico::borrarViejos() {
  for (const char *ruta : {bin_, tmp_, bak_}) {
    if (fs_.existe(ruta)) fs_.borrar(ruta);
  }
}

bool ArchivoAtomico::cargar(std::vector<uint8_t> &carga, size_t maxCarga) {
  if (ranuras_.cargar(carga, maxCarga)) {
    borrarViejos();  // restos de una migración que se cortó antes de borrarlos (si no hay, solo mira)
    return true;
  }
  // Formato anterior (o un .ab que se cortó al crearse, con los viejos aún ahí): se pasa a las ranuras y, ya
  // guardado, se borra
  if (!cargarViejo(carga, maxCarga)) return false;
  if (ranuras_.guardar(carga.data(), carga.size())) borrarViejos();
  return true;
}

bool ArchivoAtomico::guardar(const uint8_t *carga, size_t n) { return ranuras_.guardar(carga, n); }
