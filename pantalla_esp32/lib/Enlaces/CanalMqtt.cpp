#include "CanalMqtt.h"

#include <stdio.h>
#include <string.h>

namespace {
constexpr uint16_t PUERTO_MQTT_TLS = 8883;
constexpr uint32_t REINTENTO_MAX_MS = 30000;
constexpr char URGENTE[] = "sp/todos/urgente";
}

CanalMqtt::CanalMqtt(Enlace &enlace, const char *servidor, uint16_t validador, const char *clave)
    : enlace_(enlace), servidor_(servidor ? servidor : ""), clave_(clave ? clave : "") {
  snprintf(usuario_, sizeof(usuario_), "%u", static_cast<unsigned>(validador));
  snprintf(idCliente_, sizeof(idCliente_), "subepe-%u", static_cast<unsigned>(validador));
  snprintf(sube_, sizeof(sube_), "sp/v/%u/sube", static_cast<unsigned>(validador));
  snprintf(baja_, sizeof(baja_), "sp/v/%u/baja", static_cast<unsigned>(validador));
  snprintf(conexion_, sizeof(conexion_), "sp/v/%u/conexion", static_cast<unsigned>(validador));
  bufferListo_ = mqtt_.setBufferSize(2200);
  mqtt_.setKeepAlive(60).setSocketTimeout(1);
  mqtt_.setCallback([this](char *topico, uint8_t *datos, unsigned int largo) {
    recibido(topico, datos, largo);
  });
}

CanalMqtt::~CanalMqtt() { desconectar(); }

const char *CanalMqtt::nombre() const { return "mqtt-wifi"; }

bool CanalMqtt::listo() const {
  return !suspendido_ && enlace_.estado() == EstadoEnlace::Listo && mqtt_.connected();
}

void CanalMqtt::reintentar(uint32_t ahora) {
  proximoIntento_ = ahora + espera_;
  espera_ = espera_ < REINTENTO_MAX_MS / 2 ? espera_ * 2 : REINTENTO_MAX_MS;
}

void CanalMqtt::atender() {
  if (suspendido_ || enlace_.estado() != EstadoEnlace::Listo || !bufferListo_ ||
      servidor_.length() == 0 || clave_.length() == 0) {
    if (mqtt_.connected()) desconectar();
    return;
  }
  if (!mqtt_.connected()) {
    const uint32_t ahora = millis();
    if (proximoIntento_ && (int32_t)(ahora - proximoIntento_) < 0) return;
    Client *flujo = enlace_.abrir(servidor_.c_str(), PUERTO_MQTT_TLS);
    if (!flujo) { reintentar(ahora); return; }
    mqtt_.setClient(*flujo).setServer(servidor_.c_str(), PUERTO_MQTT_TLS);
    if (!mqtt_.connect(idCliente_, usuario_, clave_.c_str(), conexion_, 0, true, "0", true)) {
      enlace_.cerrar();
      reintentar(ahora);
      return;
    }
    if (!mqtt_.subscribe(baja_, 0) || !mqtt_.subscribe(URGENTE, 0) ||
        !mqtt_.publish(conexion_, "1", true)) {
      desconectar();
      reintentar(ahora);
      return;
    }
    espera_ = 1000;
    proximoIntento_ = 0;
  }
  if (!mqtt_.loop()) { enlace_.cerrar(); reintentar(millis()); }
}

bool CanalMqtt::enviar(const uint8_t *paquete, size_t largo) {
  return paquete && largo > 0 && largo <= MAX_PAQUETE && listo() &&
         mqtt_.publish(sube_, paquete, static_cast<unsigned int>(largo), false);
}

void CanalMqtt::recibido(char *topico, uint8_t *datos, unsigned int largo) {
  if (!topico || (strcmp(topico, baja_) != 0 && strcmp(topico, URGENTE) != 0)) return;
  if (!datos || !largo || largo > MAX_PAQUETE || cantidad_ == 2) { ++descartados_; return; }
  Mensaje &destino = recibidos_[cola_];
  memcpy(destino.datos, datos, largo);
  destino.largo = static_cast<uint16_t>(largo);
  cola_ = (cola_ + 1) % 2;
  ++cantidad_;
}

size_t CanalMqtt::recibir(uint8_t *buf, size_t max) {
  if (!cantidad_ || !buf || max < recibidos_[cabeza_].largo) return 0;
  Mensaje &origen = recibidos_[cabeza_];
  memcpy(buf, origen.datos, origen.largo);
  const size_t largo = origen.largo;
  origen.largo = 0;
  cabeza_ = (cabeza_ + 1) % 2;
  --cantidad_;
  return largo;
}

void CanalMqtt::suspender(bool si) {
  if (suspendido_ == si) return;
  suspendido_ = si;
  if (si) desconectar();
  else proximoIntento_ = 0;
}

void CanalMqtt::desconectar() {
  if (mqtt_.connected()) mqtt_.publish(conexion_, "0", true);
  mqtt_.disconnect();
  enlace_.cerrar();
}
