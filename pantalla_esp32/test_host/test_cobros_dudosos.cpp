// #237: cobros escritos en la tarjeta sin poder comprobarlos. Sin tarjeta ni microSD.
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "cobros_dudosos.h"

namespace {

using R = CobrosDudosos::Resultado;
constexpr uint16_t YO = 106, OTRO = 105;

CobroDudoso cobro(uint32_t tarjeta, uint16_t operacion, int32_t saldo = 1600) {
  CobroDudoso c;
  c.idTarjeta = tarjeta;
  c.operacion = operacion;
  c.fecha = 1795481943;
  c.fechaReal = true;
  c.monto = 130;
  c.saldo = saldo;
  c.recargaHasta = 0;
  return c;
}

void elCasoDeLaPrueba() {
  // 23 nov: la operación 33 se escribió, la relectura falló y la tarjeta volvió con la 33 grabada por este validador
  CobrosDudosos d;
  d.anotar(cobro(1, 33));
  CobroDudoso c;
  assert(d.resolver(1, 33, YO, YO, c) == R::GRABADO);
  assert(c.operacion == 33 && c.saldo == 1600 && c.monto == 130 && c.fecha == 1795481943 && c.fechaReal);
  assert(d.cantidad() == 0);
  // Ya resuelto: una segunda lectura no lo registra otra vez
  assert(d.resolver(1, 33, YO, YO, c) == R::NINGUNO);
}

void noSeGrabo() {
  // La escritura no terminó: la tarjeta sigue con la operación anterior
  CobrosDudosos d;
  d.anotar(cobro(1, 33));
  CobroDudoso c;
  assert(d.resolver(1, 32, OTRO, YO, c) == R::NO_GRABADO && d.cantidad() == 0);
  d.anotar(cobro(1, 33));
  assert(d.resolver(1, 32, YO, YO, c) == R::NO_GRABADO);
}

void incierto() {
  CobrosDudosos d;
  CobroDudoso c;
  // La tarjeta pasó por otro validador antes de volver: va por la 34, no se sabe si la 33 fue nuestra
  d.anotar(cobro(1, 33));
  assert(d.resolver(1, 34, OTRO, YO, c) == R::INCIERTO && c.operacion == 33 && d.cantidad() == 0);
  // La misma operación pero de otro validador: otro la usó, así que la nuestra no se grabó... o no se sabe
  d.anotar(cobro(1, 33));
  assert(d.resolver(1, 33, OTRO, YO, c) == R::INCIERTO);
}

void otrasTarjetas() {
  CobrosDudosos d;
  CobroDudoso c;
  d.anotar(cobro(1, 33));
  d.anotar(cobro(4, 2));
  assert(d.resolver(7, 10, YO, YO, c) == R::NINGUNO && d.cantidad() == 2);  // una que no tiene nada anotado
  assert(d.resolver(4, 2, YO, YO, c) == R::GRABADO && c.idTarjeta == 4);
  assert(d.cantidad() == 1 && d.en(0).idTarjeta == 1);
}

void unoPorTarjetaYLimite() {
  CobrosDudosos d;
  CobroDudoso c;
  // Dos fallos seguidos con la misma tarjeta (el segundo intento escribe la misma operación o la siguiente): queda el
  // último
  d.anotar(cobro(1, 33));
  d.anotar(cobro(1, 34));
  assert(d.cantidad() == 1 && d.en(0).operacion == 34);
  // Lleno: sale el más antiguo
  CobrosDudosos l;
  for (uint32_t i = 1; i <= CobrosDudosos::MAXIMO + 2; i++) l.anotar(cobro(i, 5));
  assert(l.cantidad() == CobrosDudosos::MAXIMO && l.en(0).idTarjeta == 3);
  assert(l.resolver(1, 5, YO, YO, c) == R::NINGUNO && l.resolver(CobrosDudosos::MAXIMO + 2, 5, YO, YO, c) == R::GRABADO);
}

void texto() {
  CobrosDudosos d;
  CobroDudoso a = cobro(1, 33), b = cobro(900000004, 65535, -40);
  b.fechaReal = false;
  b.recargaHasta = 7;
  d.anotar(a);
  d.anotar(b);
  char buf[CobrosDudosos::MAXIMO * 80];
  const size_t largo = d.aTexto(buf, sizeof(buf));
  assert(largo == strlen(buf));
  assert(strcmp(buf, "1 33 1795481943 1 130 1600 0\n900000004 65535 1795481943 0 130 -40 7\n") == 0);

  // Ida y vuelta (sobrevive a un reinicio)
  CobrosDudosos e;
  e.deTexto(buf);
  assert(e.cantidad() == 2);
  const CobroDudoso &x = e.en(1);
  assert(x.idTarjeta == 900000004 && x.operacion == 65535 && !x.fechaReal && x.saldo == -40 && x.recargaHasta == 7);

  // Líneas rotas (corte de energía a mitad de escribir) o basura: se ignoran, las buenas quedan
  e.deTexto("1 33 1795481943 1 130 1600 0\n2 34 17954\nbasura\n0 5 1 1 1 1 1\n3 70000 1 1 1 1 1\n4 5 1 2 1 1 1\n"
            "5 6 1 1 1 1 1 extra\n6 7 1 0 130 50 0");
  assert(e.cantidad() == 2 && e.en(0).idTarjeta == 1 && e.en(1).idTarjeta == 6);  // la última sin salto de línea vale
  e.deTexto("");
  assert(e.cantidad() == 0);

  // No cabe: 0 y cadena vacía, nunca a medias
  char poco[20];
  assert(d.aTexto(poco, sizeof(poco)) == 0 && poco[0] == '\0');
  CobrosDudosos vacio;
  assert(vacio.aTexto(buf, sizeof(buf)) == 0 && buf[0] == '\0');
}

}  // namespace

int main() {
  elCasoDeLaPrueba();
  noSeGrabo();
  incierto();
  otrasTarjetas();
  unoPorTarjetaYLimite();
  texto();
  puts("#237 cobros dudosos: grabado, no grabado, incierto, un registro por tarjeta, límite y texto OK");
}
