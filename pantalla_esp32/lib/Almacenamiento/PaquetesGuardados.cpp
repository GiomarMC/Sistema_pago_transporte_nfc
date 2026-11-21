#include "PaquetesGuardados.h"

#include <ProtocoloSP.h>
#include <stdio.h>
#include <string.h>

#include <algorithm>

#include "Crc32.h"

namespace {

uint16_t lee16(const uint8_t *p) { return p[0] | (uint16_t(p[1]) << 8); }

uint32_t lee32(const uint8_t *p) {
  return p[0] | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

void pon32(uint8_t *p, uint32_t v) {
  for (int i = 0; i < 4; i++) p[i] = v >> (8 * i);
}

constexpr size_t POS_ORIGEN = 4, POS_SECUENCIA = 8, POS_CREADO = 12;
constexpr size_t MAX_DIARIO = 64 * 1024;  // más que esto no es un diario nuestro

}  // namespace

CarpetaPaquetes::CarpetaPaquetes(SistemaArchivos &fs, const char *carpeta, const char *diario, size_t maximo, bool ajenos)
    : fs_(fs), carpeta_(carpeta), maximo_(maximo), ajenos_(ajenos) {
  snprintf(log_, sizeof(log_), "%s.log", diario);
  snprintf(tmp_, sizeof(tmp_), "%s.tmp", diario);
}

void CarpetaPaquetes::ruta(uint32_t a, uint32_t b, char *salida, size_t n) const {
  snprintf(salida, n, "%s/%lu-%lu.bin", carpeta_, (unsigned long)a, (unsigned long)b);
}

long CarpetaPaquetes::buscar(uint32_t a, uint32_t b) const {
  for (size_t i = 0; i < indice_.size(); i++)
    if (indice_[i].a == a && indice_[i].b == b) return static_cast<long>(i);
  return -1;
}

// --- Diario -----------------------------------------------------------------------------------

// tipo(1) 0(1) largo(2) | a | b | secuencia | creado | ultimoEvento | crc32 de lo anterior
void CarpetaPaquetes::registro(Tipo t, const Entrada &e, uint8_t r[TAM_REGISTRO]) {
  r[0] = t;
  r[1] = 0;
  r[2] = e.largo;
  r[3] = e.largo >> 8;
  pon32(r + 4, e.a);
  pon32(r + 8, e.b);
  pon32(r + 12, e.secuencia);
  pon32(r + 16, e.creado);
  pon32(r + 20, e.ultimoEvento);
  pon32(r + 24, crc32(r, 24));
}

// Rehace el índice con los registros válidos. hayQueReescribir: el diario tenía un final cortado o sobran
// registros.
bool CarpetaPaquetes::cargarDiario(bool &hayQueReescribir) {
  hayQueReescribir = false;
  indice_.clear();
  registros_ = 0;
  const char *rutas[] = {log_, tmp_};
  for (const char *ruta : rutas) {
    if (!fs_.existe(ruta)) continue;
    auto f = fs_.abrir(ruta, Modo::LECTURA);
    if (!f) continue;
    const size_t tam = f->tamano();
    if (tam > MAX_DIARIO) continue;
    std::vector<uint8_t> datos(tam);
    if (tam > 0 && !f->leerEn(0, datos.data(), tam)) continue;
    std::vector<Entrada> indice;
    size_t validos = 0;
    bool fin = false, cortado = tam % TAM_REGISTRO != 0;
    for (size_t i = 0; i + TAM_REGISTRO <= tam; i += TAM_REGISTRO) {
      const uint8_t *r = datos.data() + i;
      if (lee32(r + 24) != crc32(r, 24)) {
        cortado = true;
        break;
      }
      validos++;
      fin = r[0] == FIN;
      Entrada e;
      e.largo = lee16(r + 2);
      e.a = lee32(r + 4);
      e.b = lee32(r + 8);
      e.secuencia = lee32(r + 12);
      e.creado = lee32(r + 16);
      e.ultimoEvento = lee32(r + 20);
      auto it = std::find_if(indice.begin(), indice.end(), [&](const Entrada &x) { return x.a == e.a && x.b == e.b; });
      if (r[0] == ALTA) {
        if (it != indice.end()) *it = e;
        else indice.push_back(e);
      } else if (r[0] == BAJA && it != indice.end()) {
        indice.erase(it);
      }
    }
    if (ruta == tmp_) {
      // sin diario: el .tmp de una reescritura vale solo si quedó entero (termina en FIN)
      if (!fin || cortado) continue;
      indice_ = std::move(indice);
      registros_ = validos;
      fs_.renombrar(tmp_, log_);  // pasa a ser el diario (no se reescribe: es la única copia entera)
      return true;
    }
    indice_ = std::move(indice);
    registros_ = validos;
    hayQueReescribir = cortado || registros_ > 2 * indice_.size() + REGISTROS_DE_MAS;
    return true;
  }
  return false;  // no hay diario: empieza vacío
}

bool CarpetaPaquetes::reescribirDiario() {
  diario_.reset();
  {
    auto f = fs_.abrir(tmp_, Modo::NUEVO);
    if (!f) return false;
    uint8_t buf[TAM_REGISTRO * 16];
    size_t en = 0;
    for (const Entrada &e : indice_) {
      registro(ALTA, e, buf + en);
      en += TAM_REGISTRO;
      if (en == sizeof(buf)) {
        if (!f->agregar(buf, en)) return false;
        en = 0;
      }
    }
    Entrada fin;
    fin.a = static_cast<uint32_t>(indice_.size());
    registro(FIN, fin, buf + en);
    en += TAM_REGISTRO;
    if (!f->agregar(buf, en) || !f->vaciar()) return false;
  }
  if (fs_.existe(log_) && !fs_.borrar(log_)) return false;
  if (!fs_.renombrar(tmp_, log_)) return false;
  registros_ = indice_.size() + 1;
  diario_ = fs_.abrir(log_, Modo::ABRIR);
  return diario_ != nullptr;
}

bool CarpetaPaquetes::anotar(Tipo t, const Entrada &e, bool vaciar) {
  if (!diario_ && !reescribirDiario()) return false;
  if (registros_ > 2 * indice_.size() + REGISTROS_DE_MAS) {
    if (!reescribirDiario()) return false;  // el índice en RAM ya es el de ahora
  }
  uint8_t r[TAM_REGISTRO];
  registro(t, e, r);
  if (!diario_->agregar(r, sizeof(r)) || (vaciar && !diario_->vaciar())) {
    diario_.reset();  // pudo quedar a medias: se reescribirá antes de volver a anotar
    return false;
  }
  registros_++;
  return true;
}

bool CarpetaPaquetes::iniciarCarpeta() {
  diario_.reset();
  if (!fs_.crearCarpeta(carpeta_)) return false;
  bool reescribir = false;
  const bool habia = cargarDiario(reescribir);
  if (!habia) {
    indice_.clear();
    registros_ = 0;
  }
  if (reescribir && !reescribirDiario()) return false;
  if (!diario_) {
    if (fs_.existe(tmp_)) fs_.borrar(tmp_);  // resto de una reescritura que no llegó a terminar
    diario_ = fs_.abrir(log_, Modo::ABRIR);
    if (!diario_) return false;
  }
  recortar(false, 0, 0);  // tras un corte pudo quedar alguno de más
  return true;
}

// --- Paquetes ---------------------------------------------------------------------------------

bool CarpetaPaquetes::examinar(const uint8_t *datos, size_t largo, Entrada &e) const {
  if (datos == nullptr || largo < sp::TAM_CABECERA || largo > sp::TAM_MAXIMO || sp::medirPaquete(datos, largo) != largo)
    return false;
  const uint8_t tipo = datos[3];
  const uint16_t origen = lee16(datos + POS_ORIGEN);
  const size_t cuerpo = lee16(datos + 16);
  e.secuencia = lee32(datos + POS_SECUENCIA);
  e.creado = lee32(datos + POS_CREADO);
  e.largo = static_cast<uint16_t>(largo);
  const uint8_t *c = datos + sp::TAM_CABECERA;
  if (ajenos_) {
    if (tipo != sp::EVENTOS || origen == sp::SERVIDOR || origen == sp::TODOS) return false;
    const unsigned n = c[0];
    if (n < 1 || n > sp::MAX_EVENTOS || cuerpo != 4 + n * sp::TAM_EVENTO) return false;
    e.a = origen;
    e.b = e.secuencia;
    // los ids van en orden; por si no, vale el mayor
    e.ultimoEvento = 0;
    for (unsigned i = 0; i < n; i++) e.ultimoEvento = std::max(e.ultimoEvento, lee32(c + 4 + i * sp::TAM_EVENTO));
  } else {
    if (tipo != sp::LISTAS || origen != sp::SERVIDOR || cuerpo < 12) return false;
    e.a = lee32(c);
    e.b = lee32(c + 4);
    e.ultimoEvento = 0;
  }
  return true;
}

bool CarpetaPaquetes::masAntigua(const Entrada &x, const Entrada &y) const {
  if (ajenos_) {
    if (x.creado != y.creado) return x.creado < y.creado;
    return x.a != y.a ? x.a < y.a : x.b < y.b;
  }
  return x.secuencia < y.secuencia;
}

bool CarpetaPaquetes::yaConfirmado(const Entrada &e) const {
  if (!ajenos_) return false;
  auto it = acusado_.find(static_cast<uint16_t>(e.a));
  return it != acusado_.end() && e.ultimoEvento <= it->second;
}

void CarpetaPaquetes::vaciarDiario() {
  if (diario_ && !diario_->vaciar()) diario_.reset();  // se reescribirá antes de volver a anotar
}

void CarpetaPaquetes::borrarEntrada(size_t i, bool vaciar) {
  const Entrada e = indice_[i];
  char r[64];
  ruta(e.a, e.b, r, sizeof(r));
  fs_.borrar(r);  // primero el archivo; si se corta antes de la BAJA, la entrada se descubre sin archivo
  indice_.erase(indice_.begin() + i);
  anotar(BAJA, e, vaciar);
}

void CarpetaPaquetes::ordenadas(std::vector<const Entrada *> &salida) const {
  salida.clear();
  for (const Entrada &e : indice_) salida.push_back(&e);
  std::sort(salida.begin(), salida.end(), [&](const Entrada *x, const Entrada *y) { return masAntigua(*x, *y); });
}

// Borra los más antiguos mientras haya más del máximo (nunca el recién guardado)
void CarpetaPaquetes::recortar(bool protegerUno, uint32_t pa, uint32_t pb) {
  size_t borrados = 0;
  while (indice_.size() > maximo_) {
    long viejo = -1;
    for (size_t i = 0; i < indice_.size(); i++) {
      if (protegerUno && indice_[i].a == pa && indice_[i].b == pb) continue;
      if (viejo < 0 || masAntigua(indice_[i], indice_[viejo])) viejo = static_cast<long>(i);
    }
    if (viejo < 0) break;
    borrarEntrada(viejo, false);
    borrados++;
  }
  if (borrados > 0) vaciarDiario();
}

bool CarpetaPaquetes::guardarPaquete(const uint8_t *datos, size_t largo, const Entrada &e) {
  char r[64];
  ruta(e.a, e.b, r, sizeof(r));
  // ya está en el índice con el archivo entero: nada que hacer (los firmados sí se sustituyen)
  const long k = buscar(e.a, e.b);
  if (k >= 0 && ajenos_) {
    auto f = fs_.abrir(r, Modo::LECTURA);
    if (f && f->tamano() == indice_[k].largo) return true;
  }
  // 1. la ALTA en el diario, 2. el archivo
  const Entrada anterior = k >= 0 ? indice_[k] : Entrada();
  if (k >= 0) indice_[k] = e;
  else indice_.push_back(e);
  if (!anotar(ALTA, e)) {
    if (k >= 0) indice_[k] = anterior;
    else indice_.pop_back();
    return false;
  }
  {
    auto f = fs_.abrir(r, Modo::NUEVO);
    if (!f || !f->agregar(datos, largo) || !f->vaciar()) {
      f.reset();
      fs_.borrar(r);
      const long j = buscar(e.a, e.b);
      if (j >= 0) borrarEntrada(j);
      return false;
    }
  }
  recortar(true, e.a, e.b);
  return true;
}

size_t CarpetaPaquetes::leerPaquete(uint32_t a, uint32_t b, uint8_t *destino, size_t capacidad) {
  const long k = buscar(a, b);
  if (k < 0) return 0;
  char r[64];
  ruta(a, b, r, sizeof(r));
  auto f = fs_.abrir(r, Modo::LECTURA);
  const size_t tam = f ? f->tamano() : 0;
  if (tam != indice_[k].largo) {  // falta o está cortado (un corte a mitad de guardarlo)
    f.reset();
    borrarEntrada(k);
    return 0;
  }
  if (tam > capacidad || !f->leerEn(0, destino, tam)) return 0;
  return tam;
}

// --- Ajenos -----------------------------------------------------------------------------------

bool PaquetesAjenos::guardar(const uint8_t *paquete, size_t largo) {
  Entrada e;
  if (!examinar(paquete, largo, e)) return false;
  if (yaConfirmado(e)) return true;  // el servidor ya lo tiene: no hace falta guardarlo
  return guardarPaquete(paquete, largo, e);
}

size_t PaquetesAjenos::acusar(uint16_t origen, uint32_t hastaEvento, size_t maxBorrar) {
  uint32_t &hasta = acusado_[origen];
  hasta = std::max(hasta, hastaEvento);
  return borrarConfirmados(maxBorrar);
}

size_t PaquetesAjenos::borrarConfirmados(size_t max) {
  size_t borrados = 0;
  for (size_t i = 0; i < indice_.size() && borrados < max;) {
    if (yaConfirmado(indice_[i])) {
      borrarEntrada(i, false);
      borrados++;
    } else {
      i++;
    }
  }
  if (borrados > 0) vaciarDiario();
  return borrados;
}

size_t PaquetesAjenos::confirmadosPorBorrar() const {
  size_t n = 0;
  for (const Entrada &e : indice_) n += yaConfirmado(e) ? 1 : 0;
  return n;
}

size_t PaquetesAjenos::listar(InfoPaquete *salida, size_t max) {
  std::vector<const Entrada *> orden;
  ordenadas(orden);
  size_t n = 0;
  for (const Entrada *e : orden) {
    if (n >= max) break;
    salida[n++] = {e->a, e->b, e->secuencia, e->creado, e->ultimoEvento, e->largo};
  }
  return n;
}

size_t PaquetesAjenos::leer(uint16_t origen, uint32_t secuencia, uint8_t *destino, size_t capacidad) {
  return leerPaquete(origen, secuencia, destino, capacidad);
}

size_t PaquetesAjenos::leerPosicion(size_t indice, uint8_t *destino, size_t capacidad) {
  if (indice >= indice_.size() || yaConfirmado(indice_[indice])) return 0;
  const Entrada e = indice_[indice];  // copia: leerPaquete() puede quitar la entrada
  return leerPaquete(e.a, e.b, destino, capacidad);
}

// --- Firmados ---------------------------------------------------------------------------------

bool PaquetesFirmados::guardar(const uint8_t *paquete, size_t largo) {
  Entrada e;
  if (!examinar(paquete, largo, e)) return false;
  return guardarPaquete(paquete, largo, e);
}

size_t PaquetesFirmados::listar(InfoPaquete *salida, size_t max) {
  std::vector<const Entrada *> orden;
  ordenadas(orden);
  size_t n = 0;
  for (const Entrada *e : orden) {
    if (n >= max) break;
    salida[n++] = {e->a, e->b, e->secuencia, e->creado, 0, e->largo};
  }
  return n;
}

size_t PaquetesFirmados::leer(uint32_t desde, uint32_t hasta, uint8_t *destino, size_t capacidad) {
  return leerPaquete(desde, hasta, destino, capacidad);
}
