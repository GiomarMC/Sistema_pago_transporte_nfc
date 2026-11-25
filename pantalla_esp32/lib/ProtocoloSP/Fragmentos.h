// Tramas "EN" de BLE y ESP-NOW (contrato C1 5.10): un paquete C1 de hasta 2048 B viaja
// partido en fragmentos de 220 B como mucho. Lo usan M7 (BLE) y M8 (ESP-NOW).
//
// Trama (<= 240 B, little-endian):
//   'E' 'N' | clase | reservado | validador emisor(2) | ...según la clase
//   BALIZA:    version_listas(4) | version_tarifas(4) | pendientes(2) | ajenos(2) | flags | [canal]
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

// --- BALIZA (solo ESP-NOW, a difusión cada 2 s ± 500 ms) ------------------------------------
// Dice a los buses cercanos qué sabe y qué lleva este bus, para decidir si vale la pena
// intercambiar datos. Va sin firmar: solo anuncia, no es un dato que otro bus deba creer.
constexpr size_t TAM_BALIZA = 19;  // 6 comunes + 13 propios: lo mínimo que se acepta
// Desde nov 2026 la baliza lleva un byte más al final: el canal de radio en que está quien la envía (1-13). Un
// firmware anterior lo ignora (los bytes de más se descartan) y uno nuevo que lee una baliza de 19 B lo da por 0
// (desconocido). Sirve para que un bus que busca a sus vecinos sepa a qué canal ir: los canales de 2,4 GHz se solapan
// y de cerca una baliza enviada en el 6 se oye también en el 5 o el 7.
constexpr size_t TAM_BALIZA_CON_CANAL = 20;
constexpr uint8_t BALIZA_CON_SERVIDOR = 1;   // bit 0: tiene canal con el servidor ahora
constexpr uint8_t BALIZA_ACEPTA_AJENOS = 2;  // bit 1: acepta paquetes de otros buses

struct Baliza {
  uint16_t validador = 0;  // quién la envía (1-899)
  uint32_t versionListas = 0, versionTarifas = 0;
  uint16_t pendientes = 0;  // eventos propios sin acuse
  uint16_t ajenos = 0;      // paquetes de otros buses guardados para reenviar
  uint8_t flags = 0;
  uint8_t canal = 0;  // canal de radio del emisor; 0 = no lo dice (baliza de 19 B)
};

// Escribe la baliza en `trama` y devuelve su largo (TAM_BALIZA_CON_CANAL).
size_t armarBaliza(const Baliza &baliza, uint8_t trama[TAM_TRAMA_MAXIMO]);
// Lee una baliza. false si no lo es: otro tipo de trama, muy corta o sin validador. Los
// bytes de más al final se ignoran, para que una versión futura pueda añadir campos.
bool leerBaliza(const uint8_t *trama, size_t largo, Baliza &baliza);

// --- PEDIDO (solo ESP-NOW, unicast): un bus le pide algo a otro (M8-03) ------------------------
// Va sin firmar y sin fragmentar: solo pide, y lo que se entrega como respuesta son paquetes C1 que ya
// llevan su propia firma. 'que': 1 = LISTAS desde la versión x, 2 = TARIFAS (x no se usa), 3 = los ACUSE
// firmados que tengas para mí (x no se usa), 4 = la hora: quien tiene servidor manda un HOLA y reenvía el ACUSE
// (firmado, con la hora del servidor) que le llega como respuesta (x no se usa). Un firmware que no conoce el 4 lo
// descarta.
constexpr size_t TAM_PEDIDO = 11;  // 6 comunes + que(1) + x(4)
constexpr uint8_t PEDIDO_LISTAS = 1, PEDIDO_TARIFAS = 2, PEDIDO_ACUSES = 3, PEDIDO_HORA = 4;

struct Pedido {
  uint16_t validador = 0;  // quién pide (1-899)
  uint8_t que = 0;
  uint32_t x = 0;
};

size_t armarPedido(const Pedido &pedido, uint8_t trama[TAM_TRAMA_MAXIMO]);
// false si no es un PEDIDO: otra clase, muy corto, sin validador o con un `que` desconocido. Los bytes de más
// al final se ignoran.
bool leerPedido(const uint8_t *trama, size_t largo, Pedido &pedido);

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
