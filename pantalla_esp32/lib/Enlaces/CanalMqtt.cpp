#include "CanalMqtt.h"

#include <stdio.h>
#include <string.h>

namespace {
constexpr uint16_t PUERTO_MQTT_TLS = 8883;
constexpr uint32_t REINTENTO_MAX_MS = 30000;
constexpr char URGENTE[] = "sp/todos/urgente";
}

CanalMqtt::CanalMqtt(Enlace &enlace, const char *servidor, uint16_t validador, const char *clave,
                     TrabajoEnFondo *fondo)
    : enlace_(enlace), servidor_(servidor ? servidor : ""), clave_(clave ? clave : ""), fondo_(fondo) {
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

CanalMqtt::~CanalMqtt() {
  while (fase_.load() == CONECTANDO) delay(10);  // la otra tarea usa mqtt_ y el flujo: no se pueden destruir
  if (fase_.load() == TERMINADO) terminarConexion();
  desconectar();
}

const char *CanalMqtt::nombre() const { return "mqtt-wifi"; }

bool CanalMqtt::listo() const {
  return fase_.load() == LIBRE && !suspendido_ && enlace_.estado() == EstadoEnlace::Listo && mqtt_.connected();
}

void CanalMqtt::reintentar(uint32_t ahora) {
  proximoIntento_ = ahora + espera_;
  espera_ = espera_ < REINTENTO_MAX_MS / 2 ? espera_ * 2 : REINTENTO_MAX_MS;
}

void CanalMqtt::atender() {
  if (fase_.load() == CONECTANDO) return;  // la otra tarea está con mqtt_ y el flujo
  if (fase_.load() == TERMINADO) terminarConexion();
  if (suspendido_ || enlace_.estado() != EstadoEnlace::Listo || !bufferListo_ ||
      servidor_.length() == 0 || clave_.length() == 0) {
    if (mqtt_.connected()) desconectar();
    return;
  }
  if (!mqtt_.connected()) {
    const uint32_t ahora = millis();
    if (proximoIntento_ && (int32_t)(ahora - proximoIntento_) < 0) return;
    empezarConexion(ahora);
    if (fase_.load() == CONECTANDO) return;  // sigue en la otra tarea; se recoge en un atender() siguiente
    terminarConexion();
    if (!mqtt_.connected()) return;
  }
  if (!mqtt_.loop()) { enlace_.cerrar(); reintentar(millis()); }
}

void CanalMqtt::empezarConexion(uint32_t ahora) {
  inicioConexion_ = ahora;
  exito_ = false;
  enlace_.prestarFlujo(true);
  fase_.store(CONECTANDO);
  if (fondo_ && fondo_->lanzar(&CanalMqtt::conectarEnFondo, this)) return;
  exito_ = conectar();  // sin otra tarea: en el acto
  fase_.store(TERMINADO);
}

void CanalMqtt::conectarEnFondo(void *yo) {
  CanalMqtt &c = *static_cast<CanalMqtt *>(yo);
  c.exito_ = c.conectar();
  c.fase_.store(TERMINADO);  // después de exito_: quien vea TERMINADO ve también el resultado
}

// Todo lo que espera a la red. Solo toca mqtt_ y el flujo del enlace (y lo cierra si falla).
bool CanalMqtt::conectar() {
  Client *flujo = enlace_.abrir(servidor_.c_str(), PUERTO_MQTT_TLS);
  if (!flujo) return false;
  mqtt_.setClient(*flujo).setServer(servidor_.c_str(), PUERTO_MQTT_TLS);
  if (!mqtt_.connect(idCliente_, usuario_, clave_.c_str(), conexion_, 0, true, "0", true)) {
    enlace_.cerrar();
    return false;
  }
  if (!mqtt_.subscribe(baja_, 0) || !mqtt_.subscribe(URGENTE, 0) || !mqtt_.publish(conexion_, "1", true)) {
    mqtt_.disconnect();
    enlace_.cerrar();
    return false;
  }
  return true;
}

void CanalMqtt::terminarConexion() {
  fase_.store(LIBRE);
  ultimaConexionMs_ = millis() - inicioConexion_;
  enlace_.prestarFlujo(false);  // si el enlace quiso cerrar el flujo mientras tanto (red perdida), lo cierra ahora
  if (cerrarAlTerminar_) {
    cerrarAlTerminar_ = false;
    desconectar();
    return;
  }
  if (exito_ && mqtt_.connected()) {
    espera_ = 1000;
    proximoIntento_ = 0;
  } else {
    if (mqtt_.connected()) desconectar();
    reintentar(millis());
  }
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
  if (fase_.load() == CONECTANDO) {  // no se toca mqtt_ ni el flujo mientras los usa la otra tarea
    cerrarAlTerminar_ = true;
    return;
  }
  if (fase_.load() == TERMINADO) {  // la otra tarea acabó y atender() aún no lo recogió: se recoge cerrando
    cerrarAlTerminar_ = true;
    terminarConexion();  // con la fase ya LIBRE vuelve a llamar a desconectar()
    return;
  }
  // PubSubClient::disconnect() usa su cliente sin mirar si lo tiene: si nunca se conectó (sin WiFi desde el
  // arranque) es NULL y la placa se reinicia. Solo se desconecta si hay conexión; el TLS lo cierra enlace_.cerrar()
  if (mqtt_.connected()) {
    mqtt_.publish(conexion_, "0", true);
    mqtt_.disconnect();
  }
  enlace_.cerrar();
}
