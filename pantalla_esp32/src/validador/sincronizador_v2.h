// Sincronizador v2 (M4-01): intercambio de paquetes C1 con el servidor por cualquier canal.
//
// Sustituye al sincronizador v1 (JSON sobre HTTP). Solo conoce CanalPaquetes, así que sirve
// igual para MQTT sobre WiFi, para BLE o para canales falsos en las pruebas.
//
// Qué hace, en cada vuelta de loop() (C1 5.x):
//   - Al pasar el canal a "listo" manda un HOLA, y otro cada 5 minutos.
//   - Manda los eventos que el servidor no ha confirmado, en paquetes EVENTOS de hasta
//     30, con como máximo 3 paquetes sin acuse a la vez. Un viaje nuevo sale en la misma
//     vuelta en que se guarda, sin esperar a juntar más.
//   - Lo que no recibe acuse en 30 s se reenvía.
//   - Reenvía tal cual (sin volver a firmar) los paquetes de otros buses que guarda.
//   - Verifica y procesa lo que llega: ACUSE (confirma eventos y libera paquetes ajenos),
//     LISTAS y TARIFAS (se entregan al almacén; aplicarlas es M4-02).
//
// Lo que necesita del almacenamiento queda detrás de AlmacenSync, porque hoy los eventos
// están en texto y pasarán a una cola binaria (M3).
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <CanalPaquetes.h>
#include <ProtocoloSP.h>

class AlmacenSync {
 public:
  virtual ~AlmacenSync() = default;

  // --- Eventos propios (ids crecientes, uno por viaje) ---
  virtual uint32_t ultimoEvento() const = 0;   // mayor id guardado; 0 si no hay
  virtual uint32_t ultimoAcusado() const = 0;  // mayor id que el servidor ya confirmó
  // Copia en `salida` hasta `max` eventos con id MAYOR que `desdeId`, en orden. Devuelve cuántos.
  virtual size_t eventosDesde(uint32_t desdeId, sp::Evento *salida, size_t max) = 0;
  virtual void acusarHasta(uint32_t hastaId) = 0;  // el servidor confirmó hasta ese id

  // --- Estado que se anuncia en el HOLA ---
  virtual uint32_t versionListas() const = 0;
  virtual uint32_t versionTarifas() const = 0;
  // Secuencia de los paquetes de este validador: creciente y sin repetirse nunca, ni tras
  // un reinicio (C1 5.1), por eso la guarda el almacén.
  virtual uint32_t siguienteSecuencia() = 0;

  // --- Paquetes de otros buses que se llevan al servidor ---
  virtual size_t ajenos() const = 0;
  // Bytes del paquete tal como llegó, sin tocar. 0 si no existe o no cabe en `max`.
  virtual size_t leerAjeno(size_t indice, uint8_t *buf, size_t max) = 0;
  // El servidor confirmó los eventos de `origen` hasta `hastaEvento`: esos paquetes sobran.
  virtual void ajenosAcusados(uint16_t origen, uint32_t hastaEvento) = 0;

  // --- Respuestas del servidor (aplicarlas es M4-02 y M4-03) ---
  virtual void aplicarListas(const sp::Listas &listas) { (void)listas; }
  virtual void aplicarTarifas(const sp::Tarifas &tarifas) { (void)tarifas; }
  virtual void horaDelServidor(uint32_t unix, uint32_t ahoraMs) { (void)unix; (void)ahoraMs; }
};

struct ConfigSyncV2 {
  uint16_t validador = 0;                          // n.º de este validador (1-899)
  const uint8_t *claveHmac = nullptr;              // 32 B: firma sus paquetes
  const uint8_t *clavePublicaServidor = nullptr;   // 65 B: verifica los del servidor
  uint32_t versionFirmware = 0;                    // AAAAMMDD
  uint32_t (*ahoraUnix)() = nullptr;               // hora para el campo "creado"; nullptr = 0
};

