#include "AplicadorListas.h"

#include <string.h>

#include <new>
#include <vector>

// El trabajo en curso. Va en el montón (unos 6 KB) y solo mientras dura.
struct AplicadorListas::Trabajo {
  enum class Fase : uint8_t {
    // LISTAS normal
    REINICIAR, EMPEZAR_MEZCLA, MEZCLAR, RECARGAS, VERSION, QUITAR_URGENTES, GUARDAR_FIRMADO,
    // LISTAS urgente
    BLOQUEAR, GUARDAR_FIRMADO_URGENTE,
    // TARIFAS
    GUARDAR_TARIFAS, VERSION_TARIFAS,
  };
  Fase fase;
  bool foto = false;
  bool urgente = false;
  bool ok = true;   // urgentes: si todos los bloqueos se pudieron guardar
  size_t i = 0;     // por dónde va el paso que recorre una lista
  size_t largo = 0;
  sp::Listas listas;
  sp::Tarifas tarifas;
  std::vector<ListaNegra::Cambio> cambios;
  uint8_t paquete[sp::TAM_MAXIMO];
};

AplicadorListas::~AplicadorListas() { delete trabajo_; }

const char *AplicadorListas::fase() const {
  if (!trabajo_) return "";
  using Fase = Trabajo::Fase;
  switch (trabajo_->fase) {
    case Fase::REINICIAR: return "reiniciar listas";
    case Fase::EMPEZAR_MEZCLA: return "empezar mezcla";
    case Fase::MEZCLAR: return "mezclar lista negra";
    case Fase::RECARGAS: return "recargas";
    case Fase::VERSION: return "guardar version";
    case Fase::QUITAR_URGENTES: return "quitar urgentes";
    case Fase::GUARDAR_FIRMADO: return "guardar firmado";
    case Fase::BLOQUEAR: return "bloqueo urgente";
    case Fase::GUARDAR_FIRMADO_URGENTE: return "guardar firmado (urgente)";
    case Fase::GUARDAR_TARIFAS: return "guardar tarifas";
    case Fase::VERSION_TARIFAS: return "guardar version tarifas";
  }
  return "";
}

AplicadorListas::Resultado AplicadorListas::terminar(Resultado r) {
  delete trabajo_;
  trabajo_ = nullptr;
  return r;
}

AplicadorListas::Resultado AplicadorListas::empezarListas(const sp::Listas &l, const uint8_t *paquete, size_t largo) {
  if (trabajo_) return Resultado::OCUPADO;
  if (paquete && largo > sp::TAM_MAXIMO) paquete = nullptr;  // no cabe: no sería un paquete válido

  if (l.urgente) {
    // Regla 4: solo si trae algo que la lista en orden aún no tiene, y sin tocar version_listas
    if (l.versionHasta <= estado_.estado().versionListas) return Resultado::REPETIDA;
  } else {
    // Un paquete normal no retrocede. La excepción: el último de una foto encadenada parte de una versión
    // intermedia (mayor que 2^31, que no es real) y termina en la versión de verdad, que es menor
    if (l.versionDesde < VERSION_INTERMEDIA && l.versionHasta < l.versionDesde) return Resultado::INVALIDA;
    // Reglas 2 y 3: contigüidad (o foto completa si version_desde = 0)
    const uint32_t local = estado_.estado().versionListas;
    if (l.versionDesde != 0 && l.versionDesde > local) return Resultado::SALTO;
    if (l.versionDesde != 0 && l.versionDesde < local) return Resultado::REPETIDA;
  }

  Trabajo *t = new (std::nothrow) Trabajo;
  if (!t) return Resultado::FALLO;
  t->listas = l;
  t->urgente = l.urgente;
  t->foto = !l.urgente && l.versionDesde == 0;
  t->largo = paquete ? largo : 0;
  if (paquete) memcpy(t->paquete, paquete, largo);
  const size_t n = l.n < sp::MAX_ENTRADAS ? l.n : sp::MAX_ENTRADAS;
  t->listas.n = static_cast<uint8_t>(n);
  if (!l.urgente) {
    for (size_t i = 0; i < n; i++) {
      const sp::EntradaLista &e = l.entradas[i];
      if (e.tipo != sp::BLOQUEO) continue;
      if (e.op == sp::AGREGAR) t->cambios.push_back({e.a, true});
      else if (e.op == sp::QUITAR) t->cambios.push_back({e.a, false});
    }
  }
  using Fase = Trabajo::Fase;
  t->fase = l.urgente ? Fase::BLOQUEAR : (t->foto ? Fase::REINICIAR : Fase::EMPEZAR_MEZCLA);
  trabajo_ = t;
  return Resultado::EN_CURSO;
}

AplicadorListas::Resultado AplicadorListas::empezarTarifas(const sp::Tarifas &tf) {
  if (trabajo_) return Resultado::OCUPADO;
  if (tf.version < estado_.estado().versionTarifas) return Resultado::REPETIDA;
  Trabajo *t = new (std::nothrow) Trabajo;
  if (!t) return Resultado::FALLO;
  t->tarifas = tf;
  t->fase = Trabajo::Fase::GUARDAR_TARIFAS;
  trabajo_ = t;
  return Resultado::EN_CURSO;
}

