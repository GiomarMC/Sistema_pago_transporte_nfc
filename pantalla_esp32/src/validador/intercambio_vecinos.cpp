#include "intercambio_vecinos.h"

#include <string.h>

namespace {
bool mismaMac(const uint8_t *a, const uint8_t *b) { return memcmp(a, b, 6) == 0; }
// Una versión intermedia de una foto completa (>= 2^31) no es una versión real: no se pide ni se compara
constexpr uint32_t VERSION_INTERMEDIA = 0x80000000u;
}  // namespace

IntercambioVecinos::Recuerdo &IntercambioVecinos::recuerdo(const uint8_t mac[6], uint32_t ahoraMs) {
  (void)ahoraMs;
  for (Recuerdo &r : recuerdos_)
    if (r.usado && mismaMac(r.mac, mac)) return r;
  Recuerdo &r = recuerdos_[siguienteRecuerdo_];
  siguienteRecuerdo_ = (siguienteRecuerdo_ + 1) % VECINOS_RECORDADOS;
  r = Recuerdo();
  r.usado = true;
  memcpy(r.mac, mac, 6);
  return r;
}

bool IntercambioVecinos::encolar(const Trabajo &t) {
  if (nTrabajos_ >= TRABAJOS) return false;
  trabajos_[nTrabajos_++] = t;
  return true;
}

bool IntercambioVecinos::hayTrabajo(Tipo tipo, const uint8_t mac[6]) const {
  for (size_t i = 0; i < nTrabajos_; i++)
    if (trabajos_[i].tipo == tipo && mismaMac(trabajos_[i].mac, mac)) return true;
  return false;
}

void IntercambioVecinos::quitarTrabajo(size_t i) {
  for (size_t j = i + 1; j < nTrabajos_; j++) trabajos_[j - 1] = trabajos_[j];
  nTrabajos_--;
}

void IntercambioVecinos::atender(uint32_t ahoraMs, bool tengoCanal, bool necesitoHora) {
  tengoCanal_ = tengoCanal;
  // Cuánto lleva este bus sin canal de subida (para el criterio de los 10 minutos)
  if (tengoCanal) {
    habiaCanal_ = true;
  } else if (habiaCanal_) {
    habiaCanal_ = false;
    sinCanalDesdeMs_ = ahoraMs;
  }
  publicarBaliza(ahoraMs, tengoCanal);
  recibir(ahoraMs);
  revisarEntrega(ahoraMs);
  decidir(ahoraMs, tengoCanal, necesitoHora);
  ejecutarTrabajo(ahoraMs);
}

void IntercambioVecinos::publicarBaliza(uint32_t ahoraMs, bool tengoCanal) {
  sp::Baliza b;
  b.validador = validador_;
  b.versionListas = almacen_.versionListas();
  b.versionTarifas = almacen_.versionTarifas();
  const uint32_t pend = sinc_.pendientes();
  b.pendientes = pend > 0xFFFF ? 0xFFFF : static_cast<uint16_t>(pend);
  const size_t ajenos = almacen_.ajenos();
  b.ajenos = ajenos > 0xFFFF ? 0xFFFF : static_cast<uint16_t>(ajenos);
  b.flags = (tengoCanal ? sp::BALIZA_CON_SERVIDOR : 0) | (ajenos < MAX_AJENOS_ACEPTADOS ? sp::BALIZA_ACEPTA_AJENOS : 0);
  // Solo se vuelve a publicar si cambió algo (la radio repite la baliza cada 2 s sola)
  if (hayBaliza_ && memcmp(&b, &ultimaBaliza_, sizeof(b)) == 0) return;
  hayBaliza_ = true;
  ultimaBaliza_ = b;
  ultimaBalizaMs_ = ahoraMs;
  radio_.publicar(b);
}

void IntercambioVecinos::recibir(uint32_t ahoraMs) {
  uint8_t mac[6];
  size_t n;
  // Paquetes C1 de un vecino: el sincronizador decide qué hace con cada uno (y verifica las firmas)
  while ((n = radio_.recibirMensaje(rx_, sizeof(rx_), mac)) > 0) {
    cuenta_.recibidos++;
    sinc_.entregar(rx_, n, ahoraMs);
  }
  // Pedidos: se atienden de uno en uno desde ejecutarTrabajo()
  sp::Pedido p;
  while (radio_.recibirPedido(mac, p)) {
    Trabajo t = {};
    memcpy(t.mac, mac, 6);
    t.validador = p.validador;
    if (p.que == sp::PEDIDO_LISTAS) {
      if (hayTrabajo(Tipo::LISTAS, mac)) continue;
      t.tipo = Tipo::LISTAS;
      t.cursor = p.x;
      t.restantes = CADENA_LISTAS;
    } else if (p.que == sp::PEDIDO_ACUSES) {
      if (hayTrabajo(Tipo::ACUSE, mac)) continue;
      t.tipo = Tipo::ACUSE;
      t.restantes = 1;
    } else if (p.que == sp::PEDIDO_HORA) {
      // Solo con canal: sin servidor no hay hora fresca que dar (una vieja haría más daño que la estimada)
      if (!tengoCanal_ || hayTrabajo(Tipo::HORA, mac)) continue;
      t.tipo = Tipo::HORA;
      t.cursor = ahoraMs;
      t.restantes = 1;
      sinc_.pedirHola();  // el servidor contesta con un ACUSE con su hora
    } else {
      continue;  // TARIFAS: no se guardan paquetes TARIFAS para entregar
    }
    if (encolar(t)) cuenta_.pedidosAtendidos++;
  }
}

