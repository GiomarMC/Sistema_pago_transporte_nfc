#include "Recargas.h"

#include <algorithm>

namespace {

void pon32(uint8_t *p, uint32_t v) {
  for (int i = 0; i < 4; i++) p[i] = v >> (8 * i);
}

uint32_t lee32(const uint8_t *p) {
  return p[0] | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

bool antes(const Recarga &a, const Recarga &b) { return a.cuenta != b.cuenta ? a.cuenta < b.cuenta : a.seq < b.seq; }

}  // namespace

bool Recargas::iniciar() {
  recargas_.clear();
  std::vector<uint8_t> carga;
  if (!archivo_.cargar(carga, MAX_RECARGAS * TAM_REGISTRO)) return false;
  if (carga.size() % TAM_REGISTRO != 0) return false;
  std::vector<Recarga> leidas;
  for (size_t i = 0; i < carga.size(); i += TAM_REGISTRO) {
    const uint8_t *p = carga.data() + i;
    const Recarga r{lee32(p), lee32(p + 4), static_cast<int32_t>(lee32(p + 8))};
    if (!leidas.empty() && !antes(leidas.back(), r)) return false;  // desordenada o repetida
    leidas.push_back(r);
  }
  recargas_ = std::move(leidas);
  return true;
}

bool Recargas::guardar(const std::vector<Recarga> &r) {
  std::vector<uint8_t> carga(r.size() * TAM_REGISTRO);
  for (size_t i = 0; i < r.size(); i++) {
    uint8_t *p = carga.data() + i * TAM_REGISTRO;
    pon32(p, r[i].cuenta);
    pon32(p + 4, r[i].seq);
    pon32(p + 8, static_cast<uint32_t>(r[i].monto));
  }
  return archivo_.guardar(carga.data(), carga.size());
}

bool Recargas::aplicar(const sp::Listas &listas) {
  std::vector<Recarga> nuevas;
  if (listas.versionDesde != 0) nuevas = recargas_;
  const size_t n = listas.n < sp::MAX_ENTRADAS ? listas.n : sp::MAX_ENTRADAS;
  for (size_t i = 0; i < n; i++) {
    const sp::EntradaLista &e = listas.entradas[i];
    if (e.tipo != sp::RECARGA) continue;
    const Recarga r{e.a, e.b, static_cast<int32_t>(e.c)};
    auto it = std::lower_bound(nuevas.begin(), nuevas.end(), r, antes);
    const bool esta = it != nuevas.end() && it->cuenta == r.cuenta && it->seq == r.seq;
    if (e.op == sp::PENDIENTE) {
      if (esta) {
        it->monto = r.monto;
      } else {
        if (nuevas.size() >= MAX_RECARGAS) return false;
        nuevas.insert(it, r);
      }
    } else if (e.op == sp::APLICADA && esta) {
      nuevas.erase(it);
    }
  }
  if (nuevas == recargas_) return true;
  if (!guardar(nuevas)) return false;
  recargas_ = std::move(nuevas);
  return true;
}

bool Recargas::reemplazar(std::vector<Recarga> nuevas) {
  // estable: de dos iguales (cuenta, seq) queda la última que llegó
  std::stable_sort(nuevas.begin(), nuevas.end(), antes);
  std::vector<Recarga> unicas;
  for (const Recarga &r : nuevas) {
    if (!unicas.empty() && unicas.back().cuenta == r.cuenta && unicas.back().seq == r.seq) {
      unicas.back() = r;
    } else {
      unicas.push_back(r);
    }
  }
  if (unicas.size() > MAX_RECARGAS) return false;
  if (unicas == recargas_) return true;
  if (!guardar(unicas)) return false;
  recargas_ = std::move(unicas);
  return true;
}

void Recargas::pendientes(uint32_t cuenta, uint32_t ultima, int32_t &total, uint32_t &hasta) const {
  total = 0;
  hasta = ultima;
  if (ultima == UINT32_MAX) return;
  const Recarga buscada{cuenta, ultima + 1, 0};
  auto it = std::lower_bound(recargas_.begin(), recargas_.end(), buscada, antes);
  while (it != recargas_.end() && it->cuenta == cuenta && it->seq == hasta + 1) {
    total += it->monto;
    hasta = it->seq;
    if (hasta == UINT32_MAX) break;
    ++it;
  }
}
