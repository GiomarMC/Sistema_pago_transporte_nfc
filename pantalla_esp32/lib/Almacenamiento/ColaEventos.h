// Cola de eventos del validador en /eventos.bin (M3-01).
//
// Registros de 64 B (la forma de C1 5.3, la misma que va en los paquetes EVENTOS), con ids
// consecutivos, que solo se agregan al final. Un viaje se da por cobrado cuando agregar()
// devuelve true, y para entonces el registro ya está vaciado (flush) a la microSD: un corte de
// energía después no lo pierde.
//
// Al arrancar (iniciar()):
//   - un /eventos.tmp que sobra es de una compactación interrumpida: si /eventos.bin sigue
//     existiendo se tira (el original está entero); si no, el original ya se había borrado y
//     el temporal estaba completo, así que pasa a ser /eventos.bin;
//   - un registro incompleto al final (corte a mitad de una escritura) se quita y se avisa.
//
// Compactación: cuando más de la mitad de los registros ya tienen acuse se reescribe el
// archivo sin ellos, por un archivo temporal que luego se renombra.
//
// Los ids siguen sin saltos aunque el archivo se vacíe del todo: el siguiente id sale del
// último registro y de EstadoPersistente (que se actualiza antes de compactar).
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <ProtocoloSP.h>

#include "Archivos.h"
#include "EstadoPersistente.h"

class ColaEventos {
 public:
  using Incidencia = void (*)(const char *texto);
  static constexpr const char *ARCHIVO = "/eventos.bin", *TEMPORAL = "/eventos.tmp";

  ColaEventos(SistemaArchivos &fs, EstadoPersistente &estado, Incidencia incidencia = nullptr)
      : fs_(fs), estado_(estado), incidencia_(incidencia) {}

  // Recupera la cola de lo que haya en disco. false si no se pudo leer el archivo.
  bool iniciar();

  // Guarda un evento nuevo con el siguiente id (se escribe en e.idEvento). Solo devuelve
  // true cuando ya está en la microSD vaciado.
  bool agregar(sp::Evento &e);

  uint32_t ultimoId() const { return ultimoId_; }
  uint32_t siguienteId() const { return ultimoId_ + 1; }
  // Los eventos que el servidor aún no ha confirmado
  uint32_t pendientes() const {
    const uint32_t acusado = estado_.estado().hastaAcusado;
    return ultimoId_ > acusado ? ultimoId_ - acusado : 0;
  }
  uint32_t ultimoAcusado() const { return estado_.estado().hastaAcusado; }

  // Copia hasta `max` eventos con id MAYOR que `desdeId`, en orden. Devuelve cuántos.
  size_t leerDesde(uint32_t desdeId, sp::Evento *salida, size_t max);

  // El servidor confirmó hasta este id: se guarda y, si ya sobra la mitad, se compacta. Con compactarAhora = false
  // solo se guarda el acuse (una escritura del estado) y la compactación queda pendiente (compactacionPendiente()):
  // se hace luego, en dos pasos, con compactarPaso(). Compactar todo junto cuesta ~2 s en la microSD real.
  bool acusarHasta(uint32_t id, bool compactarAhora = true);
  bool compactar();
  bool compactacionPendiente() const { return compactarPendiente_; }
  // Un paso de la compactación pendiente (una operación sobre la microSD): primero se guarda siguiente_id, luego se
  // reescribe el archivo sin los registros confirmados. Llamar mientras compactacionPendiente(). false si falló.
  bool compactarPaso();

  size_t registros() const { return cantidad_; }
  uint32_t compactaciones() const { return compactaciones_; }

 private:
  bool compactarAhora();
  bool alVaciar() const;      // la compactación quitaría todos los registros
  bool sobraLaMitad() const;  // los confirmados ya son más de la mitad de los registros del archivo
  bool reescribir(size_t desdeIndice, size_t hastaIndice);  // conserva los registros [desde, hasta)
  bool leerRegistro(size_t indice, sp::Evento &e);
  void avisar(const char *texto) const {
    if (incidencia_) incidencia_(texto);
  }

  SistemaArchivos &fs_;
  EstadoPersistente &estado_;
  Incidencia incidencia_;
  uint32_t primerId_ = 1, ultimoId_ = 0;  // ids del primer y último registro del archivo
  size_t cantidad_ = 0;
  uint32_t compactaciones_ = 0;
  bool compactarPendiente_ = false;
};
