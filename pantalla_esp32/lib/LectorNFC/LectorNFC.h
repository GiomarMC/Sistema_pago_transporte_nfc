// Acceso a la tarjeta NTAG215, independiente del lector físico.
//
// LectorPN532: PN532 conectado directamente a la ESP32 (el que usa el validador).
// LectorPuente: el ACR122U del ordenador, a través de puente_nfc.py por USB.
// Para cambiar de lector solo hay que implementar esta interfaz.
#pragma once
#include <Arduino.h>

class LectorNFC {
 public:
  virtual ~LectorNFC() = default;

  // Espera hasta `ms` a que haya una tarjeta; devuelve su UID.
  virtual bool esperarTarjeta(uint32_t ms, uint8_t uid[10], size_t &lenUid) = 0;
  // Comando nativo de la tarjeta (READ, FAST_READ, PWD_AUTH...). false si la tarjeta
  // lo rechaza o no responde (queda reactivada para el siguiente comando).
  virtual bool comando(const uint8_t *cmd, size_t lenCmd, uint8_t *resp, size_t &lenResp) = 0;
  // Escribe `paginas` páginas consecutivas de 4 bytes desde `pagina`.
  virtual bool escribir(uint8_t pagina, const uint8_t *datos, size_t paginas) = 0;
  // Termina la sesión dejando la tarjeta sin autenticar.
  virtual void terminar() = 0;
  // true cuando ya no hay tarjeta en el lector (espera hasta `ms`).
  virtual bool retirada(uint32_t ms) = 0;
  // Hora unix del ordenador, si el lector puede darla (0 si no).
  virtual uint32_t horaUnix() { return 0; }

  // --- Comandos NTAG215 sobre `comando()` ---
  bool autenticar(const uint8_t pwd[4], uint8_t pack[2]) {
    uint8_t cmd[5] = {0x1B, pwd[0], pwd[1], pwd[2], pwd[3]}, r[8];
    size_t n = sizeof(r);
    if (!comando(cmd, 5, r, n) || n != 2) return false;
    pack[0] = r[0];
    pack[1] = r[1];
    return true;
  }
  bool leer(uint8_t desde, uint8_t hasta, uint8_t *salida) {  // FAST_READ
    uint8_t cmd[3] = {0x3A, desde, hasta};
    size_t esperado = (hasta - desde + 1) * 4, n = esperado;
    return comando(cmd, 3, salida, n) && n == esperado;
  }
  bool contador(uint32_t &valor) {  // READ_CNT
    uint8_t cmd[2] = {0x39, 0x02}, r[4];
    size_t n = sizeof(r);
    if (!comando(cmd, 2, r, n) || n != 3) return false;
    valor = r[0] | (uint32_t(r[1]) << 8) | (uint32_t(r[2]) << 16);
    return true;
  }
};
