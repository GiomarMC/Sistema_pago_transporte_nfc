#include "sincronizador_v2.h"

#include <string.h>

namespace {
// Solo se esperan paquetes del servidor (firma ECDSA); ninguno de otro validador por este canal
const uint8_t *sinClavesDeValidadores(uint16_t) { return nullptr; }
}  // namespace

void SincronizadorV2::atender(CanalPaquetes *canal, uint32_t ahoraMs) {
  if (canal == nullptr || !canal->listo()) {
    // Sin canal no se pierde nada: los eventos siguen en la cola local hasta que haya uno.
    // Lo que iba en vuelo se da por perdido y se reenvía al volver.
    canalListo_ = false;
    canalActual_ = nullptr;
    nVuelo_ = 0;
    return;
  }
  if (!canalListo_ || canal != canalActual_) alPasarAListo(canal, ahoraMs);

  recibir(*canal, ahoraMs);

  // Sin acuse a tiempo: se vuelve a mandar desde el primer evento sin confirmar
  if (nVuelo_ > 0 && (uint32_t)(ahoraMs - vuelo_[0].enviadoMs) >= REENVIO_MS) {
    reenvios_++;
    nVuelo_ = 0;
    siguienteId_ = almacen_.ultimoAcusado() + 1;
  }

  if (holaPendiente_ || (uint32_t)(ahoraMs - ultimoHolaMs_) >= HOLA_MS) enviarHola(*canal, ahoraMs);
  while (nVuelo_ < PAQUETES_SIN_ACUSE && enviarEventos(*canal, ahoraMs)) {
  }
  enviarAjenos(*canal, ahoraMs);
}

void SincronizadorV2::alPasarAListo(CanalPaquetes *canal, uint32_t ahoraMs) {
  (void)ahoraMs;
  canalActual_ = canal;
  canalListo_ = true;
  holaPendiente_ = true;  // lo primero que se dice al conectar
  nVuelo_ = 0;
  siguienteId_ = almacen_.ultimoAcusado() + 1;
  rondaAjenosActiva_ = false;
  hayRondaPrevia_ = false;  // los ajenos se mandan enseguida al conectar
}

void SincronizadorV2::enviarHola(CanalPaquetes &canal, uint32_t ahoraMs) {
  tx_.cabecera = {sp::HOLA, cfg_.validador, sp::SERVIDOR, almacen_.siguienteSecuencia(), creado()};
  const uint32_t pend = pendientes();
  sp::Hola &h = tx_.hola;
  h.versionListas = almacen_.versionListas();
  h.versionTarifas = almacen_.versionTarifas();
  h.ultimoEvento = almacen_.ultimoEvento();
  h.pendientes = pend > 0xFFFF ? 0xFFFF : pend;
  h.ajenos = almacen_.ajenos() > 0xFFFF ? 0xFFFF : almacen_.ajenos();
  h.enlace = canal.codigoEnlace();
  h.senal = senal_;
  h.bytesTxHoy = bytesTx_;
  h.bytesRxHoy = bytesRx_;
  h.versionFirmware = cfg_.versionFirmware;
  const size_t largo = sp::empaquetar(tx_, cfg_.claveHmac, bufTx_, sizeof(bufTx_));
  if (largo == 0 || !canal.enviar(bufTx_, largo)) return;  // se reintenta en la próxima vuelta
  holaPendiente_ = false;
  ultimoHolaMs_ = ahoraMs;
  holas_++;
}

// Hasta EVENTOS_POR_PAQUETE eventos con id mayor que `desdeId` en tx_.eventos, cortando en el primero que aún lleva
// la hora estimada sin corregir (el orden importa: el servidor los quiere contiguos) y con las fechas corregidas
size_t SincronizadorV2::prepararEventos(uint32_t desdeId) {
  if (retenido(desdeId + 1)) return 0;
  sp::Eventos &e = tx_.eventos;
  size_t n = almacen_.eventosDesde(desdeId, e.eventos, EVENTOS_POR_PAQUETE);
  for (size_t i = 0; i < n; i++) {
    if (retenido(e.eventos[i].idEvento)) {
      n = i;
      break;
    }
    if (cfg_.correccion) cfg_.correccion->corregir(e.eventos[i]);
  }
  e.n = n;
  return n;
}

size_t SincronizadorV2::ultimoAcuse(uint8_t *buf, size_t max, uint32_t &recibidoMs) const {
  if (largoUltimoAcuse_ == 0 || largoUltimoAcuse_ > max) return 0;
  memcpy(buf, ultimoAcuse_, largoUltimoAcuse_);
  recibidoMs = ultimoAcuseMs_;
  return largoUltimoAcuse_;
}