// Un EVENTOS que la radio abortó no llegó: se deshace lo que se anotó como entregado y se vuelve a ofrecer en
// REINTENTO_EVENTOS_MS (no a los 10 min de ENTREGA_VIGENTE_MS). Sigue contando para el máximo por encuentro, así
// que una radio que falla siempre no se reintenta sin fin. Solo hay un mensaje en vuelo a la vez y no sale otro hasta
// que enviando() es false, así que el aumento de abortados() es de este.
void IntercambioVecinos::revisarEntrega(uint32_t ahoraMs) {
  if (!enVuelo_.activo || radio_.enviando()) return;
  enVuelo_.activo = false;
  if (radio_.abortados() == enVuelo_.abortadosAntes) return;  // llegó entero
  cuenta_.eventosAbortados++;
  Recuerdo &r = recuerdo(enVuelo_.mac, ahoraMs);
  if (r.entregadoHasta > enVuelo_.entregadoAntes) r.entregadoHasta = enVuelo_.entregadoAntes;
  r.reintentoMs = (ahoraMs + REINTENTO_EVENTOS_MS) | 1;  // nunca 0 (0 = sin espera)
  for (size_t i = 0; i < nTrabajos_; i++) {  // lo que quedaba de la tanda seguía tras el que se perdió: se rehace
    if (trabajos_[i].tipo == Tipo::EVENTOS && mismaMac(trabajos_[i].mac, enVuelo_.mac)) {
      quitarTrabajo(i);
      break;
    }
  }
}

void IntercambioVecinos::decidir(uint32_t ahoraMs, bool tengoCanal, bool necesitoHora) {
  const uint32_t miVersion = almacen_.versionListas();
  const uint32_t pendientes = sinc_.pendientes();
  const bool sinCanalLargo = !tengoCanal && !habiaCanal_ && (uint32_t)(ahoraMs - sinCanalDesdeMs_) >= SIN_CANAL_MS;

  for (size_t i = 0; i < radio_.cantidad(); i++) {
    const Vecino &v = radio_.vecino(i);
    const sp::Baliza &b = v.baliza;
    Recuerdo &r = recuerdo(v.mac, ahoraMs);

    // 0. Hora: solo a quien tiene servidor, y como mucho un pedido cada REPETIR_PEDIDO_MS
    if (necesitoHora && !tengoCanal && (b.flags & sp::BALIZA_CON_SERVIDOR) &&
        (!r.pedidoHora || (uint32_t)(ahoraMs - r.pedidoHoraMs) >= REPETIR_PEDIDO_MS) && !radio_.enviando()) {
      sp::Pedido p;
      p.validador = validador_;
      p.que = sp::PEDIDO_HORA;
      if (radio_.enviarPedido(v.mac, p) == RadioVecinos::Envio::ACEPTADO) {
        r.pedidoHora = true;
        r.pedidoHoraMs = ahoraMs;
        cuenta_.pedidosHora++;
        sinc_.esperarHoraDeVecino(ahoraMs + VENTANA_HORA_MS);
      }
    }

    // 1. Listas: el vecino sabe más que yo
    if (b.versionListas > miVersion && b.versionListas < VERSION_INTERMEDIA && miVersion < VERSION_INTERMEDIA &&
        (!r.pedidoListas || (uint32_t)(ahoraMs - r.pedidoListasMs) >= REPETIR_PEDIDO_MS) && !radio_.enviando()) {
      sp::Pedido p;
      p.validador = validador_;
      p.que = sp::PEDIDO_LISTAS;
      p.x = miVersion;
      if (radio_.enviarPedido(v.mac, p) == RadioVecinos::Envio::ACEPTADO) {
        r.pedidoListas = true;
        r.pedidoListasMs = ahoraMs;
        cuenta_.pedidosListas++;
      }
    }

    // 2. Eventos: entregárselos a quien puede subirlos
    if (r.entregaMs && (uint32_t)(ahoraMs - r.entregaMs) >= ENTREGA_VIGENTE_MS) {
      r.entregadoHasta = 0;  // no llegó acuse en mucho tiempo: se vuelve a ofrecer
      r.paquetes = 0;
      r.entregaMs = 0;
    }
    const bool puedeRecibir = (b.flags & sp::BALIZA_ACEPTA_AJENOS) != 0;
    const bool leSirve = (b.flags & sp::BALIZA_CON_SERVIDOR) != 0 || sinCanalLargo;
    if (r.reintentoMs && (int32_t)(ahoraMs - r.reintentoMs) >= 0) r.reintentoMs = 0;
    if (!tengoCanal && puedeRecibir && leSirve && pendientes > 0 && r.paquetes < PAQUETES_EVENTOS &&
        !r.reintentoMs && !hayTrabajo(Tipo::EVENTOS, v.mac)) {
      const uint32_t desde = r.entregadoHasta > almacen_.ultimoAcusado() ? r.entregadoHasta : almacen_.ultimoAcusado();
      if (desde < almacen_.ultimoEvento() && !sinc_.retenido(desde + 1)) {  // con hora estimada esperan a la real
        Trabajo t = {};
        t.tipo = Tipo::EVENTOS;
        memcpy(t.mac, v.mac, 6);
        t.cursor = desde;
        t.restantes = static_cast<uint8_t>(PAQUETES_EVENTOS - r.paquetes);
        encolar(t);
      }
    }

    // 3. Acuses: pedirlos si entregué eventos que siguen sin confirmar (o si este vecino guarda ajenos y yo tengo
    // eventos sin confirmar: pudo quedarse con los míos de otra vez)
    const bool esperoAcuse = pendientes > 0 && (r.entregadoHasta > almacen_.ultimoAcusado() || b.ajenos > 0);
    if (esperoAcuse && (!r.pedidoAcuses || (uint32_t)(ahoraMs - r.pedidoAcusesMs) >= REPETIR_PEDIDO_MS / 2) &&
        !radio_.enviando()) {
      sp::Pedido p;
      p.validador = validador_;
      p.que = sp::PEDIDO_ACUSES;
      if (radio_.enviarPedido(v.mac, p) == RadioVecinos::Envio::ACEPTADO) {
        r.pedidoAcuses = true;
        r.pedidoAcusesMs = ahoraMs;
        cuenta_.pedidosAcuses++;
      }
    }
  }
}

