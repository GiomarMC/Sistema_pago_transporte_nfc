#include "ColaEventos.h"

#include <string.h>

namespace {
constexpr size_t TAM = sp::TAM_EVENTO;
}  // namespace

bool ColaEventos::iniciar() {
  // Resto de una compactación interrumpida
  if (fs_.existe(TEMPORAL)) {
    if (fs_.existe(ARCHIVO)) {
      fs_.borrar(TEMPORAL);
    } else if (!fs_.renombrar(TEMPORAL, ARCHIVO)) {
      return false;
    }
  }

  size_t bytes;
  {
    auto a = fs_.abrir(ARCHIVO, Modo::ABRIR);  // lo crea vacío si es la primera vez
    if (!a) return false;
    bytes = a->tamano();
  }
  cantidad_ = bytes / TAM;
  if (bytes % TAM != 0) {
    // Un corte a mitad de una escritura: se quedan solo los registros enteros
    avisar("eventos.bin: se encontro un registro incompleto y se descarta");
    if (!reescribir(0, cantidad_)) return false;
  }

  const uint32_t siguienteGuardado = estado_.estado().siguienteId;
  uint32_t ultimoEnArchivo = 0;
  if (cantidad_ > 0) {
    sp::Evento primero, ultimo;
    if (!leerRegistro(0, primero) || !leerRegistro(cantidad_ - 1, ultimo)) return false;
    primerId_ = primero.idEvento;
    ultimoEnArchivo = ultimo.idEvento;
    if (ultimoEnArchivo - primerId_ + 1 != cantidad_) avisar("eventos.bin: los ids no son consecutivos");
  }
  // Con el archivo vacío (todo compactado) los ids siguen desde lo guardado en el estado
  ultimoId_ = ultimoEnArchivo;
  if (siguienteGuardado > 0 && siguienteGuardado - 1 > ultimoId_) ultimoId_ = siguienteGuardado - 1;
  if (cantidad_ == 0) primerId_ = ultimoId_ + 1;
  // Si se cortó la luz tras guardar un acuse y antes de compactar, la compactación sigue pendiente
  compactarPendiente_ = sobraLaMitad();
  return true;
}

bool ColaEventos::alVaciar() const { return estado_.estado().hastaAcusado >= ultimoId_; }

bool ColaEventos::sobraLaMitad() const {
  const uint32_t acusado = estado_.estado().hastaAcusado;
  const uint32_t confirmados = acusado >= ultimoId_ ? cantidad_ : (acusado >= primerId_ ? acusado - primerId_ + 1 : 0);
  return cantidad_ > 0 && confirmados * 2 > cantidad_;
}

bool ColaEventos::leerRegistro(size_t indice, sp::Evento &e) {
  auto a = fs_.abrir(ARCHIVO, Modo::LECTURA);
  uint8_t r[TAM];
  return a && a->leerEn(indice * TAM, r, TAM) && sp::eventoDeBytes(r, e);
}

bool ColaEventos::agregar(sp::Evento &e) {
  e.idEvento = ultimoId_ + 1;
  uint8_t r[TAM];
  if (sp::eventoABytes(e, r) == 0) return false;

  auto a = fs_.abrir(ARCHIVO, Modo::ABRIR);
  if (!a) return false;
  if (a->tamano() != cantidad_ * TAM) {
    // Una escritura anterior falló a medias: se quita el resto antes de seguir, para que
    // los registros no queden desalineados
    a.reset();
    if (!reescribir(0, cantidad_)) return false;
    a = fs_.abrir(ARCHIVO, Modo::ABRIR);
    if (!a) return false;
  }
  if (!a->agregar(r, TAM) || !a->vaciar()) return false;  // sin esto no se da el cobro por hecho

  if (cantidad_ == 0) primerId_ = e.idEvento;
  ultimoId_ = e.idEvento;
  cantidad_++;
  return true;
}

