// Quién hace un trabajo largo sin parar loop(). CanalMqtt lo usa para conectar (TLS + CONNECT de MQTT, unos 3-4 s
// contra el broker de producción), que bloquearía el cobro de una tarjeta que llegue en ese momento (#129).
//
//   En la placa: TareaFondo (una tarea de FreeRTOS en el núcleo 0).
//   En el PC: sin trabajador (nullptr) se hace en el acto, o un falso que el test termina cuando quiere.
#pragma once

class TrabajoEnFondo {
 public:
  using Trabajo = void (*)(void *arg);
  virtual ~TrabajoEnFondo() = default;
  // Empieza trabajo(arg) en otra tarea y vuelve enseguida. false si no pudo (y entonces no lo hace): el llamador
  // lo hace en el acto. Un solo trabajo a la vez: quien lo lanza espera a que termine antes de lanzar otro.
  virtual bool lanzar(Trabajo trabajo, void *arg) = 0;
};
