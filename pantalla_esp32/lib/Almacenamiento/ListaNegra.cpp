#include "ListaNegra.h"

#include <stdio.h>
#include <string.h>

#include <algorithm>

#include "Crc32.h"

namespace {

constexpr uint32_t MAGIA = 0x314E4C53;  // "SLN1"
constexpr uint32_t MASCARA_URGENTE = 0x55AA55AAu;
constexpr size_t TAM_URGENTE = 8;  // id y id ^ MASCARA: un registro a medio escribir no pasa la comprobación

void pon32(uint8_t *p, uint32_t v) {
  p[0] = v;
  p[1] = v >> 8;
  p[2] = v >> 16;
  p[3] = v >> 24;
}
uint32_t lee32(const uint8_t *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }

// Los identificadores se guardan tal cual en memoria: el ESP32 (y el PC de pruebas) son little-endian

}  // namespace

// Una mezcla de cambios con la lista en curso
struct ListaNegra::Mezcla {
  enum class Fase : uint8_t { MEZCLANDO, PIE, CAMBIAR };
  Fase fase = Fase::MEZCLANDO;
  std::vector<Cambio> cambios;  // ordenados por id y sin repetir
  size_t c = 0;
  std::unique_ptr<Archivo> viejo, nuevo;
  size_t viejoTotal = 0, viejoLeidos = 0;
  uint32_t lectura[POR_LECTURA];
  size_t lecturaPos = 0, lecturaLen = 0;
  bool tieneA = false, finViejo = false, error = false;
  uint32_t a = 0;  // el siguiente id de la lista vieja
  uint32_t salida[POR_LECTURA];
  size_t salidaLen = 0;
  uint32_t crc = 0, escritas = 0, sinVaciar = 0, generacion = 0;
  std::vector<uint32_t> indiceNuevo;
};

ListaNegra::ListaNegra(SistemaArchivos &fs, const char *base, const char *urgentes) : fs_(fs) {
  snprintf(bin_, sizeof(bin_), "%s.bin", base);
  snprintf(tmp_, sizeof(tmp_), "%s.tmp", base);
  snprintf(bak_, sizeof(bak_), "%s.bak", base);
  snprintf(urg_, sizeof(urg_), "%s.bin", urgentes);
  snprintf(urgTmp_, sizeof(urgTmp_), "%s.tmp", urgentes);
}

ListaNegra::~ListaNegra() = default;  // aquí, donde Mezcla ya está completa

// --- Arranque ------------------------------------------------------------------------------

ListaNegra::Pie ListaNegra::leerPie(const char *ruta) {
  Pie pie;
  auto f = fs_.abrir(ruta, Modo::LECTURA);
  if (!f) return pie;
  const size_t tam = f->tamano();
  if (tam < TAM_PIE || (tam - TAM_PIE) % 4 != 0) return pie;
  uint8_t p[TAM_PIE];
  if (!f->leerEn(tam - TAM_PIE, p, TAM_PIE) || lee32(p) != MAGIA) return pie;
  pie.generacion = lee32(p + 4);
  pie.cuenta = lee32(p + 8);
  pie.crc = lee32(p + 12);
  pie.valido = pie.cuenta == (tam - TAM_PIE) / 4 && pie.cuenta <= MAX_IDS;
  return pie;
}

bool ListaNegra::verificar(const char *ruta, const Pie &pie, std::vector<uint32_t> &indice) {
  auto f = fs_.abrir(ruta, Modo::LECTURA);
  if (!f) return false;
  indice.clear();
  indice.reserve(pie.cuenta / BLOQUE + 1);
  uint32_t buf[POR_LECTURA];
  uint32_t crc = 0, anterior = 0;
  size_t leidos = 0;
  while (leidos < pie.cuenta) {
    const size_t n = std::min(POR_LECTURA, pie.cuenta - leidos);
    if (!f->leerEn(leidos * 4, buf, n * 4)) return false;
    crc = crc32(buf, n * 4, crc);
    for (size_t i = 0; i < n; i++) {
      if (leidos + i > 0 && buf[i] <= anterior) return false;  // debe ser estrictamente creciente
      anterior = buf[i];
      if ((leidos + i) % BLOQUE == 0) indice.push_back(buf[i]);
    }
    leidos += n;
  }
  return crc == pie.crc;
}

