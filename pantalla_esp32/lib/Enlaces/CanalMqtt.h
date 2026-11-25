#pragma once

#include <Arduino.h>
#include <PubSubClient.h>

#include <atomic>

#include "CanalPaquetes.h"
#include "Enlace.h"
#include "TrabajoEnFondo.h"

// Un solo adaptador MQTT para EnlaceWifi o EnlaceFalso (C2). La contraseña se recibe del
// llamador (NVS), nunca se escribe en la microSD ni se imprime en el log.
//
// Conectar (abrir el flujo TLS, CONNECT, suscripciones y el "1" de conexión) tarda segundos. Con un TrabajoEnFondo
// se hace en otra tarea y atender() vuelve enseguida (#129). Mientras tanto esa tarea es la única que toca mqtt_ y
// el flujo: listo() es false, enviar() no envía, y desconectar() o suspender() esperan a que termine. Al enlace se le
// presta el flujo (Enlace::prestarFlujo) para que no lo cierre a mitad si se pierde la red.
// Sin TrabajoEnFondo se conecta en el acto, dentro de atender().
class CanalMqtt final : public CanalPaquetes {
 public:
  static constexpr size_t MAX_PAQUETE = 2048;
  CanalMqtt(Enlace &enlace, const char *servidor, uint16_t validador, const char *clave,
            TrabajoEnFondo *fondo = nullptr);
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
  bool conectando() const { return fase_.load() == CONECTANDO; }  // en la otra tarea, ahora mismo
  uint32_t ultimaConexionMs() const { return ultimaConexionMs_; }  // lo que tardó el último intento (bien o mal)

 private:
  enum Fase : uint8_t { LIBRE, CONECTANDO, TERMINADO };
  void recibido(char *topico, uint8_t *datos, unsigned int largo);
  void reintentar(uint32_t ahora);
  void empezarConexion(uint32_t ahora);
  bool conectar();  // la parte lenta; en la otra tarea si hay fondo_
  static void conectarEnFondo(void *yo);
  void terminarConexion();  // de vuelta en loop()

  Enlace &enlace_;
  mutable PubSubClient mqtt_;  // connected() de PubSubClient no es const
  String servidor_, clave_;
  char usuario_[8] = {}, idCliente_[24] = {};
  char sube_[48] = {}, baja_[48] = {}, conexion_[48] = {};
  bool suspendido_ = false, bufferListo_ = false;
  uint32_t proximoIntento_ = 0, espera_ = 1000, descartados_ = 0;
  TrabajoEnFondo *fondo_;
  std::atomic<uint8_t> fase_{LIBRE};
  bool exito_ = false;              // lo escribe conectar() antes de pasar a TERMINADO
  bool cerrarAlTerminar_ = false;   // desconectar() o suspender() mientras conectaba
  uint32_t inicioConexion_ = 0, ultimaConexionMs_ = 0;
  struct Mensaje { uint16_t largo = 0; uint8_t datos[MAX_PAQUETE] = {}; } recibidos_[2];
  uint8_t cabeza_ = 0, cola_ = 0, cantidad_ = 0;
  uint32_t llenaDesde_ = 0;  // millis() desde que la cola está llena; 0 si no lo está
  static constexpr uint32_t COLA_LLENA_MAX_MS = 5000;
};