AplicadorListas::Resultado AplicadorListas::avanzar() {
  if (!trabajo_) return Resultado::FALLO;
  const bool esTarifas = trabajo_->fase == Trabajo::Fase::GUARDAR_TARIFAS || trabajo_->fase == Trabajo::Fase::VERSION_TARIFAS;
  return esTarifas ? pasoTarifas() : pasoListas();
}

AplicadorListas::Resultado AplicadorListas::pasoTarifas() {
  Trabajo &t = *trabajo_;
  if (t.fase == Trabajo::Fase::GUARDAR_TARIFAS) {
    if (!tarifas_.guardar(t.tarifas)) return terminar(Resultado::FALLO);
    t.fase = Trabajo::Fase::VERSION_TARIFAS;
    return Resultado::EN_CURSO;
  }
  // La versión se guarda la última: un corte antes deja la anterior y el paquete se repite
  const bool ok = estado_.fijarVersiones(estado_.estado().versionListas, t.tarifas.version);
  return terminar(ok ? Resultado::APLICADA : Resultado::FALLO);
}

AplicadorListas::Resultado AplicadorListas::pasoListas() {
  using Fase = Trabajo::Fase;
  Trabajo &t = *trabajo_;
  switch (t.fase) {
    case Fase::BLOQUEAR: {
      // Un urgente solo bloquea (un desbloqueo urgente se ignora: desbloquear va siempre en orden). Un bloqueo por
      // paso: cada uno se guarda y vacía. Queda bloqueada en RAM aunque no se pueda guardar.
      while (t.i < t.listas.n) {
        const sp::EntradaLista &e = t.listas.entradas[t.i++];
        if (e.tipo == sp::BLOQUEO && e.op == sp::AGREGAR) {
          t.ok = listaNegra_.bloqueoUrgente(e.a) && t.ok;
          return Resultado::EN_CURSO;
        }
      }
      if (!t.largo) return terminar(t.ok ? Resultado::URGENTE : Resultado::FALLO);
      t.fase = Fase::GUARDAR_FIRMADO_URGENTE;
      return Resultado::EN_CURSO;
    }
    case Fase::GUARDAR_FIRMADO_URGENTE:
      firmados_.guardar(t.paquete, t.largo);
      return terminar(t.ok ? Resultado::URGENTE : Resultado::FALLO);

    case Fase::REINICIAR:
      // Los urgentes se conservan al reconstruir: puede que la foto aún no recoja alguno
      if (!listaNegra_.reiniciarListas(true)) return terminar(Resultado::FALLO);
      t.fase = Fase::EMPEZAR_MEZCLA;
      return Resultado::EN_CURSO;

    case Fase::EMPEZAR_MEZCLA:
      if (t.cambios.empty()) {
        t.fase = Fase::RECARGAS;
        return Resultado::EN_CURSO;
      }
      if (!listaNegra_.aplicarCambios(t.cambios.data(), t.cambios.size())) return terminar(Resultado::FALLO);
      t.fase = Fase::MEZCLAR;
      return Resultado::EN_CURSO;

    case Fase::MEZCLAR: {
      const ListaNegra::Progreso p = listaNegra_.avanzar(IDS_POR_PASO);
      if (p == ListaNegra::Progreso::EN_CURSO) return Resultado::EN_CURSO;
      if (p != ListaNegra::Progreso::TERMINADA) return terminar(Resultado::FALLO);
      t.fase = Fase::RECARGAS;
      return Resultado::EN_CURSO;
    }

    case Fase::RECARGAS:
      if (!recargas_.aplicar(t.listas)) return terminar(Resultado::FALLO);
      t.fase = Fase::VERSION;
      return Resultado::EN_CURSO;

    case Fase::VERSION:
      // La versión se guarda la última: un corte antes deja la anterior y el paquete se repite
      if (!estado_.fijarVersiones(t.listas.versionHasta, estado_.estado().versionTarifas)) return terminar(Resultado::FALLO);
      t.fase = Fase::QUITAR_URGENTES;
      t.i = 0;
      return Resultado::EN_CURSO;

    case Fase::QUITAR_URGENTES:
      // Lo que la lista ya recoge (o lo que un cambio en orden desbloquea) deja de ser urgente. Cada uno que
      // estaba cuesta un guardado; los que no estaban no cuestan nada, así que se pasa por encima de ellos.
      while (t.i < t.cambios.size()) {
        const uint32_t id = t.cambios[t.i++].id;
        const size_t antes = listaNegra_.cantidadUrgentes();
        listaNegra_.quitarUrgente(id);
        if (listaNegra_.cantidadUrgentes() != antes) return Resultado::EN_CURSO;
      }
      t.fase = Fase::GUARDAR_FIRMADO;
      return t.largo ? Resultado::EN_CURSO : terminar(Resultado::APLICADA);

    case Fase::GUARDAR_FIRMADO:
      firmados_.guardar(t.paquete, t.largo);
      return terminar(Resultado::APLICADA);

    default:
      return terminar(Resultado::FALLO);
  }
}

AplicadorListas::Resultado AplicadorListas::listas(const sp::Listas &l, const uint8_t *paquete, size_t largo) {
  Resultado r = empezarListas(l, paquete, largo);
  while (r == Resultado::EN_CURSO) r = avanzar();
  return r;
}

AplicadorListas::Resultado AplicadorListas::tarifas(const sp::Tarifas &t) {
  Resultado r = empezarTarifas(t);
  while (r == Resultado::EN_CURSO) r = avanzar();
  return r;
}
