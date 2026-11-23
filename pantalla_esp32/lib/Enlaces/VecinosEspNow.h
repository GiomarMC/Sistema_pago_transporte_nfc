// Contrato C2: ESP-NOW entre buses (M8-01). Descubre buses cercanos con balizas.
//
// Cada bus difunde una BALIZA (C1 5.10) cada 2 s ± 500 ms en el canal de radio 1 y
// mantiene la tabla de los vecinos que oye; se olvidan tras 10 s sin balizas.
//
// Además transfiere paquetes C1 (hasta 2048 B) a un vecino concreto (M8-02): se parten en
// fragmentos de 220 B que van dirigidos a su MAC; el aviso de entrega de ESP-NOW dice si
// cada uno llegó, con hasta 3 reintentos por fragmento, y si se agotan se aborta el mensaje
// entero. Como máximo 20 mensajes por encuentro y por sentido.
//
// ESP-NOW y el WiFi comparten la radio. Con el WiFi STA conectado a un punto de acceso
// en otro canal, hay que llamar a pausar(true) (lo hace el GestorEnlaces). iniciar()
// fija el canal de la radio, así que debe llamarse con el WiFi sin conectar o ya en ese
// canal.
//
// Uso:
//   VecinosEspNow vecinos(log);
//   vecinos.publicar(baliza);     // qué anuncia este bus; se puede repetir cuando cambie
//   vecinos.iniciar(1);
//   ... en cada loop():  vecinos.atender();
//   enviar:    vecinos.enviar(mac, paquete, largo)   // devuelve al instante; sale en atender()
//   recibir:   n = vecinos.recibir(buf, sizeof(buf), macOrigen)
#pragma once

#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

#include <Fragmentos.h>

#include "EmisorFragmentos.h"
#include "TablaVecinos.h"
#include "Vecinos.h"

class VecinosEspNow final : public Vecinos {
 public:
  using Registro = void (*)(const String &);

  static constexpr uint32_t BALIZA_MS = 2000;     // periodo entre balizas
  static constexpr uint32_t VARIACION_MS = 500;   // ± al azar, para que dos buses no choquen siempre
  static constexpr size_t MAX_MENSAJE = EmisorFragmentos::MAX_MENSAJE;
  static constexpr uint8_t MENSAJES_EN_ESPERA = 2;  // recibidos completos que aguardan a recibir()

  enum class Envio : uint8_t {
    ACEPTADO,           // saldrá en las próximas vueltas de atender()
    PAUSADO,            // sin iniciar, o en pausa
    NO_ES_VECINO,       // esa MAC no está en la tabla
    OCUPADO,            // todavía se está enviando otro mensaje
    MENSAJE_INVALIDO,   // vacío o de más de 2048 B
    LIMITE_ALCANZADO,   // ya van 20 mensajes a este vecino en el encuentro
    ERROR_RADIO,        // no se pudo registrar al vecino en ESP-NOW
  };

  explicit VecinosEspNow(Registro log = nullptr) : log_(log) {}
  ~VecinosEspNow() override;

  // --- Vecinos ---------------------------------------------------------------------
  // ESP-NOW por la interfaz AP (con un AP oculto que no admite a nadie) en vez de la STA. Llamar antes de iniciar().
  // La velocidad y el protocolo de largo alcance (fijarTasas) se configuran por interfaz y cambian TODAS sus tramas:
  // en la STA impedían autenticar con el router. En la AP no tocan la conexión WiFi. Con la STA conectada, el AP se
  // queda en el canal del router (es la misma radio), así que ESP-NOW sigue al WiFi solo.
  void usarInterfazAp() { interfazAp_ = true; }
  void iniciar(uint8_t canalWifi) override;  // 0 se toma como 1
  // ESP-NOW sigue al WiFi: con `canal` (el del AP al que se conecta o se conectó) se pasa a él; con 0 vuelve al de
  // iniciar() (espnow_canal). Solo cambia la radio si el STA no está conectado (conectado, manda el canal del AP).
  void cambiarCanal(uint8_t canal);
  uint8_t canal() const { return canal_; }
  // Mientras el WiFi se conecta a un AP no se transmite nada (ni balizas, ni fragmentos, ni PEDIDOs; se sigue
  // escuchando y no se olvida a los vecinos). Es precaución: dura unos segundos y solo con un AP a la vista. Lo que sí
  // está medido es que transmitir en OTRO canal impide autenticar; en el mismo canal los rechazos medidos (motivo 2)
  // salen igual con ESP-NOW apagado
  void silenciar(bool si) { silencio_ = si; }
  void atender() override;                   // en cada loop(); no bloquea
  void pausar(bool si) override;
  size_t cantidad() const override { return tabla_.cantidad(); }

