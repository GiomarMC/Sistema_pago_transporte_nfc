#include "ArchivoRanuras.h"

#include <stdio.h>
#include <string.h>

#include "Crc32.h"

namespace {

constexpr size_t TROZO = 512;  // lo que se lee de una vez (un sector)

void pon32(uint8_t *p, uint32_t v) {
  for (int i = 0; i < 4; i++) p[i] = v >> (8 * i);
}

uint32_t lee32(const uint8_t *p) {
  return p[0] | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

}  // namespace

ArchivoRanuras::ArchivoRanuras(SistemaArchivos &fs, const char *ruta, const char magia[4], bool mantenerAbierto)
    : fs_(fs), mantenerAbierto_(mantenerAbierto) {
  snprintf(ruta_, sizeof(ruta_), "%s", ruta);
  snprintf(temporal_, sizeof(temporal_), "%sn", ruta);
  memcpy(magia_, magia, 4);
}

void ArchivoRanuras::cabecera(uint32_t generacion, const uint8_t *carga, size_t n, uint8_t c[TAM_CABECERA]) const {
  memcpy(c, magia_, 4);
  pon32(c + 4, generacion);
  pon32(c + 8, n);
  pon32(c + 12, crc32(carga, n, crc32(c, 12)));
}

ArchivoRanuras::Copia ArchivoRanuras::leerRanura(Archivo &a, size_t i, size_t tam, std::vector<uint8_t> *carga,
                                                 size_t maxCarga) {
  Copia c;
  const size_t base = i * tam;
  uint8_t cab[TAM_CABECERA];
  if (!a.leerEn(base, cab, sizeof(cab)) || memcmp(cab, magia_, 4) != 0) return c;
  const size_t n = lee32(cab + 8);
  if (n > maxCarga || TAM_CABECERA + n > tam) return c;
  std::vector<uint8_t> datos(n);
  uint32_t crc = crc32(cab, 12);
  for (size_t pos = 0; pos < n; pos += TROZO) {
    const size_t k = n - pos < TROZO ? n - pos : TROZO;
    if (!a.leerEn(base + TAM_CABECERA + pos, datos.data() + pos, k)) return c;
    crc = crc32(datos.data() + pos, k, crc);
  }
  if (crc != lee32(cab + 12)) return c;
  c.valida = true;
  c.generacion = lee32(cab + 4);
  if (carga) *carga = std::move(datos);
  return c;
}

bool ArchivoRanuras::hayArchivo() { return fs_.existe(ruta_) || fs_.existe(temporal_); }

bool ArchivoRanuras::abrir() {
  abierto_.reset();
  tamRanura_ = 0;
  if (!fs_.existe(ruta_)) return false;  // EDITAR no crea (y en la microSD abrir lo que no existe deja un aviso)
  abierto_ = fs_.abrir(ruta_, Modo::EDITAR);
  if (!abierto_) return false;
  const size_t tam = abierto_->tamano();
  if (tam < 2 * TAM_MINIMO || tam % (2 * TAM_MINIMO) != 0) {  // no es de este formato o se cortó al crearlo
    abierto_.reset();
    return false;
  }
  tamRanura_ = tam / 2;
  return true;
}

bool ArchivoRanuras::cargar(std::vector<uint8_t> &carga, size_t maxCarga) {
  carga.clear();
  abierto_.reset();
  tamRanura_ = 0;
  generacion_ = 0;
  vigente_ = -1;

  // El temporal de un rehacer() que no llegó al final: puede traer la copia más nueva (si ya se había borrado el
  // archivo) o sobrar (si no)
  Copia mejorTemporal;
  std::vector<uint8_t> delTemporal;
  if (fs_.existe(temporal_)) {
    auto t = fs_.abrir(temporal_, Modo::LECTURA);
    const size_t tam = t ? t->tamano() : 0;
    if (tam >= 2 * TAM_MINIMO && tam % (2 * TAM_MINIMO) == 0) {
      for (size_t i = 0; i < 2; i++) {
        std::vector<uint8_t> datos;
        const Copia c = leerRanura(*t, i, tam / 2, &datos, maxCarga);
        if (c.valida && (!mejorTemporal.valida || c.generacion > mejorTemporal.generacion)) {
          mejorTemporal = c;
          delTemporal = std::move(datos);
        }
      }
    }
  }

  Copia mejor;
  if (abrir()) {
    for (size_t i = 0; i < 2; i++) {
      std::vector<uint8_t> datos;
      const Copia c = leerRanura(*abierto_, i, tamRanura_, &datos, maxCarga);
      if (c.valida && (!mejor.valida || c.generacion > mejor.generacion)) {
        mejor = c;
        vigente_ = static_cast<int>(i);
        carga = std::move(datos);
      }
    }
  }

  if (mejorTemporal.valida && (!mejor.valida || mejorTemporal.generacion > mejor.generacion)) {
    // El temporal estaba completo y es lo más nuevo: pasa a ser el archivo
    abierto_.reset();
    if (fs_.existe(ruta_)) fs_.borrar(ruta_);
    fs_.renombrar(temporal_, ruta_);
    carga = std::move(delTemporal);
    generacion_ = mejorTemporal.generacion;
    vigente_ = -1;
    if (abrir()) {  // la ranura vigente es la de esa generación
      for (size_t i = 0; i < 2; i++) {
        if (leerRanura(*abierto_, i, tamRanura_, nullptr, maxCarga).generacion == generacion_) vigente_ = (int)i;
      }
    }
    if (!mantenerAbierto_) abierto_.reset();
    return true;
  }
  if (fs_.existe(temporal_)) fs_.borrar(temporal_);  // sobra: el archivo trae lo mismo o algo más nuevo
  if (!mantenerAbierto_) abierto_.reset();  // tamRanura_ y vigente_ siguen valiendo para el próximo guardado
  if (!mejor.valida) {
    carga.clear();
    vigente_ = -1;
    return false;
  }
  generacion_ = mejor.generacion;
  return true;
}

bool ArchivoRanuras::rehacer(const uint8_t *carga, size_t n) {
  // Tamaño de ranura: la potencia de dos (desde 512) en que quepa la carga
  size_t tam = TAM_MINIMO;
  while (tam < TAM_CABECERA + n) tam *= 2;
  const uint32_t generacion = generacion_ + 1;
  abierto_.reset();
  {
    auto t = fs_.abrir(temporal_, Modo::NUEVO);
    if (!t) return false;
    uint8_t cab[TAM_CABECERA];
    cabecera(generacion, carga, n, cab);
    if (!t->agregar(cab, sizeof(cab))) return false;
    for (size_t pos = 0; pos < n; pos += TROZO) {
      if (!t->agregar(carga + pos, n - pos < TROZO ? n - pos : TROZO)) return false;
    }
    // El relleno de la ranura 0 y la ranura 1 vacía (magia en blanco: no vale hasta el primer guardar())
    static const uint8_t ceros[TROZO] = {};
    for (size_t resto = 2 * tam - TAM_CABECERA - n; resto > 0;) {
      const size_t k = resto < TROZO ? resto : TROZO;
      if (!t->agregar(ceros, k)) return false;
      resto -= k;
    }
    if (!t->vaciar()) return false;
  }
  if (fs_.existe(ruta_) && !fs_.borrar(ruta_)) return false;
  if (!fs_.renombrar(temporal_, ruta_)) return false;
  generacion_ = generacion;
  vigente_ = 0;
  if (mantenerAbierto_) abrir();  // si no abre, el próximo guardar() lo vuelve a intentar
  else tamRanura_ = tam;
  return true;
}

bool ArchivoRanuras::guardar(const uint8_t *carga, size_t n) {
  if (!abierto_ && fs_.existe(ruta_)) abrir();  // sin mantenerAbierto, tras soltar() o tras un fallo
  if (!abierto_ || TAM_CABECERA + n > tamRanura_) return rehacer(carga, n);
  const size_t ranura = vigente_ == 0 ? 1 : 0;
  const uint32_t generacion = generacion_ + 1;
  std::vector<uint8_t> buf(TAM_CABECERA + n);
  cabecera(generacion, carga, n, buf.data());
  if (n) memcpy(buf.data() + TAM_CABECERA, carga, n);
  if (!abierto_->escribirEn(ranura * tamRanura_, buf.data(), buf.size()) || !abierto_->vaciar()) {
    abierto_.reset();  // se vuelve a abrir en el próximo guardado; la ranura vigente sigue intacta
    return false;
  }
  generacion_ = generacion;
  vigente_ = static_cast<int>(ranura);
  if (!mantenerAbierto_) abierto_.reset();
  return true;
}
