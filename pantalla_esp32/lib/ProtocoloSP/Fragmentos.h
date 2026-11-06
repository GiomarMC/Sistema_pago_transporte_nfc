// Tramas "EN" de BLE y ESP-NOW (contrato C1 5.10): un paquete C1 de hasta 2048 B viaja
// partido en fragmentos de 220 B como mucho. Lo usan M7 (BLE) y M8 (ESP-NOW).
//
// Trama (<= 240 B, little-endian):
//   'E' 'N' | clase | reservado | validador emisor(2) | ...según la clase
//   FRAGMENTO: id_mensaje(4) | indice | total (<= 10) | largo | datos (<= 220 B)
// El índice empieza en 0. Todos los fragmentos menos el último llevan 220 B, así cada
// uno va en indice × 220 del mensaje.
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "ProtocoloSP.h"

namespace sp {

constexpr uint8_t BALIZA = 1, FRAGMENTO = 2, PEDIDO = 3;  // clase de la trama
constexpr size_t TAM_TRAMA_MAXIMO = 240;
constexpr size_t TAM_CABECERA_FRAGMENTO = 13;  // 6 comunes + 7 del fragmento
constexpr size_t DATOS_POR_FRAGMENTO = 220;
constexpr size_t MAX_FRAGMENTOS = 10;
constexpr uint32_t ESPERA_FRAGMENTOS_MS = 3000;

// Cuántos fragmentos hacen falta para un mensaje de `largo` B (0 si no cabe en 10)
uint8_t contarFragmentos(size_t largo);

// Escribe en `trama` el fragmento `indice` del mensaje. Devuelve el largo de la trama, o 0
// si el índice o el mensaje no son válidos.
size_t armarFragmento(const uint8_t *mensaje, size_t largo, uint16_t emisor, uint32_t idMensaje,
                      uint8_t indice, uint8_t trama[TAM_TRAMA_MAXIMO]);

// Junta los fragmentos de un mensaje a la vez (uno por canal o por bus vecino). Si llega
// un fragmento de otro mensaje, o pasan 3 s sin completarlo, el anterior se descarta
// entero: nunca se entrega un mensaje con huecos.
class Reensamblador {
 public:
  enum class Resultado : uint8_t { INCOMPLETO, COMPLETO, INVALIDA };

  // `ahoraMs` es millis(); se pasa como argumento para poder probar el tiempo límite.
  Resultado recibir(const uint8_t *trama, size_t largo, uint32_t ahoraMs);
  // Descarta el mensaje en curso si venció el tiempo límite. Llamarlo en el loop.
  void revisar(uint32_t ahoraMs);

  bool enCurso() const { return recibidos_ != 0; }
  // Válidos tras un COMPLETO, hasta el siguiente recibir()
  const uint8_t *mensaje() const { return datos_; }
  size_t largo() const { return largo_; }
  uint16_t emisor() const { return emisor_; }
  uint32_t descartados() const { return descartados_; }

 private:
  void descartar();

  uint8_t datos_[MAX_FRAGMENTOS * DATOS_POR_FRAGMENTO];
  size_t largo_ = 0;
  uint16_t emisor_ = 0;
  uint32_t idMensaje_ = 0;
  uint8_t total_ = 0;
  uint16_t recibidos_ = 0;  // un bit por índice
  uint32_t inicioMs_ = 0;
  uint32_t descartados_ = 0;
};

}  // namespace sp