  // --- Además del contrato ---------------------------------------------------------
  // Lo que este bus anuncia (su número de validador, versiones, pendientes...)
  void publicar(const sp::Baliza &datos) { propia_ = datos; }
  const Vecino &vecino(size_t i) const { return tabla_.en(i); }  // i < cantidad()

  // Manda un paquete a un vecino. Copia el mensaje y vuelve enseguida; solo hay uno en
  // curso a la vez, el siguiente se acepta cuando enviando() deja de ser true.
  Envio enviar(const uint8_t mac[6], const uint8_t *mensaje, size_t largo);
  bool enviando() const { return emisor_.ocupado(); }
  // Un PEDIDO (C1 5.10, trama de clase 3) a un vecino: sin fragmentar, sin esperar aviso de entrega (si no llega, quien
  // pide lo repite). ACEPTADO / OCUPADO (se está enviando un mensaje o falta el aviso del pedido anterior) /
  // NO_ES_VECINO / PAUSADO / ERROR_RADIO.
  Envio enviarPedido(const uint8_t mac[6], const sp::Pedido &pedido);
  // Saca el PEDIDO más antiguo que llegó de un vecino. false si no hay.
  bool recibirPedido(uint8_t macOrigen[6], sp::Pedido &pedido);
  // Saca el paquete completo más antiguo que llegó (0 si no hay). Copia hasta `max` bytes;
  // si el paquete es mayor, se pierde. macOrigen (6 B) es opcional.
  size_t recibir(uint8_t *buf, size_t max, uint8_t *macOrigen = nullptr);
  uint32_t mensajesEnviados() const { return enviados_; }      // entregados fragmento a fragmento
  uint32_t mensajesAbortados() const { return abortados_; }    // se agotaron los reintentos
  uint32_t fragmentosReintentados() const { return emisor_.reintentos(); }
  void fijarReintentos(uint8_t n) { emisor_.fijarReintentos(n); }  // por fragmento, antes de abortar

  // Velocidad de la radio: ESTANDAR es 1 Mbit/s; LR500 y LR250 son el modo de largo alcance de
  // Espressif (500 y 250 kbit/s), que alcanza más lejos a cambio de tardar 1,5 y 2,4 veces más.
  // Solo funciona entre ESP32 y exige haberlo activado en los dos extremos (cualquier placa que
  // llame a esto acepta recibir en largo alcance, sea cual sea su tasa de envío).
  //   datos      primer intento de cada fragmento
  //   reintentos segundo intento en adelante (subir a LR da más alcance justo donde falla)
  //   balizas    las balizas, que son las que detectan al vecino
  // Llamar después de iniciar(). Por defecto todo va en ESTANDAR.
  enum class Tasa : uint8_t { ESTANDAR, LR500, LR250 };
  void fijarTasas(Tasa datos, Tasa reintentos, Tasa balizas);
  uint32_t pedidosDescartados() const { return pedidosPerdidos_; }
  uint32_t mensajesRecibidos() const { return recibidosOk_; }  // completos y entregados a recibir()
  uint32_t mensajesDescartados() const { return reens_.descartados() + sinSitio_ + tramasPerdidas_; }
  bool activo() const { return iniciado_ && !pausado_; }
  uint32_t balizasEnviadas() const { return enviadas_; }
  uint32_t balizasRecibidas() const { return recibidas_; }
  uint32_t erroresDeEnvio() const { return erroresEnvio_; }
  uint32_t balizasPerdidas() const { return perdidasCola_; }  // llegaron con la cola llena

