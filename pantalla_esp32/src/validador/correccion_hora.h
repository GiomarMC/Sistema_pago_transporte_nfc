// Corrección de la fecha de los eventos cobrados con la hora ESTIMADA.
//
// La placa no tiene reloj con batería: al arrancar parte de la última hora guardada en la NVS, que va por detrás de
// la real todo lo que estuvo apagada. Los eventos que se guardan en ese tiempo llevan esa fecha estimada. Pero el
// reloj estimado y el real avanzan con el mismo millis(), así que la diferencia entre los dos es CONSTANTE durante
// el arranque: cuando llega la hora real (servidor, vecino u ordenador) se sabe exactamente cuánto hay que sumar.
//
// Por eso: cada evento con hora estimada se anota aquí (solo en RAM), y no sale de la placa mientras no se pueda
// corregir (retenido). Con la hora real, al empaquetarlo se le suma la diferencia. Si la placa se apaga antes de
// recibir la hora real, se pierde lo anotado y esos eventos saldrán con la fecha estimada (no hay forma de saber
// cuánto estuvo apagada).
//
// La hora estimada puede saltar dentro del mismo arranque (cobrar() la adelanta a la fecha firmada de la tarjeta para
// no cobrar dos veces): cada salto empieza un tramo nuevo con su propia base. Lógica pura, sin Arduino.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <ProtocoloSP.h>

class CorreccionHora {
 public:
  static constexpr uint8_t TRAMOS = 8;

  // Un evento guardado con la hora estimada: su fecha salió de baseUnix + (millis() - baseMillis) / 1000. Los ids
  // llegan en orden creciente. Con la hora real ya conocida no hace nada.
  void anotar(uint32_t idEvento, uint32_t baseUnix, uint32_t baseMillis) {
    if (real_ || idEvento == 0) return;
    if (n_ > 0) {
      Tramo &u = tramos_[n_ - 1];
      if ((u.baseUnix == baseUnix && u.baseMillis == baseMillis) || n_ == TRAMOS) {  // mismo reloj (o sin sitio)
        if (idEvento > u.hasta) u.hasta = idEvento;
        return;
      }
    }
    tramos_[n_++] = {idEvento, idEvento, baseUnix, baseMillis, 0};
  }

  // Llegó la hora real: `unix` era la hora en el instante `ms` (millis()). Fija cuánto se corrige cada tramo.
  void horaReal(uint32_t unix, uint32_t ms) {
    if (real_) return;
    real_ = true;
    for (uint8_t i = 0; i < n_; i++) {
      Tramo &t = tramos_[i];
      // hora real en el instante m = unix + (m - ms)/1000; estimada = baseUnix + (m - baseMillis)/1000
      const int64_t difMs = ((int64_t)unix * 1000 - (int64_t)ms) - ((int64_t)t.baseUnix * 1000 - (int64_t)t.baseMillis);
      t.delta = (int32_t)((difMs >= 0 ? difMs + 500 : difMs - 500) / 1000);
    }
  }

  bool hayHoraReal() const { return real_; }
  size_t anotados() const {
    size_t total = 0;
    for (uint8_t i = 0; i < n_; i++) total += tramos_[i].hasta - tramos_[i].desde + 1;
    return total;
  }
  // Segundos que se suman al primer tramo (para el registro); 0 si no hay
  int32_t delta() const { return n_ ? tramos_[0].delta : 0; }

  // true si el evento lleva la hora estimada y todavía no se puede corregir: no debe salir de la placa
  bool retenido(uint32_t idEvento) const { return !real_ && tramo(idEvento) != nullptr; }

  // Suma la corrección a `e` si es uno de los anotados y ya llegó la hora real. true si cambió la fecha
  bool corregir(sp::Evento &e) const {
    if (!real_) return false;
    const Tramo *t = tramo(e.idEvento);
    if (!t || t->delta == 0 || e.fecha == 0) return false;
    e.fecha = (uint32_t)((int64_t)e.fecha + t->delta);
    return true;
  }

 private:
  struct Tramo {
    uint32_t desde, hasta;  // ids de eventos
    uint32_t baseUnix, baseMillis;
    int32_t delta;  // s que se suman (con la hora real)
  };
  const Tramo *tramo(uint32_t id) const {
    for (uint8_t i = 0; i < n_; i++)
      if (id >= tramos_[i].desde && id <= tramos_[i].hasta) return &tramos_[i];
    return nullptr;
  }
  Tramo tramos_[TRAMOS];
  uint8_t n_ = 0;
  bool real_ = false;
};
