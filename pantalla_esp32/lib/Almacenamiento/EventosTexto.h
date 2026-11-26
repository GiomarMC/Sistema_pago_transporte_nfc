// Eventos binarios (C1 5.3, sp::Evento) <-> las líneas JSON del sincronizador v1 y de /eventos.txt (M3-01).
//
// Mientras el validador siga hablando JSON con el servidor (sync v1), los eventos se guardan en la cola binaria
// (ColaEventos) y se convierten a JSON al subirlos. Y los /eventos.txt de versiones anteriores se leen con
// eventoDeJson() para pasarlos a la cola. Sin Arduino ni String: se prueba en el ordenador.
//
// El formato de la línea no cambia respecto al de siempre:
//   {"id":N,"tipo":"viaje","fecha":"AAAA-MM-DDThh:mm:ss+00:00","tarjeta_id":T,"uid":"HEX","monto":M,
//    "saldo_final":S,"operacion":O,"contador":C,"recarga_hasta":R|null,"detalle":null}
// Un evento que no es un viaje lleva monto, saldo_final, operacion, contador y recarga_hasta en null y un detalle.
//
// El detalle de C1 son 20 caracteres ASCII: lo más largo se corta y lo que no es ASCII pasa a '?'.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <ProtocoloSP.h>

namespace eventos_texto {

// Fecha unix (s) <-> "AAAA-MM-DDThh:mm:ss+00:00" (UTC)
void isoDeUnix(uint32_t unix, char salida[26]);
bool unixDeIso(const char *iso, uint32_t &unix);

// La línea JSON del evento, terminada en '\0'. Devuelve su largo, o 0 si no cabe en `capacidad`.
size_t aJson(const sp::Evento &e, char *salida, size_t capacidad);
// Un evento de una línea JSON como las de arriba. false si no se entiende (línea cortada, tipo desconocido...).
bool deJson(const char *linea, sp::Evento &e);

// Detalle de C1 desde un texto cualquiera: ASCII, 20 caracteres como mucho
void ponerDetalle(sp::Evento &e, const char *texto);
// Los bytes de un UID escrito en hexadecimal ("04A1B2C3"). false si no es hexadecimal o no cabe.
bool uidDeHex(const char *hex, sp::Evento &e);

}  // namespace eventos_texto