size_t ColaEventos::leerDesde(uint32_t desdeId, sp::Evento *salida, size_t max) {
  if (cantidad_ == 0 || max == 0) return 0;
  const uint32_t primero = desdeId + 1 > primerId_ ? desdeId + 1 : primerId_;
  if (primero > ultimoId_) return 0;
  auto a = fs_.abrir(ARCHIVO, Modo::LECTURA);
  if (!a) return 0;
  size_t n = 0;
  for (uint32_t id = primero; id <= ultimoId_ && n < max; id++) {
    uint8_t r[TAM];
    if (!a->leerEn(size_t(id - primerId_) * TAM, r, TAM) || !sp::eventoDeBytes(r, salida[n])) break;
    n++;
  }
  return n;
}

bool ColaEventos::acusarHasta(uint32_t id, bool compactarAhora) {
  if (id <= estado_.estado().hastaAcusado) return true;
  if (!estado_.fijarHastaAcusado(id)) return false;
  bool hayQueCompactar = false;
  if (id > ultimoId_) {
    // El servidor confirma más de lo que hay aquí (p. ej. se perdió la tarjeta y se empezó de cero):
    // los ids nuevos siguen desde ahí, o el servidor los tomaría por repetidos y los descartaría
    ultimoId_ = id;
    hayQueCompactar = true;
  } else {
    hayQueCompactar = sobraLaMitad();  // registros ya confirmados que siguen ocupando sitio en el archivo
  }
  if (!hayQueCompactar) return true;
  if (!compactarAhora) {
    compactarPendiente_ = true;
    return true;
  }
  return compactar();
}

bool ColaEventos::compactar() {
  if (!compactarAhora()) return false;
  compactarPendiente_ = false;
  return true;
}

bool ColaEventos::compactarPaso() {
  if (!compactarPendiente_) return true;
  // Si la compactación va a dejar el archivo vacío, el siguiente id se guarda ANTES (paso 1) y luego se reescribe
  // (paso 2; guardar el id otra vez no escribe nada porque no cambió). Si quedan registros, un solo paso.
  if (alVaciar() && estado_.estado().siguienteId != ultimoId_ + 1) return estado_.fijarSiguienteId(ultimoId_ + 1);
  return compactar();
}

bool ColaEventos::compactarAhora() {
  const uint32_t acusado = estado_.estado().hastaAcusado;
  // Si se vacía el archivo, los ids siguen desde lo guardado: se guarda ANTES de quitar los registros. Si quedan
  // registros, el último dice por dónde van los ids y no hace falta escribir el estado (~0,7 s en la microSD real)
  if (alVaciar() && !estado_.fijarSiguienteId(ultimoId_ + 1)) return false;
  size_t desde = 0;
  if (acusado >= ultimoId_) desde = cantidad_;
  else if (acusado >= primerId_) desde = acusado - primerId_ + 1;
  if (desde == 0) return true;
  if (!reescribir(desde, cantidad_)) return false;
  compactaciones_++;
  return true;
}

// Escribe en /eventos.tmp los registros [desde, hasta) y lo deja como /eventos.bin. Un corte en
// cualquier punto se recupera en iniciar(): mientras /eventos.bin exista el original está entero,
// y si ya se borró, el temporal está completo porque se vació antes de borrar nada.
bool ColaEventos::reescribir(size_t desde, size_t hasta) {
  {
    auto origen = fs_.abrir(ARCHIVO, Modo::LECTURA);
    auto destino = fs_.abrir(TEMPORAL, Modo::NUEVO);
    if (!origen || !destino) return false;
    uint8_t trozo[16 * TAM];
    for (size_t i = desde; i < hasta;) {
      const size_t n = hasta - i < 16 ? hasta - i : 16;
      if (!origen->leerEn(i * TAM, trozo, n * TAM) || !destino->agregar(trozo, n * TAM)) return false;
      i += n;
    }
    if (!destino->vaciar()) return false;
  }
  if (!fs_.borrar(ARCHIVO) || !fs_.renombrar(TEMPORAL, ARCHIVO)) return false;

  cantidad_ = hasta - desde;
  if (cantidad_ > 0) {
    sp::Evento primero;
    if (!leerRegistro(0, primero)) return false;
    primerId_ = primero.idEvento;
  } else {
    primerId_ = ultimoId_ + 1;
  }
  return true;
}
