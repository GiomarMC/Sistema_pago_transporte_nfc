// Formato de los datos del sistema de transporte en la tarjeta (versión 2).
// Traducción exacta de transporte/tarjeta/formato.py y claves.py: los bytes y las
// firmas deben coincidir con los de Python, o las tarjetas emitidas en el PC no
// se podrán leer aquí.
//
// Área del sistema = páginas 8 a 25 (72 bytes, enteros big-endian):
//   0-23  cabecera: 'T' 'R' | versión | estado | id_tarjeta(4) | id_cuenta(4) |
//         tarifa | reservado | días desde 2020-01-01 (2) | firma (8)
//   32-51 registro A ┐ saldo(4, con signo) | operación(2) | fecha unix(4) |
//   52-71 registro B ┘ validador(2) | última recarga(4) | firma(4)
#pragma once
#include <Arduino.h>

namespace tt {

constexpr uint8_t VERSION = 2;
constexpr uint8_t ACTIVA = 1;
constexpr uint8_t BLOQUEADA = 2;

constexpr uint8_t PAG_INICIO = 8;
constexpr uint8_t PAG_CABECERA = 8;
constexpr uint8_t PAG_REGISTRO[2] = {16, 21};
constexpr uint8_t PAG_FIN = 25;
constexpr size_t TAM_AREA = (PAG_FIN - PAG_INICIO + 1) * 4;  // 72
constexpr size_t TAM_CABECERA = 24;
constexpr size_t TAM_REGISTRO = 20;

struct Cabecera {
  uint8_t version = VERSION;
  uint8_t estado = ACTIVA;
  uint32_t idTarjeta = 0;
  uint32_t idCuenta = 0;
  uint8_t tarifa = 0;
  uint16_t dias = 0;  // fecha de emisión, en días desde 2020-01-01
};

struct Registro {
  int32_t saldo = 0;       // céntimos de sol
  uint16_t operacion = 0;  // sube en cada cambio de saldo
  uint32_t fecha = 0;      // unix, UTC
  uint16_t validador = 0;
  uint32_t recarga = 0;    // última recarga de la cuenta aplicada en la tarjeta
};

struct EstadoTarjeta {
  Cabecera cab;
  Registro reg[2];
  bool valido[2] = {false, false};
  int vigente = -1;  // índice del registro válido con mayor n.º de operación
  int ranuraLibre() const { return 1 - vigente; }
  const Registro &registro() const { return reg[vigente]; }
};

enum class Error { OK, SIN_MARCA, VERSION_NO_SOPORTADA, FIRMA_CABECERA, SIN_REGISTRO_VALIDO };
const char *describir(Error e);

// HMAC-SHA256 (mbedtls)
void hmacSha256(const uint8_t *clave, size_t lenClave, const uint8_t *datos, size_t lenDatos,
                uint8_t salida[32]);

// Claves derivadas de la clave maestra y el UID (claves.py)
void claveFirma(const uint8_t maestra[32], const uint8_t *uid, size_t lenUid, uint8_t salida[32]);
void contrasena(const uint8_t maestra[32], const uint8_t *uid, size_t lenUid, uint8_t pwd[4],
                uint8_t pack[2]);

Error decodificar(const uint8_t area[TAM_AREA], const uint8_t *uid, size_t lenUid,
                  const uint8_t clave[32], EstadoTarjeta &salida);
void codificarCabecera(const Cabecera &c, const uint8_t *uid, size_t lenUid,
                       const uint8_t clave[32], uint8_t salida[TAM_CABECERA]);
void codificarRegistro(const Registro &r, uint32_t idTarjeta, const uint8_t *uid, size_t lenUid,
                       const uint8_t clave[32], uint8_t salida[TAM_REGISTRO]);

}  // namespace tt
