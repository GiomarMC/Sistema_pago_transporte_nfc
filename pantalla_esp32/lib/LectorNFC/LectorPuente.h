// Lector NFC a través del ordenador: puente_nfc.py tiene el ACR122U y reenvía
// los comandos. Protocolo por el puerto serie (una línea por mensaje):
//   ESP32 -> PC:  >n HOLA | HORA | ESPERA ms | CMD hex | ESCRIBE pag hex | FIN | RETIRADA ms
//   PC -> ESP32:  <n OK [datos] | NADA | ERR motivo | CFG clave=valor;clave=valor
//   n = número de petición: la respuesta lo repite y la ESP32 descarta cualquier
//   respuesta con otro número (p. ej. a peticiones antiguas acumuladas en el puerto).
//   Líneas que empiezan por '#' = mensajes de la ESP32 para la consola del PC.
#pragma once
#include "LectorNFC.h"

class LectorPuente : public LectorNFC {
 public:
  explicit LectorPuente(Stream &puerto) : s_(puerto) {}

  // Saluda al puente. Devuelve true si responde; si envía configuración
  // (<CFG ...), la deja en `config`.
  bool saludar(String &config);

  bool esperarTarjeta(uint32_t ms, uint8_t uid[10], size_t &lenUid) override;
  bool comando(const uint8_t *cmd, size_t lenCmd, uint8_t *resp, size_t &lenResp) override;
  bool escribir(uint8_t pagina, const uint8_t *datos, size_t paginas) override;
  void terminar() override;
  bool retirada(uint32_t ms) override;
  uint32_t horaUnix() override;

  bool conectado() const { return conectado_; }

 private:
  // Envía `peticion` y espera una línea que empiece por '<'. Devuelve false si
  // no llega respuesta en `espera` ms (puente cerrado o cable desconectado).
  bool pedir(const String &peticion, String &respuesta, uint32_t espera = 3000);
  Stream &s_;
  bool conectado_ = false;
  uint32_t numero_ = 0;
};
