#include "EstadoPersistente.h"

#include <string.h>

#include "Crc32.h"

namespace {

void pon32(uint8_t *p, uint32_t v) {
  for (int i = 0; i < 4; i++) p[i] = v >> (8 * i);
}

uint32_t lee32(const uint8_t *p) {
  return p[0] | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

constexpr uint8_t MAGIA_VIEJA[4] = {'E', 'S', 'T', '1'};

// La carga de las ranuras: siguiente_id | hasta_acusado | version_listas | version_tarifas | secuencia_paquetes
void codificar(const Estado &e, uint8_t c[EstadoPersistente::TAM_CARGA]) {
  pon32(c, e.siguienteId);
  pon32(c + 4, e.hastaAcusado);
  pon32(c + 8, e.versionListas);
  pon32(c + 12, e.versionTarifas);
  pon32(c + 16, e.secuenciaPaquetes);
}

Estado decodificar(const uint8_t *c) {
  Estado e;
  e.siguienteId = lee32(c);
  e.hastaAcusado = lee32(c + 4);
  e.versionListas = lee32(c + 8);
  e.versionTarifas = lee32(c + 12);
  e.secuenciaPaquetes = lee32(c + 16);
  return e;
}

}  // namespace

// Formato anterior: magia "EST1" | generación | los cinco campos | crc32 de lo anterior (32 B)
EstadoPersistente::Copia EstadoPersistente::leerViejo(const char *ruta) {
  Copia c;
  if (!fs_.existe(ruta)) return c;
  auto a = fs_.abrir(ruta, Modo::LECTURA);
  if (!a || a->tamano() != TAM_REGISTRO) return c;
  uint8_t r[TAM_REGISTRO];
  if (!a->leerEn(0, r, sizeof(r))) return c;
  if (memcmp(r, MAGIA_VIEJA, 4) != 0 || lee32(r + 28) != crc32(r, 28)) return c;
  c.generacion = lee32(r + 4);
  c.estado = decodificar(r + 8);
  c.valida = true;
  return c;
}

void EstadoPersistente::borrarViejos() {
  for (const char *ruta : {ARCHIVO, TEMPORAL, ANTERIOR}) {
    if (fs_.existe(ruta)) fs_.borrar(ruta);
  }
}

bool EstadoPersistente::cargar() {
  std::vector<uint8_t> carga;
  if (ranuras_.cargar(carga, TAM_CARGA) && carga.size() == TAM_CARGA) {
    estado_ = decodificar(carga.data());
    secuenciaActual_ = estado_.secuenciaPaquetes;  // lo reservado: lo anterior pudo haberse entregado
    borrarViejos();  // restos de una migración que se cortó antes de borrarlos (si no hay, solo mira)
    return true;
  }
  // Formato anterior (o un .ab que se cortó al crearse, con los viejos aún ahí): la copia válida más nueva de las
  // tres, que se pasa a las ranuras; ya guardada, se borran
  Copia mejor;
  for (const char *ruta : {ARCHIVO, TEMPORAL, ANTERIOR}) {
    const Copia c = leerViejo(ruta);
    if (c.valida && (!mejor.valida || c.generacion > mejor.generacion)) mejor = c;
  }
  if (!mejor.valida) {
    estado_ = Estado();
    secuenciaActual_ = 0;
    return false;
  }
  estado_ = mejor.estado;
  secuenciaActual_ = estado_.secuenciaPaquetes;
  if (guardar()) borrarViejos();
  return true;
}

bool EstadoPersistente::guardar() {
  uint8_t carga[TAM_CARGA];
  codificar(estado_, carga);
  return ranuras_.guardar(carga, sizeof(carga));
}

bool EstadoPersistente::fijarSiguienteId(uint32_t id) {
  if (estado_.siguienteId == id) return true;
  const Estado antes = estado_;
  estado_.siguienteId = id;
  if (guardar()) return true;
  estado_ = antes;
  return false;
}

bool EstadoPersistente::fijarHastaAcusado(uint32_t id) {
  if (estado_.hastaAcusado == id) return true;
  const Estado antes = estado_;
  estado_.hastaAcusado = id;
  if (guardar()) return true;
  estado_ = antes;
  return false;
}

bool EstadoPersistente::fijarVersiones(uint32_t listas, uint32_t tarifas) {
  if (estado_.versionListas == listas && estado_.versionTarifas == tarifas) return true;
  const Estado antes = estado_;
  estado_.versionListas = listas;
  estado_.versionTarifas = tarifas;
  if (guardar()) return true;
  estado_ = antes;
  return false;
}

uint32_t EstadoPersistente::siguienteSecuencia() {
  if (secuenciaActual_ + 1 > estado_.secuenciaPaquetes) {
    // Se reserva un bloque por delante antes de entregar ningún número de él
    const Estado antes = estado_;
    estado_.secuenciaPaquetes = secuenciaActual_ + RESERVA_SECUENCIA;
    if (!guardar()) {
      estado_ = antes;
      return 0;
    }
  }
  return ++secuenciaActual_;
}