bool ListaNegra::reparar() {
  struct Candidata {
    const char *ruta;
    Pie pie;
  };
  Candidata cs[3] = {{bin_, leerPie(bin_)}, {tmp_, leerPie(tmp_)}, {bak_, leerPie(bak_)}};
  // La válida de mayor generación primero; a igualdad, el orden bin, tmp, bak
  std::stable_sort(cs, cs + 3, [](const Candidata &x, const Candidata &y) {
    if (x.pie.valido != y.pie.valido) return x.pie.valido;
    return x.pie.generacion > y.pie.generacion;
  });
  for (const Candidata &c : cs) {
    if (!c.pie.valido) continue;
    std::vector<uint32_t> indice;
    if (!verificar(c.ruta, c.pie, indice)) continue;
    if (c.ruta == tmp_) {  // una mezcla que llegó a terminar pero se cortó antes de cambiar de archivo
      fs_.borrar(bak_);
      if (fs_.existe(bin_)) fs_.renombrar(bin_, bak_);
      if (!fs_.renombrar(tmp_, bin_)) return false;
    } else if (c.ruta == bak_) {
      if (fs_.existe(bin_)) fs_.borrar(bin_);
      if (!fs_.renombrar(bak_, bin_)) return false;
    }
    cuenta_ = c.pie.cuenta;
    generacion_ = c.pie.generacion;
    indice_ = std::move(indice);
    if (fs_.existe(tmp_)) fs_.borrar(tmp_);  // lo que quede es una mezcla vieja o incompleta
    return true;
  }
  if (fs_.existe(tmp_)) fs_.borrar(tmp_);
  return false;
}

bool ListaNegra::iniciar() {
  abortarMezcla();
  archivo_.reset();
  indice_.clear();
  bloqueLeido_ = -1;
  cuenta_ = 0;
  generacion_ = 0;
  cargarUrgentes();
  const bool hay = reparar();
  if (hay) abrirLista();
  return hay;
}

bool ListaNegra::abrirLista() {
  archivo_ = fs_.abrir(bin_, Modo::LECTURA);
  return archivo_ != nullptr;
}

// --- Consulta ------------------------------------------------------------------------------

bool ListaNegra::bloqueada(uint32_t id) {
  for (uint32_t u : urgentes_)
    if (u == id) return true;
  if (cuenta_ == 0 || indice_.empty()) return false;

  // El último bloque cuyo primer id es <= id: solo ahí puede estar
  const auto it = std::upper_bound(indice_.begin(), indice_.end(), id);
  if (it == indice_.begin()) return false;
  const size_t k = (it - indice_.begin()) - 1;
  if ((long)k != bloqueLeido_) {
    if (!archivo_ && !abrirLista()) return false;
    const size_t desde = k * BLOQUE;
    const size_t n = std::min(BLOQUE, cuenta_ - desde);
    bloque_.resize(BLOQUE);
    if (!archivo_->leerEn(desde * 4, bloque_.data(), n * 4)) {
      bloqueLeido_ = -1;
      return false;
    }
    bloqueN_ = n;
    bloqueLeido_ = (long)k;
  }
  return std::binary_search(bloque_.begin(), bloque_.begin() + bloqueN_, id);
}

// --- Cambios -------------------------------------------------------------------------------

bool ListaNegra::iniciarMezcla(const Cambio *entradas, size_t n, bool desdeCero) {
  if (mezcla_) return false;
  std::vector<Cambio> todos(entradas, entradas + n);
  std::stable_sort(todos.begin(), todos.end(), [](const Cambio &x, const Cambio &y) { return x.id < y.id; });
  std::vector<Cambio> unicos;  // de los repetidos vale el último (el más reciente)
  size_t altas = 0;
  for (size_t i = 0; i < todos.size(); i++) {
    if (i + 1 < todos.size() && todos[i + 1].id == todos[i].id) continue;
    unicos.push_back(todos[i]);
    if (todos[i].alta) altas++;
  }
  const size_t base = desdeCero ? 0 : cuenta_;
  if (base + altas > MAX_IDS) return false;

  std::unique_ptr<Mezcla> m(new Mezcla());
  m->cambios = std::move(unicos);
  m->generacion = generacion_ + 1;
  if (!desdeCero && cuenta_ > 0) {
    m->viejo = fs_.abrir(bin_, Modo::LECTURA);
    if (!m->viejo) return false;
    m->viejoTotal = cuenta_;
  } else {
    m->finViejo = true;
  }
  m->nuevo = fs_.abrir(tmp_, Modo::NUEVO);
  if (!m->nuevo) return false;
  m->indiceNuevo.reserve((base + altas) / BLOQUE + 1);
  mezcla_ = std::move(m);
  return true;
}

