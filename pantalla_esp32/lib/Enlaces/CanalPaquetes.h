// Contrato C2: canal que mueve paquetes C1 completos.
//
// El canal de subida es MQTT sobre un Enlace (WiFi): CanalMqtt(Enlace&) (M15). El sincronizador (M4)
// solo conoce esta interfaz; para tests, CanalFalso. Sin BLE (descartado el 27 nov).
//
// Reglas del gestor (M15):
// - Un solo canal de subida, y nunca se corta un paquete a medias: si el WiFi cae durante un
//   intercambio, el canal se suelta al terminarlo.
// - Sin canal listo, los eventos esperan en la cola local de la microSD (y pueden salir por ESP-NOW).
#pragma once

#include <stddef.h>
#include <stdint.h>

class CanalPaquetes {
 public:
  virtual ~CanalPaquetes() = default;
  virtual const char *nombre() const = 0;    // "mqtt-wifi"
  virtual uint8_t codigoEnlace() const = 0;  // 1 (WiFi), C1 5.4
  virtual bool listo() const = 0;            // puede enviar ahora
  virtual void atender() = 0;                // en cada loop()
  virtual bool enviar(const uint8_t *paquete, size_t largo) = 0;
  // Paquete recibido del servidor (copiado en buf); devuelve su largo o 0 si no hay
  virtual size_t recibir(uint8_t *buf, size_t max) = 0;
};
