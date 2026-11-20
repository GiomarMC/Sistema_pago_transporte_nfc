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

constexpr uint8_t MAGIA[4] = {'E', 'S', 'T', '1'};

// magia(4) | generación | siguiente_id | hasta_acusado | version_listas | version_tarifas |
// secuencia_paquetes | crc32 de lo anterior
void codificar(uint32_t generacion, const Estado &e, uint8_t r[EstadoPersistente::TAM_REGISTRO]) {
  memcpy(r, MAGIA, 4);
  pon32(r + 4, generacion);
  pon32(r + 8, e.siguienteId);
  pon32(r + 12, e.hastaAcusado);
  pon32(r + 16, e.versionListas);
  pon32(r + 20, e.versionTarifas);
  pon32(r + 24, e.secuenciaPaquetes);
  pon32(r + 28, crc32(r, 28));
}

}  // namespace

EstadoPersistente::Copia EstadoPersistente::leer(const char *ruta) {
  Copia c;
  auto a = fs_.abrir(ruta, Modo::LECTURA);
  if (!a || a->tamano() != TAM_REGISTRO) return c;
  uint8_t r[TAM_REGISTRO];
  if (!a->leerEn(0, r, sizeof(r))) return c;
  if (memcmp(r, MAGIA, 4) != 0 || lee32(r + 28) != crc32(r, 28)) return c;
  c.generacion = lee32(r + 4);
  c.estado.siguienteId = lee32(r + 8);
  c.estado.hastaAcusado = lee32(r + 12);
  c.estado.versionListas = lee32(r + 16);
  c.estado.versionTarifas = lee32(r + 20);
  c.estado.secuenciaPaquetes = lee32(r + 24);
  c.valida = true;
  return c;
}

bool EstadoPersistente::cargar() {
  // La copia válida más nueva de las tres: así da igual en qué paso del guardado se cortó
  const char *rutas[] = {ARCHIVO, TEMPORAL, ANTERIOR};
  Copia mejor;
  const char *deDonde = nullptr;
  for (const char *ruta : rutas) {
    const Copia c = leer(ruta);
    if (c.valida && (!mejor.valida || c.generacion > mejor.generacion)) {
      mejor = c;
      deDonde = ruta;
    }
  }
  if (!mejor.valida) {
    estado_ = Estado();
    generacion_ = 0;
    secuenciaActual_ = 0;
    return false;
  }
  estado_ = mejor.estado;
  generacion_ = mejor.generacion;
  secuenciaActual_ = estado_.secuenciaPaquetes;  // lo reservado: lo anterior pudo haberse entregado
  if (strcmp(deDonde, ARCHIVO) != 0) {
    guardar();  // deja las tres copias ordenadas (la nueva pasa a .bin y la vieja a .bak)
  } else if (fs_.existe(TEMPORAL)) {
    fs_.borrar(TEMPORAL);  // resto de un guardado que no llegó a terminar
  }
  return true;
}

bool EstadoPersistente::guardar() {
  uint8_t registro[TAM_REGISTRO];
  codificar(generacion_ + 1, estado_, registro);

  {
    auto a = fs_.abrir(TEMPORAL, Modo::NUEVO);
    if (!a || !a->agregar(registro, sizeof(registro)) || !a->vaciar()) return false;
  }
  if (fs_.existe(ANTERIOR) && !fs_.borrar(ANTERIOR)) return false;
  if (fs_.existe(ARCHIVO) && !fs_.renombrar(ARCHIVO, ANTERIOR)) return false;
  if (!fs_.renombrar(TEMPORAL, ARCHIVO)) return false;
  generacion_++;
  return true;
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
