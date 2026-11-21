#include "ConsumoDatos.h"

#include <algorithm>

namespace {

void pon32(uint8_t *p, uint32_t v) {
  for (int i = 0; i < 4; i++) p[i] = v >> (8 * i);
}

uint32_t lee32(const uint8_t *p) {
  return p[0] | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

bool fechaPlausible(uint32_t f) {
  const uint32_t a = f / 10000, m = f / 100 % 100, d = f % 100;
  return a >= 2020 && a <= 2200 && m >= 1 && m <= 12 && d >= 1 && d <= 31;
}

}  // namespace

uint32_t ConsumoDatos::fechaDeUnix(uint32_t unix) {
  // días desde 1970-01-01 a año/mes/día (algoritmo civil de Howard Hinnant)
  int64_t z = static_cast<int64_t>(unix / 86400) + 719468;
  const int64_t era = z / 146097;
  const int64_t doe = z - era * 146097;
  const int64_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  const int64_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  const int64_t mp = (5 * doy + 2) / 153;
  const int64_t d = doy - (153 * mp + 2) / 5 + 1;
  const int64_t m = mp < 10 ? mp + 3 : mp - 9;
  const int64_t y = yoe + era * 400 + (m <= 2);
  return static_cast<uint32_t>(y * 10000 + m * 100 + d);
}

bool ConsumoDatos::iniciar() {
  dias_.clear();
  sucio_ = false;
  std::vector<uint8_t> carga;
  if (!archivo_.cargar(carga, MAX_DIAS * TAM_REGISTRO)) return false;
  if (carga.size() % TAM_REGISTRO != 0) return false;
  std::vector<DiaConsumo> leidos;
  for (size_t i = 0; i < carga.size(); i += TAM_REGISTRO) {
    const uint8_t *p = carga.data() + i;
    DiaConsumo d;
    d.fecha = lee32(p);
    for (size_t c = 0; c < N_CANALES; c++) {
      d.tx[c] = lee32(p + 4 + 4 * c);
      d.rx[c] = lee32(p + 16 + 4 * c);
    }
    if (!fechaPlausible(d.fecha) || (!leidos.empty() && d.fecha <= leidos.back().fecha)) return false;
    leidos.push_back(d);
  }
  dias_ = std::move(leidos);
  return true;
}

DiaConsumo *ConsumoDatos::hoy() {
  if (fecha_ == 0) return nullptr;
  auto it = std::lower_bound(dias_.begin(), dias_.end(), fecha_, [](const DiaConsumo &d, uint32_t f) { return d.fecha < f; });
  if (it != dias_.end() && it->fecha == fecha_) return &*it;
  // con el historial lleno, un día anterior a todos los guardados no cabe (reloj corregido hacia atrás)
  if (dias_.size() >= MAX_DIAS && it == dias_.begin()) return nullptr;
  DiaConsumo nuevo;
  nuevo.fecha = fecha_;
  it = dias_.insert(it, nuevo);
  if (dias_.size() > MAX_DIAS) {  // se pierde el día más antiguo
    const size_t pos = it - dias_.begin();
    dias_.erase(dias_.begin());
    it = dias_.begin() + (pos - 1);
  }
  return &*it;
}

const DiaConsumo *ConsumoDatos::hoy() const {
  if (fecha_ == 0) return nullptr;
  auto it = std::lower_bound(dias_.begin(), dias_.end(), fecha_, [](const DiaConsumo &d, uint32_t f) { return d.fecha < f; });
  return it != dias_.end() && it->fecha == fecha_ ? &*it : nullptr;
}

void ConsumoDatos::fijarFecha(uint32_t fecha) {
  if (fecha == fecha_) return;
  fecha_ = fecha;
  if (fecha_ == 0) return;
  // lo contado antes de saber la hora pasa al primer día conocido
  bool hay = false;
  for (size_t c = 0; c < N_CANALES; c++) hay = hay || sinFecha_.tx[c] || sinFecha_.rx[c];
  if (!hay) return;
  DiaConsumo *d = hoy();
  if (d) {
    for (size_t c = 0; c < N_CANALES; c++) {
      suma(d->tx[c], sinFecha_.tx[c]);
      suma(d->rx[c], sinFecha_.rx[c]);
    }
    sucio_ = true;
  }
  sinFecha_ = DiaConsumo();
}

void ConsumoDatos::registrar(CanalDatos canal, uint32_t tx, uint32_t rx) {
  const size_t c = static_cast<size_t>(canal);
  if (c >= N_CANALES || (tx == 0 && rx == 0)) return;
  if (fecha_ == 0) {
    suma(sinFecha_.tx[c], tx);
    suma(sinFecha_.rx[c], rx);
    return;
  }
  DiaConsumo *d = hoy();
  if (!d) return;
  suma(d->tx[c], tx);
  suma(d->rx[c], rx);
  sucio_ = true;
}

void ConsumoDatos::observar(CanalDatos canal, uint32_t txTotal, uint32_t rxTotal) {
  const size_t c = static_cast<size_t>(canal);
  if (c >= N_CANALES) return;
  // el contador del enlace cuenta desde su arranque; si baja, el enlace se reinició y vale entero
  const uint32_t dTx = (!visto_[c] || txTotal < ultimoTx_[c]) ? txTotal : txTotal - ultimoTx_[c];
  const uint32_t dRx = (!visto_[c] || rxTotal < ultimoRx_[c]) ? rxTotal : rxTotal - ultimoRx_[c];
  visto_[c] = true;
  ultimoTx_[c] = txTotal;
  ultimoRx_[c] = rxTotal;
  registrar(canal, dTx, dRx);
}

void ConsumoDatos::bytesHoyCanal(CanalDatos canal, uint32_t &tx, uint32_t &rx) const {
  tx = rx = 0;
  const size_t c = static_cast<size_t>(canal);
  if (c >= N_CANALES) return;
  const DiaConsumo *d = fecha_ == 0 ? &sinFecha_ : hoy();
  if (!d) return;
  tx = d->tx[c];
  rx = d->rx[c];
}

void ConsumoDatos::bytesHoy(uint32_t &tx, uint32_t &rx) const {
  tx = rx = 0;
  for (size_t c = 0; c < N_CANALES; c++) {
    uint32_t t, r;
    bytesHoyCanal(static_cast<CanalDatos>(c), t, r);
    suma(tx, t);
    suma(rx, r);
  }
}

bool ConsumoDatos::guardar() {
  if (!sucio_) return true;
  std::vector<uint8_t> carga(dias_.size() * TAM_REGISTRO);
  for (size_t i = 0; i < dias_.size(); i++) {
    uint8_t *p = carga.data() + i * TAM_REGISTRO;
    pon32(p, dias_[i].fecha);
    for (size_t c = 0; c < N_CANALES; c++) {
      pon32(p + 4 + 4 * c, dias_[i].tx[c]);
      pon32(p + 16 + 4 * c, dias_[i].rx[c]);
    }
  }
  if (!archivo_.guardar(carga.data(), carga.size())) return false;
  sucio_ = false;
  return true;
}

bool ConsumoDatos::atender(uint32_t ahoraMs) {
  if (!relojIniciado_) {  // el primer guardado es a los 5 minutos de empezar a contar
    relojIniciado_ = true;
    ultimoGuardadoMs_ = ahoraMs;
    return false;
  }
  if (!sucio_ || ahoraMs - ultimoGuardadoMs_ < INTERVALO_MS) return false;
  ultimoGuardadoMs_ = ahoraMs;  // aunque falle, no se reintenta antes de otros 5 minutos
  return guardar();
}
