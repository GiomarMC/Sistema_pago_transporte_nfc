#include "almacen_sync_binario.h"

#include <string.h>

#include <new>

struct AlmacenSyncBinario::Trabajo {
  bool tarifas = false;
  bool urgente = false;
  size_t largo = 0;  // del paquete (LISTAS)
  union {
    sp::Listas listas;
    sp::Tarifas tarifasLista;
  };
  uint8_t paquete[sp::TAM_MAXIMO];
  Trabajo() { memset(&listas, 0, sizeof(listas)); }
};

AlmacenSyncBinario::~AlmacenSyncBinario() {
  for (Trabajo *&t : cola_t_) {
    delete t;
    t = nullptr;
  }
}

uint32_t AlmacenSyncBinario::ultimoAcusado() const {
  const uint32_t guardado = cola_.ultimoAcusado();
  return acusePendiente_ > guardado ? acusePendiente_ : guardado;
}

void AlmacenSyncBinario::acusarHasta(uint32_t hastaId) {
  if (hastaId > acusePendiente_) acusePendiente_ = hastaId;
}

void AlmacenSyncBinario::incidencia(const char *detalle) {
  if (hayIncidencia_ || !detalle) return;  // con una esperando, la otra se pierde (el sincronizador ya las espacia)
  strncpy(incidencia_, detalle, sizeof(incidencia_) - 1);
  incidencia_[sizeof(incidencia_) - 1] = '\0';
  hayIncidencia_ = true;
}

void AlmacenSyncBinario::aplicarListas(const sp::Listas &listas, const uint8_t *paquete, size_t largo) {
  if (nTrabajos_ >= COLA_TRABAJOS) {
    cuenta_.descartados++;  // el servidor lo repite en el siguiente HOLA
    return;
  }
  Trabajo *t = new (std::nothrow) Trabajo;
  if (!t) {
    cuenta_.descartados++;
    return;
  }
  t->urgente = listas.urgente;
  t->listas = listas;
  t->largo = (paquete && largo <= sizeof(t->paquete)) ? largo : 0;
  if (t->largo) memcpy(t->paquete, paquete, t->largo);
  cola_t_[nTrabajos_++] = t;
}

void AlmacenSyncBinario::aplicarTarifas(const sp::Tarifas &tarifas) {
  // TARIFAS es la lista completa: una nueva sustituye a otra que aún espera
  for (size_t i = 0; i < nTrabajos_; i++) {
    if (cola_t_[i]->tarifas) {
      if (tarifas.version >= cola_t_[i]->tarifasLista.version) cola_t_[i]->tarifasLista = tarifas;
      return;
    }
  }
  if (nTrabajos_ >= COLA_TRABAJOS) {
    cuenta_.descartados++;
    return;
  }
  Trabajo *t = new (std::nothrow) Trabajo;
  if (!t) {
    cuenta_.descartados++;
    return;
  }
  t->tarifas = true;
  t->tarifasLista = tarifas;
  cola_t_[nTrabajos_++] = t;
}

// Si la compactación falla varias veces seguidas (tarjeta con problemas) se deja en paz hasta el siguiente acuse
bool AlmacenSyncBinario::compactarCola() const { return cola_.compactacionPendiente() && fallosCompactar_ < 3; }

bool AlmacenSyncBinario::trabajoPendiente() const {
  return aplicador_.ocupado() || nTrabajos_ > 0 || hayIncidencia_ || acusePendiente_ > cola_.ultimoAcusado() || compactarCola() ||
         ajenos_.confirmadosPorBorrar() > 0;
}

void AlmacenSyncBinario::quitar(Trabajo *t) {
  for (size_t i = 0; i < nTrabajos_; i++) {
    if (cola_t_[i] != t) continue;
    for (size_t j = i + 1; j < nTrabajos_; j++) cola_t_[j - 1] = cola_t_[j];
    cola_t_[--nTrabajos_] = nullptr;
    break;
  }
  delete t;
}

AlmacenSyncBinario::Trabajo *AlmacenSyncBinario::siguienteTrabajo() {
  for (size_t i = 0; i < nTrabajos_; i++)
    if (cola_t_[i]->urgente) return cola_t_[i];  // un bloqueo urgente pasa por delante
  return nTrabajos_ ? cola_t_[0] : nullptr;
}

void AlmacenSyncBinario::contar(AplicadorListas::Resultado r) {
  using Res = AplicadorListas::Resultado;
  if (r == Res::FALLO) cuenta_.fallos++;
  else if (r == Res::APLICADA || r == Res::URGENTE) (enCursoTarifas_ ? cuenta_.tarifasAplicadas : cuenta_.listasAplicadas)++;
}

bool AlmacenSyncBinario::darPaso() {
  const AplicadorListas::Resultado r = aplicador_.avanzar();
  if (r != AplicadorListas::Resultado::EN_CURSO) contar(r);
  return true;
}

bool AlmacenSyncBinario::paso(bool lectorLibre) {
  if (!lectorLibre) return false;
  using Res = AplicadorListas::Resultado;

  // 1. Un trabajo a medias sigue antes que nada: no conviene dejarlo a medias mucho tiempo
  if (aplicador_.ocupado()) return darPaso();

  // 2. Una incidencia pendiente
  if (hayIncidencia_) {
    sp::Evento e;
    memset(&e, 0, sizeof(e));
    e.tipo = sp::INCIDENCIA;
    e.fecha = ahoraUnix_ ? ahoraUnix_() : 0;
    e.contador = sp::SIN_CONTADOR;
    strncpy(e.detalle, incidencia_, sizeof(e.detalle) - 1);
    hayIncidencia_ = false;
    if (cola_.agregar(e)) cuenta_.incidencias++;
    else cuenta_.fallos++;
    return true;
  }

  // 3. LISTAS / TARIFAS esperando: se empieza el trabajo (el aplicador copia lo que necesita) y se da su primer paso
  if (Trabajo *t = siguienteTrabajo()) {
    enCursoTarifas_ = t->tarifas;
    const Res r = t->tarifas ? aplicador_.empezarTarifas(t->tarifasLista)
                             : aplicador_.empezarListas(t->listas, t->largo ? t->paquete : nullptr, t->largo);
    quitar(t);
    if (r == Res::EN_CURSO) return darPaso();
    contar(r);  // ya tenía el resultado: repetida, salto, inválida...
    return true;
  }

  // 4. El acuse del servidor: guardarlo (una escritura del estado). La compactación de la cola, si ya sobra la
  // mitad, va aparte y en dos pasos: todo junto son ~2 s en la microSD real
  if (acusePendiente_ > cola_.ultimoAcusado()) {
    fallosCompactar_ = 0;
    if (!cola_.acusarHasta(acusePendiente_, false)) cuenta_.fallos++;
    if (cola_.ultimoAcusado() >= acusePendiente_) acusePendiente_ = 0;
    return true;
  }
  if (compactarCola()) {
    if (!cola_.compactarPaso()) {
      cuenta_.fallos++;
      fallosCompactar_++;
    }
    return true;
  }

  // 5. Un paquete ajeno que el servidor ya confirmó
  if (ajenos_.borrarConfirmados(1) > 0) {
    cuenta_.ajenosBorrados++;
    return true;
  }
  return false;
}
