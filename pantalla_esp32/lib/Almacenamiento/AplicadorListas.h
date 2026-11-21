// Aplicación de LISTAS y TARIFAS del servidor (M4-02, C1 5.5 y 5.6) sobre el almacenamiento binario.
//
// Las cinco reglas de LISTAS:
//   1. Versionado: cada cambio del servidor lleva una versión global creciente; el validador guarda
//      version_listas (en EstadoPersistente) y la anuncia en el HOLA.
//   2. Contigüidad: un paquete se aplica solo si version_desde == version_listas, o si es 0: entonces
//      borra sus listas y las reconstruye. Tras aplicarlo guarda version_listas = version_hasta. Uno
//      con version_desde mayor (se perdió uno antes) o menor (ya se tenía) se descarta, y el servidor
//      vuelve a mandar lo que falte al siguiente HOLA.
//   3. Foto completa: version_desde = 0 y luego paquetes encadenados (cada uno parte del hasta del
//      anterior); las versiones intermedias son mayores que 2^31, así que si la cadena se corta el
//      siguiente HOLA trae una versión "del futuro" y el servidor manda otra foto.
//   4. Urgentes: un LISTAS con urgente = 1 bloquea sus tarjetas aunque no sea contiguo y NO cambia
//      version_listas. Cuando el cambio llega en orden se aplica otra vez sin efecto. Solo se bloquea
//      (un desbloqueo urgente se ignora: desbloquear va siempre en orden), y un urgente cuya versión ya
//      se tiene (hasta <= version_listas) se ignora: reaplicarlo podría volver a bloquear una tarjeta que
//      un cambio posterior ya desbloqueó.
//   5. Firma: la comprueba el sincronizador antes de llegar aquí (ECDSA del servidor); un paquete con
//      firma inválida no se aplica y el sincronizador registra la incidencia.
//
// Orden de lo que se escribe, para que un corte de energía no deje la versión por delante de los datos:
// primero la lista negra y las recargas, y solo al final version_listas. Un corte en medio deja la
// versión anterior y el servidor repite el paquete (aplicar dos veces da lo mismo).
//
// Los urgentes que una lista ya recoge, o que un cambio en orden desbloquea, se quitan de /urgentes.bin
// uno a uno (ListaNegra::quitarUrgente). Los LISTAS aplicados se guardan en /firmados para pasárselos a
// otros buses (M8).
//
// OJO: aplicar bloquea (síncrono): cada paquete cuesta lo que cueste cambiar archivos en la microSD
// (la lista negra cierra con renombrados de decenas a cientos de ms).
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <ProtocoloSP.h>

#include "EstadoPersistente.h"
#include "ListaNegra.h"
#include "PaquetesGuardados.h"
#include "Recargas.h"
#include "TarifasGuardadas.h"

class AplicadorListas {
 public:
  // Las versiones intermedias de una foto completa son todas mayores o iguales que esta
  static constexpr uint32_t VERSION_INTERMEDIA = 0x80000000u;

  enum class Resultado : uint8_t {
    APLICADA,   // los cambios están aplicados y la versión guardada
    URGENTE,    // bloqueos urgentes aplicados (la versión no cambia)
    REPETIDA,   // ya se tenía (versión menor o igual): no se hace nada
    SALTO,      // falta un paquete anterior: no se aplica
    INVALIDA,   // no es coherente (una versión normal que retrocede)
    FALLO,      // no se pudo escribir: nada cambió de la versión, se repetirá
  };

  AplicadorListas(EstadoPersistente &estado, ListaNegra &listaNegra, Recargas &recargas, TarifasGuardadas &tarifas,
                  PaquetesFirmados &firmados)
      : estado_(estado), listaNegra_(listaNegra), recargas_(recargas), tarifas_(tarifas), firmados_(firmados) {}

  // `paquete` y `largo`: el LISTAS tal como llegó (con su firma), para guardarlo en /firmados; puede ser nullptr
  Resultado listas(const sp::Listas &listas, const uint8_t *paquete, size_t largo);
  // TARIFAS es siempre la lista completa. Una versión anterior a la que se tiene se descarta (REPETIDA): sería un
  // paquete viejo reenviado por otro bus.
  Resultado tarifas(const sp::Tarifas &tarifas);

 private:
  Resultado urgente(const sp::Listas &listas, const uint8_t *paquete, size_t largo);

  EstadoPersistente &estado_;
  ListaNegra &listaNegra_;
  Recargas &recargas_;
  TarifasGuardadas &tarifas_;
  PaquetesFirmados &firmados_;
};