bool ListaNegra::proximoPasoBloquea() const { return mezcla_ && mezcla_->fase != Mezcla::Fase::MEZCLANDO; }

bool ListaNegra::aplicarCambios(const Cambio *entradas, size_t n) { return iniciarMezcla(entradas, n, false); }

void ListaNegra::abortarMezcla() {
  if (!mezcla_) return;
  mezcla_->nuevo.reset();
  mezcla_->viejo.reset();
  fs_.borrar(tmp_);
  mezcla_.reset();
}

ListaNegra::Progreso ListaNegra::fallo() {
  abortarMezcla();
  if (!archivo_) abrirLista();
  return Progreso::FALLO;
}

bool ListaNegra::siguienteViejo(uint32_t &id) {
  Mezcla &m = *mezcla_;
  if (m.lecturaPos == m.lecturaLen) {
    if (m.viejoLeidos >= m.viejoTotal) return false;
    const size_t n = std::min(POR_LECTURA, m.viejoTotal - m.viejoLeidos);
    if (!m.viejo->leerEn(m.viejoLeidos * 4, m.lectura, n * 4)) {
      m.error = true;
      return false;
    }
    m.viejoLeidos += n;
    m.lecturaPos = 0;
    m.lecturaLen = n;
  }
  id = m.lectura[m.lecturaPos++];
  return true;
}

bool ListaNegra::escribirSalida() {
  Mezcla &m = *mezcla_;
  if (m.salidaLen == 0) return true;
  if (!m.nuevo->agregar(m.salida, m.salidaLen * 4)) return false;
  m.crc = crc32(m.salida, m.salidaLen * 4, m.crc);
  m.escritas += m.salidaLen;
  m.sinVaciar += m.salidaLen;
  m.salidaLen = 0;
  if (m.sinVaciar >= VACIAR_CADA) {  // así el vaciado final no se lleva toda la escritura de golpe
    if (!m.nuevo->vaciar()) return false;
    m.sinVaciar = 0;
  }
  return true;
}

ListaNegra::Progreso ListaNegra::avanzar(size_t maxEntradas) {
  if (!mezcla_) return Progreso::OCIOSA;
  Mezcla &m = *mezcla_;

  if (m.fase == Mezcla::Fase::MEZCLANDO) {
    for (size_t hechas = 0; hechas < maxEntradas; hechas++) {
      if (!m.tieneA && !m.finViejo) {
        m.tieneA = siguienteViejo(m.a);
        if (!m.tieneA) {
          if (m.error) return fallo();
          m.finViejo = true;
        }
      }
      const bool hayCambio = m.c < m.cambios.size();
      if (!m.tieneA && !hayCambio) {
        m.fase = Mezcla::Fase::PIE;
        return Progreso::EN_CURSO;
      }
      uint32_t emitir = 0;
      bool emite = false;
      if (hayCambio && (!m.tieneA || m.cambios[m.c].id < m.a)) {
        const Cambio &x = m.cambios[m.c++];  // un id que no estaba: solo el alta lo agrega
        if (x.alta) {
          emitir = x.id;
          emite = true;
        }
      } else if (hayCambio && m.cambios[m.c].id == m.a) {
        const Cambio &x = m.cambios[m.c++];  // el id ya estaba: el alta lo conserva y la baja lo quita
        m.tieneA = false;
        if (x.alta) {
          emitir = x.id;
          emite = true;
        }
      } else {
        emitir = m.a;
        m.tieneA = false;
        emite = true;
      }
      if (emite) {
        const size_t posicion = m.escritas + m.salidaLen;
        if (posicion >= MAX_IDS) return fallo();
        if (posicion % BLOQUE == 0) m.indiceNuevo.push_back(emitir);
        m.salida[m.salidaLen++] = emitir;
        if (m.salidaLen == POR_LECTURA && !escribirSalida()) return fallo();
      }
    }
    return Progreso::EN_CURSO;
  }

  if (m.fase == Mezcla::Fase::PIE) {
    if (!escribirSalida()) return fallo();
    uint8_t pie[TAM_PIE];
    pon32(pie, MAGIA);
    pon32(pie + 4, m.generacion);
    pon32(pie + 8, m.escritas);
    pon32(pie + 12, m.crc);
    if (!m.nuevo->agregar(pie, TAM_PIE) || !m.nuevo->vaciar()) return fallo();
    m.fase = Mezcla::Fase::CAMBIAR;
    return Progreso::EN_CURSO;
  }

  // CAMBIAR: el .tmp está completo y vaciado; se pone en lugar del .bin (ver el encabezado)
  archivo_.reset();
  m.nuevo.reset();
  m.viejo.reset();
  fs_.borrar(bak_);
  if (fs_.existe(bin_) && !fs_.renombrar(bin_, bak_)) return fallo();
  if (!fs_.renombrar(tmp_, bin_)) {
    fs_.renombrar(bak_, bin_);  // se deja como estaba
    return fallo();
  }
  indice_ = std::move(m.indiceNuevo);
  cuenta_ = m.escritas;
  generacion_ = m.generacion;
  bloqueLeido_ = -1;
  mezcla_.reset();  // m queda colgando: ya no se usa más
  abrirLista();
  return Progreso::TERMINADA;
}

