// M8-01: formato de la BALIZA y tabla de vecinos, sin radio.
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <Fragmentos.h>
#include <TablaVecinos.h>

#include <initializer_list>

namespace {

sp::Baliza baliza(uint16_t validador, uint16_t pendientes = 0) {
  sp::Baliza b;
  b.validador = validador;
  b.pendientes = pendientes;
  return b;
}

const uint8_t MAC_A[6] = {0x24, 0x6F, 0x28, 0xAA, 0xBB, 0x01};
const uint8_t MAC_B[6] = {0x24, 0x6F, 0x28, 0xAA, 0xBB, 0x02};

void formatoDeLaBaliza() {
  sp::Baliza b;
  b.validador = 0x0107;  // 263
  b.versionListas = 0x04030201;
  b.versionTarifas = 0x08070605;
  b.pendientes = 0x0A09;
  b.ajenos = 0x0C0B;
  b.flags = sp::BALIZA_CON_SERVIDOR | sp::BALIZA_ACEPTA_AJENOS;

  uint8_t trama[sp::TAM_TRAMA_MAXIMO] = {};
  assert(sp::armarBaliza(b, trama) == sp::TAM_BALIZA);
  // Bytes exactos del contrato C1 5.10, little-endian
  const uint8_t esperada[sp::TAM_BALIZA] = {'E', 'N', 1, 0, 0x07, 0x01, 0x01, 0x02, 0x03, 0x04,
                                            0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x03};
  assert(memcmp(trama, esperada, sp::TAM_BALIZA) == 0);

  sp::Baliza leida;
  assert(sp::leerBaliza(trama, sp::TAM_BALIZA, leida));
  assert(leida.validador == 263 && leida.versionListas == 0x04030201 &&
         leida.versionTarifas == 0x08070605 && leida.pendientes == 0x0A09 &&
         leida.ajenos == 0x0C0B && leida.flags == 3);

  // Bytes de más al final (una versión futura): se aceptan
  uint8_t larga[sp::TAM_BALIZA + 5];
  memcpy(larga, trama, sp::TAM_BALIZA);
  memset(larga + sp::TAM_BALIZA, 0xEE, 5);
  assert(sp::leerBaliza(larga, sizeof(larga), leida) && leida.validador == 263);

  // Lo que no es una baliza
  assert(!sp::leerBaliza(trama, sp::TAM_BALIZA - 1, leida));  // corta
  uint8_t mala[sp::TAM_BALIZA];
  memcpy(mala, trama, sizeof(mala));
  mala[2] = sp::FRAGMENTO;  // otra clase de trama
  assert(!sp::leerBaliza(mala, sizeof(mala), leida));
  memcpy(mala, trama, sizeof(mala));
  mala[0] = 'X';  // no empieza por EN
  assert(!sp::leerBaliza(mala, sizeof(mala), leida));
  memcpy(mala, trama, sizeof(mala));
  mala[4] = mala[5] = 0;  // validador 0
  assert(!sp::leerBaliza(mala, sizeof(mala), leida));
  assert(!sp::leerBaliza(trama, sp::TAM_TRAMA_MAXIMO + 1, leida));  // demasiado larga
}

void tablaDeVecinos() {
  TablaVecinos t;
  assert(t.cantidad() == 0);

  // Un vecino nuevo, y la misma MAC otra vez solo lo actualiza
  assert(t.actualizar(MAC_A, baliza(105, 3), 1000) == TablaVecinos::Cambio::NUEVO);
  assert(t.cantidad() == 1 && t.en(0).baliza.validador == 105 && t.en(0).ultimaMs == 1000);
  assert(t.actualizar(MAC_A, baliza(105, 7), 3000) == TablaVecinos::Cambio::ACTUALIZADO);
  assert(t.cantidad() == 1 && t.en(0).baliza.pendientes == 7 && t.en(0).ultimaMs == 3000);
  assert(t.actualizar(MAC_B, baliza(107), 3500) == TablaVecinos::Cambio::NUEVO);
  assert(t.cantidad() == 2);

  // Caducidad: a los 10 s exactos sin baliza se olvida; a los 9,999 s todavía no
  Vecino perdidos[TablaVecinos::MAXIMO];
  assert(t.caducar(12999, perdidos) == 0 && t.cantidad() == 2);  // A: 9999 ms
  assert(t.caducar(13000, perdidos) == 1 && t.cantidad() == 1);  // A: 10000 ms
  assert(perdidos[0].baliza.validador == 105 && memcmp(perdidos[0].mac, MAC_A, 6) == 0);
  assert(t.en(0).baliza.validador == 107);  // B sigue
  assert(t.caducar(13500) == 1 && t.cantidad() == 0);  // sin buffer de salida también vale
}

void tablaLlena() {
  TablaVecinos t;
  uint8_t mac[6] = {0x24, 0x6F, 0x28, 0, 0, 0};
  for (size_t i = 0; i < TablaVecinos::MAXIMO; i++) {
    mac[5] = i;
    assert(t.actualizar(mac, baliza(100 + i), 1000 + i) == TablaVecinos::Cambio::NUEVO);
  }
  assert(t.cantidad() == TablaVecinos::MAXIMO);

  // El undécimo no entra y no desaloja a nadie
  mac[5] = 99;
  assert(t.actualizar(mac, baliza(199), 2000) == TablaVecinos::Cambio::LLENA);
  assert(t.cantidad() == TablaVecinos::MAXIMO && t.en(0).baliza.validador == 100);

  // Uno que ya está sigue actualizándose aunque la tabla esté llena
  mac[5] = 4;
  assert(t.actualizar(mac, baliza(104, 9), 2500) == TablaVecinos::Cambio::ACTUALIZADO);

  // Cuando caduca alguno, entra el nuevo (el 4 se acaba de actualizar y sigue)
  assert(t.caducar(11000) == 1);  // solo el de 1000 ms
  assert(t.cantidad() == TablaVecinos::MAXIMO - 1);
  mac[5] = 99;
  assert(t.actualizar(mac, baliza(199), 11000) == TablaVecinos::Cambio::NUEVO);
}

void relojQueDaLaVuelta() {
  // millis() vuelve a 0 a los ~49 días: la edad no debe salir enorme ni negativa
  TablaVecinos t;
  const uint32_t antes = 0xFFFFFFF0u;  // 16 ms antes de dar la vuelta
  assert(t.actualizar(MAC_A, baliza(105), antes) == TablaVecinos::Cambio::NUEVO);
  assert(t.caducar(5000) == 0 && t.cantidad() == 1);  // pasaron 5016 ms
  assert(t.caducar(9983) == 0 && t.cantidad() == 1);  // 9999 ms
  assert(t.caducar(9984) == 1 && t.cantidad() == 0);  // 10000 ms
}

void vaciar() {
  TablaVecinos t;
  t.actualizar(MAC_A, baliza(105), 0);
  t.actualizar(MAC_B, baliza(107), 0);
  t.vaciar();
  assert(t.cantidad() == 0);
  assert(t.actualizar(MAC_A, baliza(105), 0) == TablaVecinos::Cambio::NUEVO);
}

void formatoDelPedido() {
  sp::Pedido p;
  p.validador = 107;
  p.que = sp::PEDIDO_LISTAS;
  p.x = 0x01020304;
  uint8_t trama[sp::TAM_TRAMA_MAXIMO];
  assert(sp::armarPedido(p, trama) == sp::TAM_PEDIDO);
  // los bytes del contrato (C1 5.10): "EN" | clase 3 | 0 | validador(2) | que | x(4), little-endian
  const uint8_t esperado[sp::TAM_PEDIDO] = {'E', 'N', 3, 0, 107, 0, 1, 4, 3, 2, 1};
  assert(memcmp(trama, esperado, sizeof(esperado)) == 0);
  sp::Pedido leido;
  assert(sp::leerPedido(trama, sp::TAM_PEDIDO, leido) && leido.validador == 107 && leido.que == 1 && leido.x == 0x01020304);
  // acuses (3) y tarifas (2)
  p.que = sp::PEDIDO_ACUSES;
  p.x = 0;
  sp::armarPedido(p, trama);
  assert(sp::leerPedido(trama, sp::TAM_PEDIDO, leido) && leido.que == 3);
  // bytes de más al final se ignoran (una versión futura puede añadir campos)
  uint8_t larga[20];
  memcpy(larga, trama, sp::TAM_PEDIDO);
  assert(sp::leerPedido(larga, sizeof(larga), leido));
  // no vale: corto, otra clase, sin validador, `que` desconocido
  assert(!sp::leerPedido(trama, sp::TAM_PEDIDO - 1, leido));
  uint8_t mala[sp::TAM_PEDIDO];
  memcpy(mala, trama, sizeof(mala));
  mala[2] = sp::BALIZA;
  assert(!sp::leerPedido(mala, sizeof(mala), leido));
  memcpy(mala, trama, sizeof(mala));
  mala[4] = mala[5] = 0;
  assert(!sp::leerPedido(mala, sizeof(mala), leido));
  for (int que : {0, sp::PEDIDO_HORA + 1, 255}) {
    memcpy(mala, trama, sizeof(mala));
    mala[6] = static_cast<uint8_t>(que);
    assert(!sp::leerPedido(mala, sizeof(mala), leido));
  }
  // una baliza no se confunde con un pedido, ni al revés
  sp::Baliza b;
  b.validador = 5;
  uint8_t tb[sp::TAM_TRAMA_MAXIMO];
  sp::armarBaliza(b, tb);
  assert(!sp::leerPedido(tb, sp::TAM_BALIZA, leido) && !sp::leerBaliza(trama, sp::TAM_PEDIDO, b));
}

}  // namespace

int main() {
  formatoDelPedido();
  formatoDeLaBaliza();
  tablaDeVecinos();
  tablaLlena();
  relojQueDaLaVuelta();
  vaciar();
  puts("M8-01/03 vecinos: formato de la baliza y del PEDIDO, y tabla (caducidad, llena, reloj) OK");
}
