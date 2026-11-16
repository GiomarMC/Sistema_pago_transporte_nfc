// Contrato C2: enlace IP hacia internet (WiFi; el validador no lleva módem 4G).
//
// Da un flujo TLS hacia el servidor, por el que corre MQTT (CanalMqtt, M15).
// Implementaciones: EnlaceWifi (M6); para tests, EnlaceFalso.
#pragma once

#include <Client.h>
#include <stdint.h>

enum class EstadoEnlace : uint8_t { Apagado, Conectando, Listo, Error };

class Enlace {
 public:
  virtual ~Enlace() = default;
  virtual const char *nombre() const = 0;  // "wifi"
  virtual uint8_t codigo() const = 0;      // 1 wifi (campo enlace de HOLA)
  virtual void encender() = 0;             // empieza a conectar sin bloquear
  virtual void apagar() = 0;               // libera la radio
  virtual void atender() = 0;              // en cada loop(); nunca bloquea más de 50 ms
  virtual EstadoEnlace estado() const = 0;
  virtual int8_t senal() const = 0;  // dBm/2, 0 si no se sabe
  // Cliente TLS ya conectado a host:puerto (verificado con raices_tls.h).
  // nullptr si el enlace no está Listo o falla la conexión.
  virtual Client *abrir(const char *host, uint16_t puerto) = 0;
  virtual void cerrar() = 0;
  virtual uint32_t bytesTx() const = 0;  // desde el arranque
  virtual uint32_t bytesRx() const = 0;
};
