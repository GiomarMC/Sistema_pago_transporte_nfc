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
//   - Saca un paquete del canal por vuelta. Con un verificador (ConfigSyncV2), la firma ECDSA de los del servidor
//     (~67 ms) se comprueba en otra tarea y el paquete se procesa en una vuelta siguiente; mientras, no se saca otro
//     (#129: loop() < 60 ms).
//
// OJO con la memoria: el objeto lleva dentro sus búferes de paquete (unos 10 KB) y la pila de loop() en la ESP32 es de
// 8 KB: se crea como variable global o en el montón, nunca como local (medido: en la pila se reinicia por desbordamiento).
//
// Lo que necesita del almacenamiento queda detrás de AlmacenSync, porque hoy los eventos
// están en texto y pasarán a una cola binaria (M3).
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <atomic>

#include <CanalPaquetes.h>
#include <TrabajoEnFondo.h>
#include <ProtocoloSP.h>

#include "correccion_hora.h"

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
  // Eventos cobrados con la hora estimada: no salen hasta que se puedan corregir, y salen corregidos. nullptr = no se
  // retiene ni se corrige nada
  const CorreccionHora *correccion = nullptr;
  // Comprueba en otra tarea la firma ECDSA de lo que llega del servidor, por el canal o por un vecino (#129).
  // nullptr = en el acto
  TrabajoEnFondo *verificador = nullptr;
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
  // Si hay una firma comprobándose en otra tarea, espera a que acabe (escribe en este objeto)
  ~SincronizadorV2() {
    while (verificando_ && !verificacion_.terminada.load(std::memory_order_acquire)) {
    }
  }
  SincronizadorV2(const SincronizadorV2 &) = delete;
  SincronizadorV2 &operator=(const SincronizadorV2 &) = delete;

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
  // Cuenta en rechazados() lo que no se acepta. Con un verificador libre, la firma de los del servidor se comprueba
  // en otra tarea, como los del canal (se copian a bufRx_); si está ocupado, en el acto.
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
  uint32_t paquetesRecibidos() const { return recibidos_; }  // del servidor o de un vecino, válidos o no

  // --- La hora para un vecino que no la tiene (PEDIDO de hora, M8) ---
  // Manda un HOLA en la próxima vuelta con canal: el servidor contesta con un ACUSE con su hora, fresco
  void pedirHola() { holaPendiente_ = true; }
  // El último ACUSE para este validador que llegó del servidor (tal cual, firmado) y el millis() en que llegó. 0 si no
  // hay o no cabe
  size_t ultimoAcuse(uint8_t *buf, size_t max, uint32_t &recibidoMs) const;
  // Se pidió la hora a un vecino: hasta `hastaMs` se acepta la hora de un ACUSE para OTRO validador que llegue por un
  // vecino (va firmado por el servidor). Fuera de esa ventana, un ACUSE ajeno no cambia la hora: podría ser viejo
  void esperarHoraDeVecino(uint32_t hastaMs) {
    esperandoHora_ = true;
    horaHastaMs_ = hastaMs;
  }
  uint32_t horasDeVecino() const { return horasDeVecino_; }
  // El evento no puede salir todavía: lleva la hora estimada y aún no hay hora real para corregirla
  bool retenido(uint32_t idEvento) const { return cfg_.correccion && cfg_.correccion->retenido(idEvento); }
  uint32_t listasRechazadasPorFirma() const { return listasFirmaMala_; }
  uint32_t verificadasEnFondo() const { return verificadasEnFondo_; }  // firmas del servidor comprobadas en otra tarea
  bool verificando() const { return verificando_; }
  sp::Motivo ultimoRechazo() const { return ultimoRechazo_; }

 private:
  struct EnVuelo {
    uint32_t primero, ultimo;  // ids del primer y último evento del paquete
    uint32_t enviadoMs;
  };

  void alPasarAListo(CanalPaquetes *canal, uint32_t ahoraMs);
  void recibir(CanalPaquetes &canal, uint32_t ahoraMs);
  void procesar(const uint8_t *datos, size_t largo, uint32_t ahoraMs, bool relevado = false,
                sp::FirmaServidor firma = sp::FirmaServidor::COMPROBAR);
  // La firma de bufRx_ que se comprueba en otra tarea: lo que lee y lo que deja
  struct Verificacion {
    const uint8_t *datos = nullptr;
    size_t largo = 0;
    const uint8_t *clave = nullptr;
    std::atomic<bool> valida{false}, terminada{false};
  };
  static void verificarEnFondo(void *arg);
  // Lanza la comprobación de la firma de bufRx_ (n bytes); false si no hay verificador o no se pudo lanzar
  bool lanzarVerificacion(size_t n, bool relevado);
  void recogerVerificacion(uint32_t ahoraMs);  // si terminó, procesa el paquete con su resultado
  void enviarHola(CanalPaquetes &canal, uint32_t ahoraMs);
  bool enviarEventos(CanalPaquetes &canal, uint32_t ahoraMs);
  void enviarAjenos(CanalPaquetes &canal, uint32_t ahoraMs);
  void alAcuse(const sp::Cabecera &cab, const sp::Acuse &acuse, const uint8_t *datos, size_t largo, uint32_t ahoraMs,
               bool relevado);
  void tomarHora(uint32_t horaServidor, uint32_t ahoraMs, bool relevado);
  size_t prepararEventos(uint32_t desdeId);  // eventosDesde + retenidos fuera + fechas corregidas, en tx_.eventos
  uint32_t creado() const { return cfg_.ahoraUnix ? cfg_.ahoraUnix() : 0; }

  AlmacenSync &almacen_;
  ConfigSyncV2 cfg_;
  Verificacion verificacion_;
  bool verificando_ = false;  // bufRx_ tiene un paquete cuya firma se está comprobando en otra tarea
  bool verificandoRelevado_ = false;  // y llegó por un vecino
  size_t largoVerificando_ = 0;
  uint32_t verificadasEnFondo_ = 0;
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
  static constexpr size_t TAM_ACUSE = 128;  // un ACUSE firmado ocupa 92 B
  uint8_t ultimoAcuse_[TAM_ACUSE];
  size_t largoUltimoAcuse_ = 0;
  uint32_t ultimoAcuseMs_ = 0;
  bool esperandoHora_ = false;
  uint32_t horaHastaMs_ = 0, horasDeVecino_ = 0;

  uint32_t holas_ = 0, paqueteEventos_ = 0, paquetesAjenos_ = 0, reenvios_ = 0, rechazados_ = 0, recibidos_ = 0;
  sp::Motivo ultimoRechazo_ = sp::Motivo::OK;

  // Búferes grandes dentro del objeto: mejor que la pila del loop
  sp::Paquete tx_, rx_;
  uint8_t bufTx_[sp::TAM_MAXIMO], bufRx_[sp::TAM_MAXIMO];
};
