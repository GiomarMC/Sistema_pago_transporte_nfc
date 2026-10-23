// Lector NFC PN532 conectado directamente a la ESP32 por I2C (interruptor del
// módulo en I2C: 1 en ON, 2 en OFF). Cableado: SDA, SCL e IRQ; ver README.
//
// Los comandos nativos del NTAG215 (PWD_AUTH, FAST_READ, READ_CNT) van con
// InCommunicateThru y las escrituras con InDataExchange (WRITE 0xA2), igual que
// tarjeta/ntag215.py con el PN532 que lleva dentro el ACR122U.
#pragma once
#include <Wire.h>

#include "LectorNFC.h"

class LectorPN532 : public LectorNFC {
 public:
  LectorPN532(TwoWire &bus, int8_t sda, int8_t scl, uint8_t irq)
      : bus_(bus), sda_(sda), scl_(scl), irq_(irq) {}

  // Despierta el PN532 y lo configura. Se puede repetir si deja de responder.
  bool iniciar();
  bool conectado() const { return conectado_; }
  // "PN532 v1.6" tras iniciar() correctamente
  const String &version() const { return version_; }

  bool esperarTarjeta(uint32_t ms, uint8_t uid[10], size_t &lenUid) override;
  bool comando(const uint8_t *cmd, size_t lenCmd, uint8_t *resp, size_t &lenResp) override;
  bool escribir(uint8_t pagina, const uint8_t *datos, size_t paginas) override;
  void terminar() override;
  bool retirada(uint32_t ms) override;

 private:
  // Envía un comando al PN532 y devuelve en `resp` los datos de su respuesta
  // (sin el código de respuesta). -1 si el PN532 no responde: queda desconectado.
  int intercambiar(const uint8_t *cmd, size_t n, uint8_t *resp, size_t max, uint32_t espera);
  bool enviarTrama(const uint8_t *cmd, size_t n);
  // Espera a que el PN532 tenga datos (IRQ baja) y lee `n` bytes tras el de estado
  bool leerListo(uint8_t *buf, size_t n, uint32_t espera);
  void abortar();  // cancela el comando en curso
  // Busca una tarjeta (InListPassiveTarget). 1 = hay, 0 = no hay, -1 = PN532 caído
  int activar(uint8_t uid[10], size_t &lenUid);
  // Apaga y enciende el campo: la tarjeta se reinicia y pierde la autenticación
  bool reiniciarCampo();
  bool reactivar();

  TwoWire &bus_;
  int8_t sda_, scl_;
  uint8_t irq_;
  bool conectado_ = false;
  String version_;
};
