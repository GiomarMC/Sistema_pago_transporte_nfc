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
  void iniciar(uint8_t canalWifi) override;  // 0 se toma como 1
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
  // Saca el paquete completo más antiguo que llegó (0 si no hay). Copia hasta `max` bytes;
  // si el paquete es mayor, se pierde. macOrigen (6 B) es opcional.
  size_t recibir(uint8_t *buf, size_t max, uint8_t *macOrigen = nullptr);
  uint32_t mensajesEnviados() const { return enviados_; }      // entregados fragmento a fragmento
  uint32_t mensajesAbortados() const { return abortados_; }    // se agotaron los reintentos
  uint32_t fragmentosReintentados() const { return emisor_.reintentos(); }
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
  uint8_t canal_ = 1;
  bool iniciado_ = false, pausado_ = false;
  uint32_t proximaMs_ = 0;
  volatile uint32_t recibidas_ = 0, perdidasCola_ = 0;
  uint32_t enviadas_ = 0, erroresEnvio_ = 0;

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