bool SincronizadorV2::enviarEventos(CanalPaquetes &canal, uint32_t ahoraMs) {
  if (siguienteId_ > almacen_.ultimoEvento()) return false;  // no hay nada nuevo
  sp::Eventos &e = tx_.eventos;
  const size_t n = prepararEventos(siguienteId_ - 1);
  if (n == 0) return false;
  tx_.cabecera = {sp::EVENTOS, cfg_.validador, sp::SERVIDOR, almacen_.siguienteSecuencia(), creado()};
  const size_t largo = sp::empaquetar(tx_, cfg_.claveHmac, bufTx_, sizeof(bufTx_));
  if (largo == 0 || !canal.enviar(bufTx_, largo)) return false;
  vuelo_[nVuelo_++] = {e.eventos[0].idEvento, e.eventos[n - 1].idEvento, ahoraMs};
  siguienteId_ = e.eventos[n - 1].idEvento + 1;
  paqueteEventos_++;
  return true;
}

void SincronizadorV2::enviarAjenos(CanalPaquetes &canal, uint32_t ahoraMs) {
  const size_t total = almacen_.ajenos();
  // Una ronda recorre los paquetes ajenos de uno en uno; vuelve a empezar a los 30 s
  // mientras sigan guardados (se borran al llegar el acuse de su dueño)
  if (!rondaAjenosActiva_) {
    if (total == 0 || (hayRondaPrevia_ && (uint32_t)(ahoraMs - rondaAjenosMs_) < REENVIO_MS)) return;
    rondaAjenosActiva_ = true;
    hayRondaPrevia_ = true;
    rondaAjenosMs_ = ahoraMs;
    ajenoSiguiente_ = 0;
  }
  if (ajenoSiguiente_ >= total) {
    rondaAjenosActiva_ = false;
    return;
  }
  const size_t largo = almacen_.leerAjeno(ajenoSiguiente_, bufTx_, sizeof(bufTx_));
  if (largo > 0) {
    if (!canal.enviar(bufTx_, largo)) return;  // se intenta otra vez en la próxima vuelta
    paquetesAjenos_++;
  }
  ajenoSiguiente_++;
}

void SincronizadorV2::recibir(CanalPaquetes &canal, uint32_t ahoraMs) {
  size_t n;
  while ((n = canal.recibir(bufRx_, sizeof(bufRx_))) > 0) procesar(bufRx_, n, ahoraMs);
}

void SincronizadorV2::entregar(const uint8_t *datos, size_t largo, uint32_t ahoraMs) {
  procesar(datos, largo, ahoraMs, true);
}

size_t SincronizadorV2::empaquetarEventos(uint32_t desdeId, uint8_t *buf, size_t capacidad, uint32_t &ultimoId,
                                          uint32_t ahoraMs) {
  (void)ahoraMs;
  sp::Eventos &e = tx_.eventos;
  const size_t n = prepararEventos(desdeId);
  if (n == 0) return 0;
  tx_.cabecera = {sp::EVENTOS, cfg_.validador, sp::SERVIDOR, almacen_.siguienteSecuencia(), creado()};
  const size_t largo = sp::empaquetar(tx_, cfg_.claveHmac, buf, capacidad);
  if (largo == 0) return 0;
  ultimoId = e.eventos[n - 1].idEvento;
  return largo;
}