void IntercambioVecinos::ejecutarTrabajo(uint32_t ahoraMs) {
  if (nTrabajos_ == 0 || radio_.enviando()) return;
  Trabajo &t = trabajos_[0];
  size_t n = 0;
  uint32_t siguiente = t.cursor;
  if (t.tipo == Tipo::HORA) {
    // Se espera el ACUSE fresco (llegado después del PEDIDO); mientras, pasan los demás trabajos
    uint32_t llegoMs = 0;
    n = sinc_.ultimoAcuse(buf_, sizeof(buf_), llegoMs);
    const bool fresco = n > 0 && (int32_t)(llegoMs - t.cursor) >= 0;
    if (!fresco) {
      if ((uint32_t)(ahoraMs - t.cursor) >= VENTANA_HORA_MS) {
        quitarTrabajo(0);
      } else if (nTrabajos_ > 1) {
        const Trabajo espera = t;
        quitarTrabajo(0);
        encolar(espera);
      }
      return;
    }
  }
  switch (t.tipo) {
    case Tipo::HORA:
      break;  // ya está en buf_
    case Tipo::LISTAS:
      n = almacen_.listasDesde(t.cursor, buf_, sizeof(buf_), siguiente);
      break;
    case Tipo::ACUSE:
      n = almacen_.acusePara(t.validador, buf_, sizeof(buf_));
      break;
    case Tipo::EVENTOS:
      n = sinc_.empaquetarEventos(t.cursor, buf_, sizeof(buf_), siguiente, ahoraMs);
      break;
  }
  if (n == 0) {  // no hay (más) que entregar
    quitarTrabajo(0);
    return;
  }
  const uint32_t abortadosAlEnviar = radio_.abortados();
  const RadioVecinos::Envio e = radio_.enviarMensaje(t.mac, buf_, n);
  if (e == RadioVecinos::Envio::OCUPADO) return;  // se reintenta en la próxima vuelta
  if (e == RadioVecinos::Envio::RECHAZADO) {
    quitarTrabajo(0);
    return;
  }
  t.cursor = siguiente;
  if (t.tipo == Tipo::LISTAS) cuenta_.listasEntregadas++;
  if (t.tipo == Tipo::ACUSE) cuenta_.acusesEntregados++;
  if (t.tipo == Tipo::HORA) cuenta_.horasEntregadas++;
  if (t.tipo == Tipo::EVENTOS) {
    cuenta_.eventosEntregados++;
    Recuerdo &r = recuerdo(t.mac, ahoraMs);
    enVuelo_.activo = true;
    memcpy(enVuelo_.mac, t.mac, 6);
    enVuelo_.entregadoAntes = r.entregadoHasta;
    enVuelo_.abortadosAntes = abortadosAlEnviar;
    r.entregadoHasta = siguiente;
    r.entregaMs = ahoraMs;
    r.paquetes++;
  }
  if (t.restantes > 0) t.restantes--;
  if (t.restantes == 0) quitarTrabajo(0);
}