bool ListaNegra::reiniciarListas(bool conservarUrgentes) {
  abortarMezcla();
  if (!conservarUrgentes) limpiarUrgentes();
  if (!iniciarMezcla(nullptr, 0, true)) return false;
  Progreso p;
  do {
    p = avanzar(MAX_IDS);
  } while (p == Progreso::EN_CURSO);
  return p == Progreso::TERMINADA;
}

// --- Urgentes ------------------------------------------------------------------------------

bool ListaNegra::cargarUrgentes() {
  urgentes_.clear();
  const char *ruta = fs_.existe(urg_) ? urg_ : fs_.existe(urgTmp_) ? urgTmp_ : nullptr;
  if (!ruta) return true;
  auto f = fs_.abrir(ruta, Modo::LECTURA);
  if (f) {
    const size_t n = f->tamano() / TAM_URGENTE;
    for (size_t i = 0; i < n; i++) {
      uint8_t r[TAM_URGENTE];
      if (!f->leerEn(i * TAM_URGENTE, r, TAM_URGENTE)) break;
      const uint32_t id = lee32(r);
      if (lee32(r + 4) != (id ^ MASCARA_URGENTE)) continue;  // registro roto: se ignora
      if (std::find(urgentes_.begin(), urgentes_.end(), id) == urgentes_.end()) urgentes_.push_back(id);
    }
  }
  f.reset();
  if (urgentes_.size() > MAX_URGENTES) urgentes_.erase(urgentes_.begin(), urgentes_.end() - MAX_URGENTES);
  if (ruta == urgTmp_) {
    fs_.renombrar(urgTmp_, urg_);  // una compactación que se cortó antes de terminar
  } else if (fs_.existe(urgTmp_)) {
    fs_.borrar(urgTmp_);
  }
  return true;
}

bool ListaNegra::reescribirUrgentes() {
  {
    auto f = fs_.abrir(urgTmp_, Modo::NUEVO);
    if (!f) return false;
    for (uint32_t id : urgentes_) {
      uint8_t r[TAM_URGENTE];
      pon32(r, id);
      pon32(r + 4, id ^ MASCARA_URGENTE);
      if (!f->agregar(r, TAM_URGENTE)) return false;
    }
    if (!f->vaciar()) return false;
  }
  fs_.borrar(urg_);
  return fs_.renombrar(urgTmp_, urg_);
}

bool ListaNegra::bloqueoUrgente(uint32_t id) {
  if (std::find(urgentes_.begin(), urgentes_.end(), id) != urgentes_.end()) return true;
  urgentes_.push_back(id);  // en RAM primero: la tarjeta queda bloqueada aunque falle el guardado
  if (urgentes_.size() > MAX_URGENTES) {
    urgentes_.erase(urgentes_.begin());  // el más viejo es el que antes recogerá una lista firmada
    return reescribirUrgentes();
  }
  auto f = fs_.abrir(urg_, Modo::ABRIR);
  if (!f) return false;
  uint8_t r[TAM_URGENTE];
  pon32(r, id);
  pon32(r + 4, id ^ MASCARA_URGENTE);
  return f->agregar(r, TAM_URGENTE) && f->vaciar();
}

bool ListaNegra::quitarUrgente(uint32_t id) {
  auto it = std::find(urgentes_.begin(), urgentes_.end(), id);
  if (it == urgentes_.end()) return true;
  urgentes_.erase(it);  // en RAM primero, como en bloqueoUrgente()
  if (urgentes_.empty()) return limpiarUrgentes();
  return reescribirUrgentes();
}

bool ListaNegra::limpiarUrgentes() {
  urgentes_.clear();
  if (fs_.existe(urg_)) fs_.borrar(urg_);
  if (fs_.existe(urgTmp_)) fs_.borrar(urgTmp_);
  return true;
}