class SincronizadorV2 {
 public:
  static constexpr size_t EVENTOS_POR_PAQUETE = sp::MAX_EVENTOS;  // 30
  static constexpr uint8_t PAQUETES_SIN_ACUSE = 3;
  static constexpr uint32_t REENVIO_MS = 30000;   // sin acuse en este tiempo, se reenvía
  static constexpr uint32_t HOLA_MS = 300000;     // un HOLA cada 5 minutos

  SincronizadorV2(AlmacenSync &almacen, const ConfigSyncV2 &config) : almacen_(almacen), cfg_(config) {}

  // Señal y consumo de datos que se anuncian en el HOLA (los da el gestor de enlaces)
  void telemetria(int8_t senal, uint32_t bytesTx, uint32_t bytesRx) {
    senal_ = senal;
    bytesTx_ = bytesTx;
    bytesRx_ = bytesRx;
  }

  // En cada loop(). `canal` es el de subida que eligió el gestor, o nullptr si no hay.
  void atender(CanalPaquetes *canal, uint32_t ahoraMs);

  // Eventos propios que el servidor aún no ha confirmado
  uint32_t pendientes() const {
    const uint32_t ultimo = almacen_.ultimoEvento(), acusado = almacen_.ultimoAcusado();
    return ultimo > acusado ? ultimo - acusado : 0;
  }
  uint8_t paquetesSinAcuse() const { return nVuelo_; }

  // Contadores para el diagnóstico
  uint32_t holasEnviados() const { return holas_; }
  uint32_t paquetesEventos() const { return paqueteEventos_; }
  uint32_t paquetesAjenos() const { return paquetesAjenos_; }
  uint32_t reenvios() const { return reenvios_; }
  uint32_t rechazados() const { return rechazados_; }
  sp::Motivo ultimoRechazo() const { return ultimoRechazo_; }

 private:
  struct EnVuelo {
    uint32_t primero, ultimo;  // ids del primer y último evento del paquete
    uint32_t enviadoMs;
  };

  void alPasarAListo(CanalPaquetes *canal, uint32_t ahoraMs);
  void recibir(CanalPaquetes &canal, uint32_t ahoraMs);
  void procesar(const uint8_t *datos, size_t largo, uint32_t ahoraMs);
  void enviarHola(CanalPaquetes &canal, uint32_t ahoraMs);
  bool enviarEventos(CanalPaquetes &canal, uint32_t ahoraMs);
  void enviarAjenos(CanalPaquetes &canal, uint32_t ahoraMs);
  void alAcuse(const sp::Cabecera &cab, const sp::Acuse &acuse, uint32_t ahoraMs);
  uint32_t creado() const { return cfg_.ahoraUnix ? cfg_.ahoraUnix() : 0; }

  AlmacenSync &almacen_;
  ConfigSyncV2 cfg_;
  int8_t senal_ = 0;
  uint32_t bytesTx_ = 0, bytesRx_ = 0;

  CanalPaquetes *canalActual_ = nullptr;  // el canal listo del que se parte; cambiar de canal es empezar de nuevo
  bool canalListo_ = false, holaPendiente_ = false;
  uint32_t ultimoHolaMs_ = 0;

  EnVuelo vuelo_[PAQUETES_SIN_ACUSE] = {};
  uint8_t nVuelo_ = 0;
  uint32_t siguienteId_ = 1;  // primer evento que aún no se ha enviado en este intento

  size_t ajenoSiguiente_ = 0;  // por cuál paquete ajeno va la ronda
  uint32_t rondaAjenosMs_ = 0;
  bool rondaAjenosActiva_ = false, hayRondaPrevia_ = false;

  uint32_t holas_ = 0, paqueteEventos_ = 0, paquetesAjenos_ = 0, reenvios_ = 0, rechazados_ = 0;
  sp::Motivo ultimoRechazo_ = sp::Motivo::OK;

  // Búferes grandes dentro del objeto: mejor que la pila del loop
  sp::Paquete tx_, rx_;
  uint8_t bufTx_[sp::TAM_MAXIMO], bufRx_[sp::TAM_MAXIMO];
};
