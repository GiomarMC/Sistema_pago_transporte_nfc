// RadioVecinos sobre VecinosEspNow: lo que IntercambioVecinos (M8-03) necesita de la radio.
#pragma once

#include <VecinosEspNow.h>

#include "intercambio_vecinos.h"

class RadioEspNow final : public RadioVecinos {
 public:
  explicit RadioEspNow(VecinosEspNow &vecinos) : vecinos_(vecinos) {}

  size_t cantidad() const override { return vecinos_.cantidad(); }
  const Vecino &vecino(size_t i) const override { return vecinos_.vecino(i); }
  bool enviando() const override { return vecinos_.enviando(); }
  uint32_t abortados() const override { return vecinos_.mensajesAbortados(); }
  Envio enviarMensaje(const uint8_t mac[6], const uint8_t *mensaje, size_t largo) override {
    return traducir(vecinos_.enviar(mac, mensaje, largo));
  }
  Envio enviarPedido(const uint8_t mac[6], const sp::Pedido &pedido) override {
    return traducir(vecinos_.enviarPedido(mac, pedido));
  }
  size_t recibirMensaje(uint8_t *buf, size_t max, uint8_t mac[6]) override { return vecinos_.recibir(buf, max, mac); }
  bool recibirPedido(uint8_t mac[6], sp::Pedido &pedido) override { return vecinos_.recibirPedido(mac, pedido); }
  void publicar(const sp::Baliza &baliza) override { vecinos_.publicar(baliza); }

 private:
  static Envio traducir(VecinosEspNow::Envio e) {
    switch (e) {
      case VecinosEspNow::Envio::ACEPTADO: return Envio::ACEPTADO;
      case VecinosEspNow::Envio::OCUPADO: return Envio::OCUPADO;
      default: return Envio::RECHAZADO;  // no es vecino, límite del encuentro, pausado, error de radio...
    }
  }
  VecinosEspNow &vecinos_;
};
