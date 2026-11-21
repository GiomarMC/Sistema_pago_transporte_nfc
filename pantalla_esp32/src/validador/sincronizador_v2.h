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
//     LISTAS y TARIFAS (se entregan al almacén, que las aplica: AplicadorListas, M4-02). Un LISTAS
//     con firma inválida se descarta y se registra como incidencia "lista firma invalida".
//   - Toma la hora del servidor de cada ACUSE válido (M4-03).
//
// OJO con la memoria: el objeto lleva dentro sus búferes de paquete (unos 10 KB) y la pila de loop() en la ESP32 es de
// 8 KB: se crea como variable global o en el montón, nunca como local (medido: en la pila se reinicia por desbordamiento).
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

  // --- Respuestas del servidor (las reglas de C1 5.5 las aplica AplicadorListas, M4-02) ---
  // `paquete`: el LISTAS tal como llegó, con su firma, para guardarlo y pasárselo a otros buses
  virtual void aplicarListas(const sp::Listas &listas, const uint8_t *paquete, size_t largo) {
    (void)listas;
    (void)paquete;
    (void)largo;
  }
  virtual void aplicarTarifas(const sp::Tarifas &tarifas) { (void)tarifas; }
  // --- Intercambio con otros buses por ESP-NOW (M8-03) ---
  // Un EVENTOS de otro validador que llegó por un vecino (tal cual, sin poder verificar su firma HMAC: solo la
  // conoce el servidor). true si se aceptó para guardarlo (puede quedar en cola y escribirse después)
  virtual bool guardarAjeno(const uint8_t *paquete, size_t largo) {
    (void)paquete;
    (void)largo;
    return false;
  }
  // Un ACUSE del servidor para OTRO validador, que este bus recibió porque subió sus eventos: se guarda para
  // entregárselo cuando lo pida (PEDIDO 3)
  virtual void guardarAcuseAjeno(const uint8_t *paquete, size_t largo) {
    (void)paquete;
    (void)largo;
  }
  // El ACUSE guardado para `validador` (copiado en `buf`); 0 si no hay o no cabe
  virtual size_t acusePara(uint16_t validador, uint8_t *buf, size_t max) {
    (void)validador;
    (void)buf;
    (void)max;
    return 0;
  }
  // El LISTAS guardado que parte de la versión `desde` (copiado en `buf`), con su version_hasta; 0 si no hay
  virtual size_t listasDesde(uint32_t desde, uint8_t *buf, size_t max, uint32_t &hasta) {
    (void)desde;
    (void)buf;
    (void)max;
    (void)hasta;
    return 0;
  }
  // Algo anormal que debe quedar en el registro del validador (hasta 20 caracteres, como el detalle de un evento)
  virtual void incidencia(const char *detalle) { (void)detalle; }
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
  // Un ACUSE que llega por BLE o por otro bus (ESP-NOW) lleva más retraso (C1): el contrato
  // estima 2 s, que se suman a la hora que trae
  static constexpr uint32_t RETRASO_RELE_S = 2;
  // Una hora anterior a la ya aceptada en más de esto se descarta: la firma es válida, pero
  // pudo ser un ACUSE viejo reenviado por quien lo transportaba
  static constexpr uint32_t TOLERANCIA_HORA_S = 3;
  // Un LISTAS con firma inválida se registra como incidencia, pero como mucho una cada minuto: cualquiera
  // podría inundar el registro con paquetes falsos
  static constexpr uint32_t INCIDENCIA_MS = 60000;
  static constexpr const char *DETALLE_LISTA_FALSA = "lista firma invalida";

  SincronizadorV2(AlmacenSync &almacen, const ConfigSyncV2 &config) : almacen_(almacen), cfg_(config) {}

  // Señal y consumo de datos que se anuncian en el HOLA (los da el gestor de enlaces)
  void telemetria(int8_t senal, uint32_t bytesTx, uint32_t bytesRx) {
    senal_ = senal;
    bytesTx_ = bytesTx;
    bytesRx_ = bytesRx;
  }

  // Un paquete que llegó por otro camino que el canal de subida: de un vecino por ESP-NOW (o del celular por BLE).
  //   - EVENTOS de otro validador: se guarda para llevarlo al servidor (no se puede verificar su firma)
  //   - ACUSE, LISTAS y TARIFAS del servidor: se verifican (ECDSA) igual que si vinieran del servidor. Los
  //     LISTAS y TARIFAS valen aunque vayan dirigidos a otro validador: su contenido es el mismo para todos y la
  //     firma cubre el paquete entero. La hora de un ACUSE relevado se compensa con RETRASO_RELE_S.
  // Cuenta en rechazados() lo que no se acepta.
  void entregar(const uint8_t *datos, size_t largo, uint32_t ahoraMs);
  // Arma un paquete EVENTOS propio, firmado, con hasta 30 eventos de id mayor que `desdeId`, para entregarlo a un
  // vecino. Devuelve su largo (0 si no hay eventos o no cabe) y en `ultimoId` el id del último evento que lleva.
  size_t empaquetarEventos(uint32_t desdeId, uint8_t *buf, size_t capacidad, uint32_t &ultimoId, uint32_t ahoraMs);
  uint32_t ajenosRecibidos() const { return ajenosRecibidos_; }

  // En cada loop(). `canal` es el de subida que eligió el gestor, o nullptr si no hay.
  void atender(CanalPaquetes *canal, uint32_t ahoraMs);

  // Eventos propios que el servidor aún no ha confirmado
  uint32_t pendientes() const {
    const uint32_t ultimo = almacen_.ultimoEvento(), acusado = almacen_.ultimoAcusado();
    return ultimo > acusado ? ultimo - acusado : 0;
  }
  uint8_t paquetesSinAcuse() const { return nVuelo_; }

  // Hora del servidor (unix, s) al recibir el último ACUSE válido, 0 si aún no hubo, y el
  // millis() de ese momento. Mismo significado que en el sincronizador v1.
  uint32_t horaServidor() const { return horaServidor_; }
  uint32_t millisHora() const { return millisHora_; }
  // La hora de ahora según el servidor (0 si no hay referencia)
  uint32_t horaActual(uint32_t ahoraMs) const {
    return horaServidor_ ? horaServidor_ + (uint32_t)(ahoraMs - millisHora_) / 1000 : 0;
  }

  // Contadores para el diagnóstico
  uint32_t holasEnviados() const { return holas_; }
  uint32_t paquetesEventos() const { return paqueteEventos_; }
  uint32_t paquetesAjenos() const { return paquetesAjenos_; }
  uint32_t reenvios() const { return reenvios_; }
  uint32_t rechazados() const { return rechazados_; }
  uint32_t listasRechazadasPorFirma() const { return listasFirmaMala_; }
  sp::Motivo ultimoRechazo() const { return ultimoRechazo_; }

 private:
  struct EnVuelo {
    uint32_t primero, ultimo;  // ids del primer y último evento del paquete
    uint32_t enviadoMs;
  };

  void alPasarAListo(CanalPaquetes *canal, uint32_t ahoraMs);
  void recibir(CanalPaquetes &canal, uint32_t ahoraMs);
  void procesar(const uint8_t *datos, size_t largo, uint32_t ahoraMs, bool relevado = false);
  void enviarHola(CanalPaquetes &canal, uint32_t ahoraMs);
  bool enviarEventos(CanalPaquetes &canal, uint32_t ahoraMs);
  void enviarAjenos(CanalPaquetes &canal, uint32_t ahoraMs);
  void alAcuse(const sp::Cabecera &cab, const sp::Acuse &acuse, const uint8_t *datos, size_t largo, uint32_t ahoraMs,
               bool relevado);
  void tomarHora(uint32_t horaServidor, uint32_t ahoraMs, bool relevado);
  uint32_t creado() const { return cfg_.ahoraUnix ? cfg_.ahoraUnix() : 0; }

  AlmacenSync &almacen_;
  ConfigSyncV2 cfg_;
  int8_t senal_ = 0;
  uint32_t bytesTx_ = 0, bytesRx_ = 0;
  uint32_t listasFirmaMala_ = 0, ultimaIncidenciaMs_ = 0, ajenosRecibidos_ = 0;
  bool hayIncidencia_ = false;

  CanalPaquetes *canalActual_ = nullptr;  // el canal listo del que se parte; cambiar de canal es empezar de nuevo
  bool canalListo_ = false, holaPendiente_ = false;
  uint32_t ultimoHolaMs_ = 0;

  EnVuelo vuelo_[PAQUETES_SIN_ACUSE] = {};
  uint8_t nVuelo_ = 0;
  uint32_t siguienteId_ = 1;  // primer evento que aún no se ha enviado en este intento

  size_t ajenoSiguiente_ = 0;  // por cuál paquete ajeno va la ronda
  uint32_t rondaAjenosMs_ = 0;
  bool rondaAjenosActiva_ = false, hayRondaPrevia_ = false;

  uint32_t horaServidor_ = 0, millisHora_ = 0, horaCrudaMax_ = 0;

  uint32_t holas_ = 0, paqueteEventos_ = 0, paquetesAjenos_ = 0, reenvios_ = 0, rechazados_ = 0;
  sp::Motivo ultimoRechazo_ = sp::Motivo::OK;

  // Búferes grandes dentro del objeto: mejor que la pila del loop
  sp::Paquete tx_, rx_;
  uint8_t bufTx_[sp::TAM_MAXIMO], bufRx_[sp::TAM_MAXIMO];
};
