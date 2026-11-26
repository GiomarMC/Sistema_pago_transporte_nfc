// Reglas de intercambio entre buses por ESP-NOW (M8-03, contrato C1 5.10 y plan M8-03).
//
// Cuando dos buses se ven (balizas), deciden qué darse. Todo lo que pasa de uno a otro son paquetes C1 que ya llevan
// su firma, o PEDIDOs sin firmar que solo piden; un bus intermedio no puede cambiar nada sin que se note:
//
//   1. LISTAS. Si el vecino anuncia una version_listas mayor que la mía, le mando PEDIDO(LISTAS, mi versión). Él
//      responde con sus LISTAS guardados (/firmados) encadenados desde esa versión, y yo los aplico como si vinieran
//      del servidor (SincronizadorV2::entregar verifica la firma ECDSA; una alterada se rechaza).
//   2. EVENTOS. Si yo no tengo canal con el servidor y el vecino acepta paquetes ajenos y tiene canal (bit0) o llevo
//      más de 10 min sin canal (bit1), le entrego mis eventos sin confirmar en paquetes EVENTOS firmados con mi
//      clave (hasta 10 por encuentro). Él los guarda (/ajenos) y los sube cuando puede.
//   3. ACUSES. Quien subió eventos de otro bus recibe del servidor los ACUSE para él y los guarda. Cuando le llegue
//      un PEDIDO(ACUSES), se los entrega; el ACUSE sigue firmado por el servidor. Yo los pido cuando entregué
//      eventos que siguen sin confirmar.
//
//   4. HORA. Un bus que solo tiene la hora estimada (y por eso retiene sus eventos: ver CorreccionHora) le manda
//      PEDIDO(HORA) a un vecino con canal. El vecino manda un HOLA a su servidor y le reenvía el ACUSE que le llega
//      como respuesta: va firmado por el servidor y trae su hora; quien pidió la toma (SincronizadorV2::
//      esperarHoraDeVecino) y corrige las fechas de sus eventos antes de entregarlos.
//
// Es lógica pura (sin radio ni Arduino): la radio es RadioVecinos (VecinosEspNow la implementa; las pruebas usan una
// falsa), y lo que sabe del bus lo pregunta a SincronizadorV2 y AlmacenSync.
//
// Diferencias con el texto del plan, a propósito: (a) los eventos solo se entregan a un vecino que acepta ajenos
// (bit1) aunque tenga canal, porque si no no los puede guardar; (b) un LISTAS reenviado vale aunque vaya dirigido a
// otro validador (contenido global, firma sobre el paquete entero).
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <ProtocoloSP.h>
#include <Fragmentos.h>
#include <TablaVecinos.h>

#include "sincronizador_v2.h"

class RadioVecinos {
 public:
  enum class Envio : uint8_t { ACEPTADO, OCUPADO, RECHAZADO };
  virtual ~RadioVecinos() = default;
  virtual size_t cantidad() const = 0;
  virtual const Vecino &vecino(size_t i) const = 0;  // i < cantidad()
  virtual bool enviando() const = 0;
  // Mensajes cuya transferencia se abandonó (un fragmento no llegó tras los reintentos). Que enviarMensaje() diga
  // ACEPTADO solo significa que empezó: cuando enviando() deja de ser true, si este número subió, no llegó.
  virtual uint32_t abortados() const { return 0; }
  // Un paquete C1 completo a un vecino. OCUPADO: reintentar más tarde; RECHAZADO: no se puede (ya no es vecino,
  // límite del encuentro...), el trabajo se abandona.
  virtual Envio enviarMensaje(const uint8_t mac[6], const uint8_t *mensaje, size_t largo) = 0;
  virtual Envio enviarPedido(const uint8_t mac[6], const sp::Pedido &pedido) = 0;
  virtual size_t recibirMensaje(uint8_t *buf, size_t max, uint8_t mac[6]) = 0;  // 0 si no hay
  virtual bool recibirPedido(uint8_t mac[6], sp::Pedido &pedido) = 0;
  virtual void publicar(const sp::Baliza &baliza) = 0;  // lo que este bus anuncia
};

