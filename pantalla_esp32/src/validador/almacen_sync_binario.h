// Adaptador entre SincronizadorV2 y el almacenamiento binario (M4/M15): AlmacenSync sobre ColaEventos,
// EstadoPersistente, PaquetesAjenos y AplicadorListas.
//
// Lo que el sincronizador hace desde atender() NO puede tardar: corre en loop(), entre dos lecturas de tarjeta.
// En la microSD real un guardado cuesta 0,4-0,9 s, aplicar un LISTAS 1,7-2,6 s y borrar un archivo 0,1-0,45 s, así
// que este adaptador solo hace en esas llamadas lo que sale de la RAM (o de una lectura) y deja lo demás para
// paso(), que loop() llama solo con el lector libre (sin tarjeta delante) y que hace UNA operación por llamada:
//
//   acusarHasta(id)        el acuse se recuerda en RAM (ultimoAcusado() ya lo da) y se guarda en paso()
//                          (EstadoPersistente); compactar la cola, si sobra la mitad, son dos pasos más
//   ajenosAcusados(o, id)  se recuerda en RAM; los paquetes que cubre se borran de uno en uno en paso()
//   aplicarListas/Tarifas  el paquete (ya verificado) se copia a una cola de COLA_TRABAJOS y paso() lo aplica por
//                          pasos (AplicadorListas::avanzar()); si la cola está llena se descarta y el servidor lo
//                          repite en el siguiente HOLA (versión sin cambiar). Los urgentes pasan por delante.
//   incidencia(detalle)    se guarda como un evento INCIDENCIA en paso() (como mucho una pendiente)
//
// Cada paso, además, deja la acción para la siguiente vuelta si el lector se ocupó entre medias: paso() devuelve
// true si hizo algo, para que loop() sepa que puede haber más.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <AplicadorListas.h>
#include <ColaEventos.h>
#include <EstadoPersistente.h>
#include <PaquetesGuardados.h>
#include <ProtocoloSP.h>

#include "sincronizador_v2.h"

class AlmacenSyncBinario : public AlmacenSync {
 public:
  static constexpr size_t COLA_TRABAJOS = 3;  // LISTAS/TARIFAS esperando (cada uno ~4 KB en RAM)
  static constexpr size_t COLA_AJENOS = 4;    // EVENTOS de vecinos esperando a escribirse (~2 KB cada uno)
  static constexpr size_t ACUSES_AJENOS = 8;  // ACUSE guardados para entregar a otros buses (en RAM)

  struct Cuenta {  // para el diagnóstico
    uint32_t listasAplicadas = 0, tarifasAplicadas = 0, descartados = 0, fallos = 0;
    uint32_t ajenosBorrados = 0, incidencias = 0;
  };

  // `ahoraUnix`: la hora para el campo fecha de las incidencias (nullptr = 0)
  // `firmados`: los LISTAS guardados, para entregárselos a otros buses (nullptr: no se entregan)
  AlmacenSyncBinario(ColaEventos &cola, EstadoPersistente &estado, PaquetesAjenos &ajenos, AplicadorListas &aplicador,
                     uint32_t (*ahoraUnix)() = nullptr, PaquetesFirmados *firmados = nullptr)
      : cola_(cola), estado_(estado), ajenos_(ajenos), aplicador_(aplicador), ahoraUnix_(ahoraUnix), firmados_(firmados) {}
  ~AlmacenSyncBinario() override;

  // Una operación pendiente, si hay y `lectorLibre`. false: no había nada que hacer (o el lector estaba ocupado).
  // Orden: un trabajo ya empezado, bloqueos urgentes, incidencia, acuse, borrar un ajeno, LISTAS/TARIFAS.
  bool paso(bool lectorLibre);
  // true si queda algo por hacer en paso()
  bool trabajoPendiente() const;
  const Cuenta &cuenta() const { return cuenta_; }
  size_t trabajosEnCola() const { return nTrabajos_; }

  // --- AlmacenSync ---
  uint32_t ultimoEvento() const override { return cola_.ultimoId(); }
  uint32_t ultimoAcusado() const override;
  size_t eventosDesde(uint32_t desdeId, sp::Evento *salida, size_t max) override { return cola_.leerDesde(desdeId, salida, max); }
  void acusarHasta(uint32_t hastaId) override;
  uint32_t versionListas() const override { return estado_.estado().versionListas; }
  uint32_t versionTarifas() const override { return estado_.estado().versionTarifas; }
  uint32_t siguienteSecuencia() override { return estado_.siguienteSecuencia(); }
  size_t ajenos() const override { return ajenos_.cantidad(); }
  size_t leerAjeno(size_t indice, uint8_t *buf, size_t max) override { return ajenos_.leerPosicion(indice, buf, max); }
  void ajenosAcusados(uint16_t origen, uint32_t hastaEvento) override { ajenos_.acusar(origen, hastaEvento, 0); }
  void aplicarListas(const sp::Listas &listas, const uint8_t *paquete, size_t largo) override;
  void aplicarTarifas(const sp::Tarifas &tarifas) override;
  void incidencia(const char *detalle) override;
  bool guardarAjeno(const uint8_t *paquete, size_t largo) override;
  void guardarAcuseAjeno(const uint8_t *paquete, size_t largo) override;
  size_t acusePara(uint16_t validador, uint8_t *buf, size_t max) override;
  size_t listasDesde(uint32_t desde, uint8_t *buf, size_t max, uint32_t &hasta) override;
  // Paquetes de otros buses esperando a escribirse en la microSD (paso() escribe uno por llamada)
  size_t ajenosEnCola() const { return nAjenosCola_; }

 private:
  struct Trabajo;  // una entrada de la cola: un LISTAS (con su paquete) o unas TARIFAS

  bool hayUrgente() const;
  Trabajo *siguienteTrabajo();
  void quitar(Trabajo *t);
  bool compactarCola() const;
  bool darPaso();  // un paso del trabajo del aplicador
  void contar(AplicadorListas::Resultado r);

  ColaEventos &cola_;
  EstadoPersistente &estado_;
  PaquetesAjenos &ajenos_;
  AplicadorListas &aplicador_;
  uint32_t (*ahoraUnix_)();
  PaquetesFirmados *firmados_;

  uint8_t fallosCompactar_ = 0;
  uint32_t acusePendiente_ = 0;  // lo que el servidor confirmó y aún no está guardado
  bool hayIncidencia_ = false;
  char incidencia_[sizeof(sp::Evento::detalle)] = {0};
  Trabajo *cola_t_[COLA_TRABAJOS] = {};
  size_t nTrabajos_ = 0;
  bool enCursoTarifas_ = false;  // el trabajo del aplicador es de TARIFAS (para contarlo al terminar)
  struct Ajeno;  // un EVENTOS de un vecino
  struct AcuseGuardado {
    uint16_t validador = 0;
    uint8_t largo = 0;
    uint8_t datos[sp::TAM_CABECERA + 8 + sp::FIRMA_ECDSA] = {};
  };
  Ajeno *colaAjenos_[COLA_AJENOS] = {};
  size_t nAjenosCola_ = 0;
  AcuseGuardado acuses_[ACUSES_AJENOS];
  size_t siguienteAcuse_ = 0;  // dónde se escribe el próximo si no hay uno de ese validador
  Cuenta cuenta_;
};
