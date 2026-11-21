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
// POR PASOS: aplicar un LISTAS cuesta 1,7-2,6 s en la microSD real si se hace de una vez (medido), y un cobro no
// puede esperar tanto. Por eso el trabajo se parte en pasos (empezarListas()/empezarTarifas() y luego avanzar()
// hasta que deje de devolver EN_CURSO): cada paso hace una sola operación (mezclar un bloque de la lista negra,
// aplicar las recargas, guardar la versión...), que cuesta como mucho unos cientos de ms. Entre paso y paso quien
// llama puede atender una tarjeta. listas() y tarifas() hacen todos los pasos seguidos (para las pruebas y para
// quien no tenga prisa). Solo hay un trabajo a la vez: mientras dura, empezar otro devuelve OCUPADO.
//
// Mientras un trabajo está a medias, la lista negra vieja sigue respondiendo (ListaNegra) y la versión guardada es
// la de antes: un corte de energía en cualquier punto deja el estado anterior y el servidor repite el paquete.
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
    EN_CURSO,   // (solo por pasos) queda trabajo: volver a llamar a avanzar()
    OCUPADO,    // (solo por pasos) hay otro trabajo a medias: no se empezó este
  };
  // Lo que procesa la mezcla de la lista negra en cada paso: un sector (128 identificadores)
  static constexpr size_t IDS_POR_PASO = ListaNegra::BLOQUE;

  AplicadorListas(EstadoPersistente &estado, ListaNegra &listaNegra, Recargas &recargas, TarifasGuardadas &tarifas,
                  PaquetesFirmados &firmados)
      : estado_(estado), listaNegra_(listaNegra), recargas_(recargas), tarifas_(tarifas), firmados_(firmados) {}

  ~AplicadorListas();

  // `paquete` y `largo`: el LISTAS tal como llegó (con su firma), para guardarlo en /firmados; puede ser nullptr
  Resultado listas(const sp::Listas &listas, const uint8_t *paquete, size_t largo);
  // TARIFAS es siempre la lista completa. Una versión anterior a la que se tiene se descarta (REPETIDA): sería un
  // paquete viejo reenviado por otro bus.
  Resultado tarifas(const sp::Tarifas &tarifas);

  // Por pasos. empezar...() comprueba las reglas y copia lo que necesita (no guarda referencias a lo que se le
  // pasa); devuelve EN_CURSO si hay trabajo, o el resultado final (REPETIDA, SALTO, INVALIDA, FALLO) si no lo hay.
  Resultado empezarListas(const sp::Listas &listas, const uint8_t *paquete, size_t largo);
  Resultado empezarTarifas(const sp::Tarifas &tarifas);
  // Un paso del trabajo en curso. EN_CURSO mientras quede; luego APLICADA, URGENTE o FALLO (y ya no hay trabajo).
  Resultado avanzar();
  bool ocupado() const { return trabajo_ != nullptr; }
  // Qué hará el siguiente avanzar() (para el diagnóstico); "" si no hay trabajo
  const char *fase() const;

 private:
  struct Trabajo;
  Resultado terminar(Resultado r);
  Resultado pasoListas();
  Resultado pasoTarifas();

  EstadoPersistente &estado_;
  ListaNegra &listaNegra_;
  Recargas &recargas_;
  TarifasGuardadas &tarifas_;
  PaquetesFirmados &firmados_;
  Trabajo *trabajo_ = nullptr;
};