class IntercambioVecinos {
 public:
  static constexpr uint32_t SIN_CANAL_MS = 10 * 60 * 1000;   // desde cuándo vale entregar a un vecino sin canal propio
  static constexpr uint32_t REPETIR_PEDIDO_MS = 10000;       // no repetir el mismo PEDIDO al mismo vecino antes
  static constexpr uint32_t ENTREGA_VIGENTE_MS = 10 * 60 * 1000;  // lo entregado se da por perdido si no llega acuse
  static constexpr uint32_t REINTENTO_EVENTOS_MS = 5000;  // tras un EVENTOS que la radio abortó, se vuelve a ofrecer
  static constexpr uint32_t VENTANA_HORA_MS = 10000;  // lo que se espera el ACUSE fresco tras un PEDIDO(HORA)
  static constexpr uint8_t PAQUETES_EVENTOS = 10;            // por encuentro
  static constexpr uint8_t CADENA_LISTAS = 10;               // LISTAS encadenados por PEDIDO
  static constexpr size_t MAX_AJENOS_ACEPTADOS = 480;        // de 500: más allá se anuncia que no se aceptan
  static constexpr size_t TRABAJOS = 6;
  static constexpr size_t VECINOS_RECORDADOS = 8;

  struct Cuenta {
    uint32_t pedidosListas = 0, pedidosAcuses = 0, pedidosAtendidos = 0;
    uint32_t eventosEntregados = 0, listasEntregadas = 0, acusesEntregados = 0, recibidos = 0;
    uint32_t eventosAbortados = 0;  // EVENTOS que empezaron a salir y la radio abandonó (se reintentan)
    uint32_t pedidosHora = 0, horasEntregadas = 0;
  };

  IntercambioVecinos(RadioVecinos &radio, SincronizadorV2 &sinc, AlmacenSync &almacen, uint16_t validador)
      : radio_(radio), sinc_(sinc), almacen_(almacen), validador_(validador) {}

  // En cada vuelta de loop() con el lector libre. `tengoCanal`: hay un canal de subida listo ahora mismo.
  // `necesitoHora`: este bus solo tiene la hora estimada; se la pide a un vecino con canal.
  void atender(uint32_t ahoraMs, bool tengoCanal, bool necesitoHora = false);
  const Cuenta &cuenta() const { return cuenta_; }
  size_t trabajosPendientes() const { return nTrabajos_; }

 private:
  enum class Tipo : uint8_t { LISTAS, ACUSE, EVENTOS, HORA };
  struct Trabajo {
    Tipo tipo;
    uint8_t mac[6];
    uint16_t validador;  // ACUSE: de quién
    uint32_t cursor;     // LISTAS: versión desde la que sigue la cadena; EVENTOS: último id ya entregado;
                         // HORA: millis() del PEDIDO (vale un ACUSE que llegue después)
    uint8_t restantes;
  };
  struct Recuerdo {  // qué se le ha dado y pedido a un vecino (por MAC)
    bool usado = false;
    uint8_t mac[6] = {};
    uint32_t pedidoListasMs = 0, pedidoAcusesMs = 0, pedidoHoraMs = 0, entregaMs = 0;
    uint32_t entregadoHasta = 0;  // último evento mío entregado
    uint8_t paquetes = 0;         // EVENTOS entregados (o intentados) en esta racha
    uint32_t reintentoMs = 0;     // tras un aborto, no se ofrecen eventos antes (0: sin espera)
    bool pedidoListas = false, pedidoAcuses = false, pedidoHora = false;
  };

  Recuerdo &recuerdo(const uint8_t mac[6], uint32_t ahoraMs);
  void publicarBaliza(uint32_t ahoraMs, bool tengoCanal);
  void recibir(uint32_t ahoraMs);
  void revisarEntrega(uint32_t ahoraMs);
  void decidir(uint32_t ahoraMs, bool tengoCanal, bool necesitoHora);
  void ejecutarTrabajo(uint32_t ahoraMs);
  bool encolar(const Trabajo &t);
  bool hayTrabajo(Tipo tipo, const uint8_t mac[6]) const;
  void quitarTrabajo(size_t i);

  RadioVecinos &radio_;
  SincronizadorV2 &sinc_;
  AlmacenSync &almacen_;
  uint16_t validador_;
  Trabajo trabajos_[TRABAJOS];
  size_t nTrabajos_ = 0;
  Recuerdo recuerdos_[VECINOS_RECORDADOS];
  size_t siguienteRecuerdo_ = 0;
  uint32_t sinCanalDesdeMs_ = 0;
  bool habiaCanal_ = true, hayBaliza_ = false, tengoCanal_ = false;
  sp::Baliza ultimaBaliza_;
  uint32_t ultimaBalizaMs_ = 0;
  Cuenta cuenta_;
  struct EnVuelo {  // el último EVENTOS que empezó a salir, hasta saber si llegó
    bool activo = false;
    uint8_t mac[6] = {};
    uint32_t entregadoAntes = 0, abortadosAntes = 0;
  } enVuelo_;
  uint8_t buf_[sp::TAM_MAXIMO], rx_[sp::TAM_MAXIMO];
};
