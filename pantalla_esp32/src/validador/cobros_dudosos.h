// Cobros dudosos (#237): el validador escribió el registro nuevo en la tarjeta pero no pudo comprobarlo al releerlo
// (la tarjeta se retiró justo al final). Puede que la escritura sí terminara: la tarjeta queda cobrada y, si el
// validador no registra el viaje, el servidor nunca se entera.
//
// Por eso cada cobro sin comprobar se anota aquí. La próxima vez que este validador lee esa tarjeta, su registro más
// reciente dice qué pasó:
//   - la operación anotada, de este validador  -> la escritura terminó: hay que registrar el viaje (GRABADO)
//   - una operación anterior                    -> no se escribió nada: se olvida (NO_GRABADO)
//   - una posterior (la tarjeta pasó por otro validador o punto) -> no se puede saber (INCIERTO): se avisa al
//     servidor con una incidencia
// No depende del tiempo que pase hasta la siguiente lectura (el margen de doble toque solo cubría 60 s) ni de que la
// tarjeta ya se conociera. El almacén lo guarda en la microSD para que sobreviva a un reinicio.
//
// Lógica pura, sin Arduino, para probarla en el ordenador.
#pragma once

#include <stddef.h>
#include <stdint.h>

struct CobroDudoso {
  uint32_t idTarjeta = 0;
  uint16_t operacion = 0;  // la que se intentó escribir
  uint32_t fecha = 0;      // del intento
  bool fechaReal = false;  // la placa tenía la hora real al intentarlo
  int32_t monto = 0, saldo = 0;
  uint32_t recargaHasta = 0;  // como en registrarViaje: 0 si el cobro no aplicaba recargas
};

class CobrosDudosos {
 public:
  static constexpr size_t MAXIMO = 8;
  enum class Resultado : uint8_t { NINGUNO, GRABADO, NO_GRABADO, INCIERTO };

  // Uno nuevo. Sustituye al de la misma tarjeta; con la lista llena sale el más antiguo
  void anotar(const CobroDudoso &c);
  // La tarjeta se volvió a leer y su registro más reciente es (operacion, validador). Si había un cobro dudoso de esa
  // tarjeta, lo saca, lo copia en `c` y dice qué pasó con él; NINGUNO si no había.
  Resultado resolver(uint32_t idTarjeta, uint16_t operacion, uint16_t validador, uint16_t yo, CobroDudoso &c);

  size_t cantidad() const { return n_; }
  const CobroDudoso &en(size_t i) const { return lista_[i]; }

  // Una línea por cobro: "id operacion fecha real monto saldo recarga". Devuelve el largo (sin el '\0'); si no cabe,
  // devuelve 0
  size_t aTexto(char *buf, size_t max) const;
  // Lo contrario; ignora las líneas mal formadas
  void deTexto(const char *texto);

 private:
  CobroDudoso lista_[MAXIMO];
  size_t n_ = 0;
  void quitar(size_t i);
};