 private:
  struct Recibida {
    uint8_t mac[6];
    uint8_t datos[sp::TAM_BALIZA];
  };
  struct TramaCruda {  // un fragmento tal como llegó, de la radio a atender()
    uint8_t mac[6];
    uint8_t largo;
    uint8_t datos[sp::TAM_TRAMA_MAXIMO];
  };
  struct PedidoRecibido {
    uint8_t mac[6];
    sp::Pedido pedido;
  };
  struct MensajeRecibido {
    uint8_t mac[6];
    size_t largo = 0;
    uint8_t datos[MAX_MENSAJE];
  };

  static void alRecibir(const uint8_t *mac, const uint8_t *datos, int largo);
  static void alEnviar(const uint8_t *mac, int estado);
  void atenderRecepcion(uint32_t ahoraMs);
  void atenderEnvio(uint32_t ahoraMs);
  void soltarDestino();
  void fijarCanal();
  void enviarBaliza();
  void programarSiguiente(uint32_t ahoraMs, bool primera);
  void anotar(const String &texto) const;
  static String macTexto(const uint8_t mac[6]);

  Registro log_;
  TablaVecinos tabla_;
  sp::Baliza propia_;
  QueueHandle_t cola_ = nullptr;  // balizas de la radio hacia atender(), que corren en tareas distintas
  QueueHandle_t colaTramas_ = nullptr;  // fragmentos, igual
  QueueHandle_t colaPedidos_ = nullptr;  // PEDIDOs, igual
  volatile uint32_t pedidosPerdidos_ = 0;
  bool esperandoAvisoPedido_ = false;    // se mandó un PEDIDO y falta el aviso de la radio (no se mezcla con un fragmento)
  uint32_t pedidoMs_ = 0;
  uint8_t canal_ = 1, canalBase_ = 1;
  bool iniciado_ = false, pausado_ = false, silencio_ = false, interfazAp_ = false;
  uint8_t ifz_ = 0;  // wifi_interface_t por la que va ESP-NOW (0 = STA, 1 = AP); aquí sin el tipo del SDK
  void crearApOculto();
  void ponerProtocolos();
  uint32_t proximaMs_ = 0;
  volatile uint32_t recibidas_ = 0, perdidasCola_ = 0;
  uint32_t enviadas_ = 0, erroresEnvio_ = 0;
  Tasa tasaDatos_ = Tasa::ESTANDAR, tasaReintentos_ = Tasa::ESTANDAR, tasaBalizas_ = Tasa::ESTANDAR;
  // La última que se le pidió a la radio. Empieza en ESTANDAR sin tocarla: es la del driver (1 Mbit/s), y
  // esp_wifi_config_espnow_rate cambia la velocidad de TODA la interfaz STA, no solo de ESP-NOW: medido, con ella
  // puesta el router GUFI no dejaba autenticar a la placa (19 veces motivo 2) y sin ella conecta en 2 s. -1 = hay que
  // volver a configurarla (tras fijarTasas).
  int tasaPuesta_ = 0;  // (int)Tasa::ESTANDAR
  void ponerTasa(Tasa t);

  // Envío
  EmisorFragmentos emisor_;
  uint8_t destino_[6] = {};
  bool hayDestino_ = false, esperandoAviso_ = false;
  uint32_t idMensaje_ = 0, enviados_ = 0, abortados_ = 0;
  volatile bool avisoListo_ = false, avisoLlego_ = false;  // lo escribe el aviso de entrega de la radio

  // Recepción: un mensaje a la vez se reconstruye; los completos esperan a recibir()
  sp::Reensamblador reens_;
  MensajeRecibido espera_[MENSAJES_EN_ESPERA];
  uint8_t cabeza_ = 0, cuenta_ = 0;
  uint32_t recibidosOk_ = 0, sinSitio_ = 0;
  volatile uint32_t tramasPerdidas_ = 0;  // llegaron con la cola de fragmentos llena

  static VecinosEspNow *instancia_;  // el aviso de la radio es una función suelta
};