void SincronizadorV2::procesar(const uint8_t *datos, size_t largo, uint32_t ahoraMs, bool relevado) {
  recibidos_++;
  // Un EVENTOS de otro bus (solo por un vecino): su firma HMAC es de ese bus y no se puede comprobar aquí; se guarda
  // para llevarlo al servidor, que sí la comprueba. Solo se mira que tenga la forma de un EVENTOS.
  if (relevado && largo >= sp::TAM_CABECERA && datos[0] == 'S' && datos[1] == 'P' && datos[3] == sp::EVENTOS) {
    const uint16_t origen = datos[4] | (datos[5] << 8);
    if (origen != sp::SERVIDOR && origen != cfg_.validador) {
      if (almacen_.guardarAjeno(datos, largo)) ajenosRecibidos_++;
      else rechazados_++;
      return;
    }
  }
  sp::Motivo motivo = sp::Motivo::OK;
  if (!sp::desempaquetar(datos, largo, sinClavesDeValidadores, cfg_.clavePublicaServidor, rx_, &motivo)) {
    rechazados_++;
    ultimoRechazo_ = motivo;
    // Regla 5 de C1 5.5: un LISTAS del servidor con la firma mala se descarta y queda como incidencia
    if (motivo == sp::Motivo::FIRMA && largo > 5 && datos[3] == sp::LISTAS && datos[4] == 0 && datos[5] == 0) {
      listasFirmaMala_++;
      if (!hayIncidencia_ || (uint32_t)(ahoraMs - ultimaIncidenciaMs_) >= INCIDENCIA_MS) {
        hayIncidencia_ = true;
        ultimaIncidenciaMs_ = ahoraMs;
        almacen_.incidencia(DETALLE_LISTA_FALSA);
      }
    }
    return;
  }
  const sp::Cabecera &cab = rx_.cabecera;
  if (cab.origen != sp::SERVIDOR) return;  // por aquí solo habla el servidor

  switch (cab.tipo) {
    case sp::ACUSE:
      alAcuse(cab, rx_.acuse, datos, largo, ahoraMs, relevado);
      break;
    case sp::LISTAS:
    case sp::TARIFAS:
      // Solo si es para este validador o para todos (o, por un vecino, para cualquiera: ver entregar())
      if (!relevado && cab.destino != cfg_.validador && cab.destino != sp::TODOS) break;
      if (cab.tipo == sp::LISTAS) {
        almacen_.aplicarListas(rx_.listas, datos, largo);
      } else {
        almacen_.aplicarTarifas(rx_.tarifas);
      }
      break;
    default:
      break;
  }
}

void SincronizadorV2::alAcuse(const sp::Cabecera &cab, const sp::Acuse &acuse, const uint8_t *datos, size_t largo,
                              uint32_t ahoraMs, bool relevado) {
  if (cab.destino != cfg_.validador) {
    // Si se le pidió la hora a un vecino, vale la de este ACUSE aunque sea de otro bus: la firma es del servidor y el
    // vecino lo pidió fresco (mandó un HOLA al recibir el PEDIDO). Fuera de la ventana no se toma: podría ser viejo
    if (relevado && esperandoHora_ && (int32_t)(ahoraMs - horaHastaMs_) <= 0 && cab.destino != sp::TODOS) {
      esperandoHora_ = false;
      tomarHora(acuse.horaServidor, ahoraMs, true);
      horasDeVecino_++;
    }
    // Acuse de otro bus cuyos paquetes se llevaban: ya no hace falta guardarlos, y se guarda el acuse para
    // entregárselo cuando lo pida (si no vino por un vecino: el servidor nos lo manda por haber subido sus eventos)
    almacen_.ajenosAcusados(cab.destino, acuse.hastaEvento);
    if (cab.destino != sp::TODOS) almacen_.guardarAcuseAjeno(datos, largo);
    return;
  }
  tomarHora(acuse.horaServidor, ahoraMs, relevado);
  if (!relevado && largo <= TAM_ACUSE) {  // para dárselo a un vecino que pida la hora
    memcpy(ultimoAcuse_, datos, largo);
    largoUltimoAcuse_ = largo;
    ultimoAcuseMs_ = ahoraMs;
  }
  const uint32_t hasta = acuse.hastaEvento;
  if (hasta <= almacen_.ultimoAcusado()) return;  // repetido o más viejo que otro ya recibido

  almacen_.acusarHasta(hasta);
  // Los paquetes en vuelo que el acuse cubre por completo dejan de esperar; los demás siguen
  uint8_t quedan = 0;
  for (uint8_t i = 0; i < nVuelo_; i++)
    if (vuelo_[i].ultimo > hasta) vuelo_[quedan++] = vuelo_[i];
  nVuelo_ = quedan;
  if (siguienteId_ <= hasta) siguienteId_ = hasta + 1;  // el servidor sabe más de lo que se creía
}

void SincronizadorV2::tomarHora(uint32_t horaServidor, uint32_t ahoraMs, bool relevadoPorVecino) {
  if (horaServidor == 0) return;
  // Un ACUSE más viejo que otro ya aceptado no hace retroceder el reloj
  if (horaCrudaMax_ != 0 && horaServidor + TOLERANCIA_HORA_S < horaCrudaMax_) return;
  if (horaServidor > horaCrudaMax_) horaCrudaMax_ = horaServidor;
  // Por BLE o reenviado por otro bus llegó con retraso: se compensa
  const uint8_t enlace = canalActual_ ? canalActual_->codigoEnlace() : sp::WIFI;
  const bool relevado = relevadoPorVecino || enlace == sp::BLE || enlace == sp::REENVIADO;
  horaServidor_ = horaServidor + (relevado ? RETRASO_RELE_S : 0);
  millisHora_ = ahoraMs;
}
