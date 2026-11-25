// M8-02: envío por fragmentos con reintentos y límite por encuentro, sin radio.
#include <assert.h>
#include <initializer_list>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <EmisorFragmentos.h>
#include <Fragmentos.h>
#include <TablaVecinos.h>

namespace {

void relleno(uint8_t *m, size_t n, uint8_t semilla) {
  for (size_t i = 0; i < n; i++) m[i] = (uint8_t)(semilla + i * 7 + (i >> 5));
}

// Transmite todo el mensaje con una radio simulada. `falla(indice, intento)` dice si ese
// intento de ese fragmento no llega. Devuelve cómo terminó y, si llegó, el mensaje
// reconstruido por el receptor.
struct Corrida {
  EmisorFragmentos::Fin fin = EmisorFragmentos::Fin::NINGUNO;
  bool completo = false;
  uint8_t reconstruido[2200] = {};
  size_t largo = 0;
  uint32_t tramas = 0;
};

template <typename F>
Corrida transmitir(EmisorFragmentos &e, F falla) {
  Corrida c;
  sp::Reensamblador receptor;
  uint32_t ahora = 1000;
  uint8_t intentoDeEste[16] = {};
  for (int vuelta = 0; vuelta < 200 && e.ocupado(); vuelta++) {
    uint8_t trama[sp::TAM_TRAMA_MAXIMO];
    const uint8_t indice = e.indice();
    const size_t n = e.siguienteTrama(trama, ahora);
    assert(n > 0);
    c.tramas++;
    // Mientras se espera el aviso no sale otra trama
    uint8_t otra[sp::TAM_TRAMA_MAXIMO];
    assert(e.siguienteTrama(otra, ahora) == 0);
    const bool llega = !falla(indice, intentoDeEste[indice]++);
    if (llega) {
      auto r = receptor.recibir(trama, n, ahora);
      if (r == sp::Reensamblador::Resultado::COMPLETO) {
        c.completo = true;
        c.largo = receptor.largo();
        memcpy(c.reconstruido, receptor.mensaje(), c.largo);
      }
    }
    ahora += 3;
    e.resultado(llega, ahora);
  }
  c.fin = e.tomarFin();
  return c;
}

void envioCompleto() {
  uint8_t mensaje[2048];
  relleno(mensaje, sizeof(mensaje), 9);
  EmisorFragmentos e;
  assert(e.iniciar(mensaje, sizeof(mensaje), 105, 77));
  assert(e.total() == 10);
  Corrida c = transmitir(e, [](uint8_t, uint8_t) { return false; });
  assert(c.fin == EmisorFragmentos::Fin::ENTREGADO && c.tramas == 10);
  assert(c.completo && c.largo == sizeof(mensaje) && memcmp(c.reconstruido, mensaje, c.largo) == 0);
  assert(!e.ocupado() && e.tomarFin() == EmisorFragmentos::Fin::NINGUNO);  // el fin se consulta una vez

  // Mensajes de 1 B, de justo un fragmento y de uno más
  for (size_t largo : {size_t(1), size_t(220), size_t(221), size_t(440), size_t(441)}) {
    relleno(mensaje, largo, 3);
    assert(e.iniciar(mensaje, largo, 105, largo));
    c = transmitir(e, [](uint8_t, uint8_t) { return false; });
    assert(c.fin == EmisorFragmentos::Fin::ENTREGADO && c.completo && c.largo == largo &&
           memcmp(c.reconstruido, mensaje, largo) == 0);
  }
}

void mensajesNoValidos() {
  uint8_t mensaje[2100] = {};
  EmisorFragmentos e;
  assert(!e.iniciar(mensaje, 0, 105, 1));       // vacío
  assert(!e.iniciar(nullptr, 10, 105, 1));
  assert(!e.iniciar(mensaje, 2049, 105, 1));    // más que un paquete C1
  assert(e.iniciar(mensaje, 2048, 105, 1));
  assert(!e.iniciar(mensaje, 10, 105, 2));      // ya hay uno en curso
  e.cancelar();
  assert(!e.ocupado() && e.iniciar(mensaje, 10, 105, 3));
}

void reintentos() {
  uint8_t mensaje[2048];
  relleno(mensaje, sizeof(mensaje), 1);
  EmisorFragmentos e;

  // El fragmento 3 falla dos veces y a la tercera llega: el mensaje se entrega entero
  assert(e.iniciar(mensaje, sizeof(mensaje), 105, 1));
  Corrida c = transmitir(e, [](uint8_t i, uint8_t intento) { return i == 3 && intento < 2; });
  assert(c.fin == EmisorFragmentos::Fin::ENTREGADO && c.tramas == 12 && e.reintentos() == 2);
  assert(c.completo && memcmp(c.reconstruido, mensaje, sizeof(mensaje)) == 0);

  // Falla 3 veces y llega a la cuarta (primer intento + 3 reintentos): todavía se entrega
  assert(e.iniciar(mensaje, sizeof(mensaje), 105, 2));
  c = transmitir(e, [](uint8_t i, uint8_t intento) { return i == 5 && intento < 3; });
  assert(c.fin == EmisorFragmentos::Fin::ENTREGADO && c.tramas == 13);

  // Falla 4 veces seguidas: se agotan los 3 reintentos y se aborta el mensaje entero
  assert(e.iniciar(mensaje, sizeof(mensaje), 105, 3));
  const uint32_t antes = e.reintentos();
  c = transmitir(e, [](uint8_t i, uint8_t) { return i == 2; });
  assert(c.fin == EmisorFragmentos::Fin::ABORTADO && !c.completo);
  assert(c.tramas == 2 + 4);  // fragmentos 0 y 1 bien, el 2 cuatro veces
  assert(e.reintentos() - antes == 3 && !e.ocupado());

  // Tras un aborto se puede mandar otro mensaje sin arrastrar nada
  assert(e.iniciar(mensaje, 300, 105, 4));
  c = transmitir(e, [](uint8_t, uint8_t) { return false; });
  assert(c.fin == EmisorFragmentos::Fin::ENTREGADO && c.completo && c.largo == 300);
}

void avisoQueNoLlega() {
  uint8_t mensaje[500];
  relleno(mensaje, sizeof(mensaje), 4);
  EmisorFragmentos e;
  assert(e.iniciar(mensaje, sizeof(mensaje), 105, 1));
  uint8_t t1[sp::TAM_TRAMA_MAXIMO], t2[sp::TAM_TRAMA_MAXIMO];
  const size_t n1 = e.siguienteTrama(t1, 5000);
  assert(n1 > 0);
  e.revisar(5099);  // aún dentro del plazo
  assert(e.siguienteTrama(t2, 5099) == 0);
  e.revisar(5100);  // 100 ms sin aviso: cuenta como fallo y se reintenta el mismo fragmento
  assert(e.reintentos() == 1);
  const size_t n2 = e.siguienteTrama(t2, 5100);
  assert(n2 == n1 && memcmp(t1, t2, n1) == 0);
  e.resultado(true, 5103);
  assert(e.indice() == 1);

  // Un aviso tardío de algo que ya no se espera no cambia nada
  e.resultado(true, 5200);
  e.resultado(false, 5200);
  assert(e.indice() == 1 && e.reintentos() == 1);
}

void limitePorEncuentro() {
  TablaVecinos t;
  const uint8_t mac[6] = {1, 2, 3, 4, 5, 6}, otro[6] = {9, 9, 9, 9, 9, 9};
  sp::Baliza b;
  b.validador = 107;
  assert(t.reservarEnvio(mac) == TablaVecinos::Reserva::NO_ES_VECINO);  // aún no se ha visto
  t.actualizar(mac, b, 0);
  assert(t.esVecino(mac) && !t.esVecino(otro));
  for (int i = 0; i < TablaVecinos::MAX_MENSAJES_POR_ENCUENTRO; i++)
    assert(t.reservarEnvio(mac) == TablaVecinos::Reserva::CONCEDIDA);
  assert(t.reservarEnvio(mac) == TablaVecinos::Reserva::LIMITE);  // el 21.º no
  t.actualizar(mac, b, 2000);  // otra baliza del mismo encuentro no reinicia la cuenta
  assert(t.reservarEnvio(mac) == TablaVecinos::Reserva::LIMITE);
  assert(t.caducar(12000) == 1);  // se pierde de vista: termina el encuentro
  t.actualizar(mac, b, 15000);    // vuelve a verse: otro encuentro
  assert(t.reservarEnvio(mac) == TablaVecinos::Reserva::CONCEDIDA);
}

}  // namespace

int main() {
  envioCompleto();
  mensajesNoValidos();
  reintentos();
  avisoQueNoLlega();
  limitePorEncuentro();
  puts("M8-02 emisor: fragmentos, reintentos (3), aborto, plazo del aviso y límite de 20 por encuentro OK");
}
