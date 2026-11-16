// CanalPaquetes falso para tests (M0-03): no usa radio ni red.
//
//   CanalFalso canal;                  // "mqtt-wifi", enlace 1, listo
//   sincronizador.usar(canal);         // el código bajo prueba solo ve CanalPaquetes
//   canal.inyectar(acuse, largo);      // lo que "manda el servidor"
//   canal.sacarEnviado(buf, sizeof(buf));  // lo que el código envió, en orden
//   canal.ponerListo(false);           // simula que el canal se cae
//
// Solo para tests: usa memoria dinámica, a diferencia del firmware real.
#pragma once

#include <string.h>

#include <deque>
#include <vector>

#include "CanalPaquetes.h"

class CanalFalso : public CanalPaquetes {
 public:
  explicit CanalFalso(const char *nombre = "mqtt-wifi", uint8_t codigoEnlace = 1)
      : nombre_(nombre), codigo_(codigoEnlace) {}

  // --- CanalPaquetes -----------------------------------------------------------------

  const char *nombre() const override { return nombre_; }
  uint8_t codigoEnlace() const override { return codigo_; }
  bool listo() const override { return listo_; }
  void atender() override { atenciones_++; }

  // Sin listo, o con un paquete vacío, rechaza el envío y no guarda nada.
  bool enviar(const uint8_t *paquete, size_t largo) override {
    if (!listo_ || paquete == nullptr || largo == 0) {
      rechazados_++;
      return false;
    }
    enviados_.emplace_back(paquete, paquete + largo);
    return true;
  }

  // Entrega los inyectados en orden. Si el siguiente no cabe en buf, lo descarta y
  // devuelve 0 (cuenta en descartados()), para no atascar la cola.
  size_t recibir(uint8_t *buf, size_t max) override {
    if (porRecibir_.empty()) return 0;
    std::vector<uint8_t> p = std::move(porRecibir_.front());
    porRecibir_.pop_front();
    if (p.size() > max) {
      descartados_++;
      return 0;
    }
    memcpy(buf, p.data(), p.size());
    return p.size();
  }

  // --- Control desde el test ---------------------------------------------------------

  void ponerListo(bool si) { listo_ = si; }

  // Lo que recibir() entregará después, como si viniera del servidor
  void inyectar(const uint8_t *paquete, size_t largo) {
    porRecibir_.emplace_back(paquete, paquete + largo);
  }

  size_t enviados() const { return enviados_.size(); }
  size_t porRecibir() const { return porRecibir_.size(); }
  uint32_t rechazados() const { return rechazados_; }
  uint32_t descartados() const { return descartados_; }
  uint32_t atenciones() const { return atenciones_; }

  // Saca el paquete enviado más antiguo (copiado en buf). 0 si no hay o no cabe.
  size_t sacarEnviado(uint8_t *buf, size_t max) {
    if (enviados_.empty() || enviados_.front().size() > max) return 0;
    size_t largo = enviados_.front().size();
    memcpy(buf, enviados_.front().data(), largo);
    enviados_.pop_front();
    return largo;
  }

  void vaciar() {
    enviados_.clear();
    porRecibir_.clear();
    rechazados_ = descartados_ = atenciones_ = 0;
  }

 private:
  const char *nombre_;
  uint8_t codigo_;
  bool listo_ = true;
  std::deque<std::vector<uint8_t>> enviados_, porRecibir_;
  uint32_t rechazados_ = 0, descartados_ = 0, atenciones_ = 0;
};
