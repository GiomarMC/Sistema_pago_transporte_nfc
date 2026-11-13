// Contrato C2: canal que mueve paquetes C1 completos.
//
// Puede ser MQTT sobre un Enlace (WiFi) o BLE. El sincronizador (M4) solo
// conoce esta interfaz. Implementaciones: CanalMqtt(Enlace&) (M15), CanalBle (M7);
// para tests, CanalFalso.
//
// Reglas del gestor (las implementa M15, pero los módulos las asumen desde ya):
// - Un solo canal de subida activo a la vez. Prioridad: WiFi > BLE.
// - Un canal de más prioridad que pasa a listo toma el control tras el intercambio en
//   curso y 10 s estable. Nunca se corta un paquete a medias.
// - BLE solo se activa con un celular conectado que pide la cola.
// - Sin canal listo, los eventos esperan en la cola local de la microSD.
#pragma once

#include <stddef.h>
#include <stdint.h>

class CanalPaquetes {
 public:
  virtual ~CanalPaquetes() = default;
  virtual const char *nombre() const = 0;    // "mqtt-wifi" | "ble"
  virtual uint8_t codigoEnlace() const = 0;  // 1 (WiFi) o 3 (BLE)
  virtual bool listo() const = 0;            // puede enviar ahora
  virtual void atender() = 0;                // en cada loop()
  virtual bool enviar(const uint8_t *paquete, size_t largo) = 0;
  // Paquete recibido del servidor (copiado en buf); devuelve su largo o 0 si no hay
  virtual size_t recibir(uint8_t *buf, size_t max) = 0;
};
