#include "AplicadorListas.h"

#include <vector>

AplicadorListas::Resultado AplicadorListas::urgente(const sp::Listas &l, const uint8_t *paquete, size_t largo) {
  // Regla 4: solo si trae algo que la lista en orden aún no tiene, y sin tocar version_listas
  if (l.versionHasta <= estado_.estado().versionListas) return Resultado::REPETIDA;
  bool ok = true;
  const size_t n = l.n < sp::MAX_ENTRADAS ? l.n : sp::MAX_ENTRADAS;
  for (size_t i = 0; i < n; i++) {
    const sp::EntradaLista &e = l.entradas[i];
    if (e.tipo == sp::BLOQUEO && e.op == sp::AGREGAR) ok = listaNegra_.bloqueoUrgente(e.a) && ok;  // bloqueada en RAM aunque falle
  }
  if (paquete) firmados_.guardar(paquete, largo);
  return ok ? Resultado::URGENTE : Resultado::FALLO;
}

AplicadorListas::Resultado AplicadorListas::listas(const sp::Listas &l, const uint8_t *paquete, size_t largo) {
  if (l.urgente) return urgente(l, paquete, largo);
  // Un paquete normal no retrocede. La excepción: el último de una foto encadenada parte de una versión intermedia
  // (mayor que 2^31, que no es real) y termina en la versión de verdad, que es menor
  if (l.versionDesde < VERSION_INTERMEDIA && l.versionHasta < l.versionDesde) return Resultado::INVALIDA;

  // Reglas 2 y 3: contigüidad (o foto completa si version_desde = 0)
  const uint32_t local = estado_.estado().versionListas;
  const bool foto = l.versionDesde == 0;
  if (!foto && l.versionDesde > local) return Resultado::SALTO;
  if (!foto && l.versionDesde < local) return Resultado::REPETIDA;

  std::vector<ListaNegra::Cambio> cambios;
  const size_t n = l.n < sp::MAX_ENTRADAS ? l.n : sp::MAX_ENTRADAS;
  for (size_t i = 0; i < n; i++) {
    const sp::EntradaLista &e = l.entradas[i];
    if (e.tipo != sp::BLOQUEO) continue;
    if (e.op == sp::AGREGAR) cambios.push_back({e.a, true});
    else if (e.op == sp::QUITAR) cambios.push_back({e.a, false});
  }

  // Los urgentes se conservan al reconstruir: puede que la foto aún no recoja alguno
  if (foto && !listaNegra_.reiniciarListas(true)) return Resultado::FALLO;
  if (!cambios.empty()) {
    if (!listaNegra_.aplicarCambios(cambios.data(), cambios.size())) return Resultado::FALLO;
    ListaNegra::Progreso p;
    do {
      p = listaNegra_.avanzar(1024);
    } while (p == ListaNegra::Progreso::EN_CURSO);
    if (p != ListaNegra::Progreso::TERMINADA) return Resultado::FALLO;
  }
  if (!recargas_.aplicar(l)) return Resultado::FALLO;
  // La versión se guarda la última: un corte antes deja la anterior y el paquete se repite
  if (!estado_.fijarVersiones(l.versionHasta, estado_.estado().versionTarifas)) return Resultado::FALLO;

  // Lo que la lista ya recoge (o lo que un cambio en orden desbloquea) deja de ser urgente
  for (const ListaNegra::Cambio &c : cambios) listaNegra_.quitarUrgente(c.id);
  if (paquete) firmados_.guardar(paquete, largo);
  return Resultado::APLICADA;
}

AplicadorListas::Resultado AplicadorListas::tarifas(const sp::Tarifas &t) {
  if (t.version < estado_.estado().versionTarifas) return Resultado::REPETIDA;
  if (!tarifas_.guardar(t)) return Resultado::FALLO;
  if (!estado_.fijarVersiones(estado_.estado().versionListas, t.version)) return Resultado::FALLO;
  return Resultado::APLICADA;
}
