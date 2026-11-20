#pragma once

#include <Arduino.h>
#include <PubSubClient.h>

#include "CanalPaquetes.h"
#include "Enlace.h"

// Un solo adaptador MQTT para EnlaceWifi o EnlaceFalso (C2). La contraseña se recibe del
// llamador (NVS), nunca se escribe en la microSD ni se imprime en el log.
class CanalMqtt final : public CanalPaquetes {
 public:
  static constexpr size_t MAX_PAQUETE = 2048;
  CanalMqtt(Enlace &enlace, const char *servidor, uint16_t validador, const char *clave);
  ~CanalMqtt() override;

  const char *nombre() const override;
  uint8_t codigoEnlace() const override { return enlace_.codigo(); }
  bool listo() const override;
  void atender() override;
  bool enviar(const uint8_t *paquete, size_t largo) override;
  size_t recibir(uint8_t *buf, size_t max) override;

  // Pausa MQTT/TLS sin apagar el enlace (por ejemplo, durante configuración).
  void suspender(bool si);
  bool suspendido() const { return suspendido_; }
  void desconectar();
  uint32_t descartados() const { return descartados_; }

 private:
  void recibido(char *topico, uint8_t *datos, unsigned int largo);
  void reintentar(uint32_t ahora);

  Enlace &enlace_;
  mutable PubSubClient mqtt_;  // connected() de PubSubClient no es const
  String servidor_, clave_;
  char usuario_[8] = {}, idCliente_[24] = {};
  char sube_[48] = {}, baja_[48] = {}, conexion_[48] = {};
  bool suspendido_ = false, bufferListo_ = false;
  uint32_t proximoIntento_ = 0, espera_ = 1000, descartados_ = 0;
  struct Mensaje { uint16_t largo = 0; uint8_t datos[MAX_PAQUETE] = {}; } recibidos_[2];
  uint8_t cabeza_ = 0, cola_ = 0, cantidad_ = 0;
};
