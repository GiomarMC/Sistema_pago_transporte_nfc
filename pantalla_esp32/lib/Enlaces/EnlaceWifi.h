// Contrato C2: enlace WiFi del validador (M6-01).
//
// Reúne lo que antes hacía el Sincronizador v1 con la radio:
//   - conectarse a las redes guardadas sin bloquear: sin conexión, cada ESCANEO_MS se buscan en segundo plano las
//     redes a la vista y se conecta a la guardada con más señal, en su canal (si no conecta en ESPERA_RED_MS, se
//     vuelve a buscar). Antes de conectar avisa del canal (alElegirCanal) para que ESP-NOW se pase a él: con ESP-NOW
//     transmitiendo en otro canal la autenticación con el AP no termina nunca (medido: motivo 2 en bucle). Una red
//     oculta (que no sale en la búsqueda) no se encuentra;
//   - buscar un servidor "nombre.local" en la red local (mDNS);
//   - mantener los clientes TCP/TLS entre peticiones (keep-alive): con HTTPS cada
//     conexión nueva cuesta 1-3 s de negociación, durante los que el validador no
//     cobra;
//   - dar un flujo TLS verificado con las raíces de confianza (abrir) o uno en claro
//     para pruebas en la red local (abrirPlano), y contar los bytes que pasan por él.
//
// Dos formas de usarlo, que no se mezclan al mismo tiempo (comparten los clientes):
//   v1 (HTTP)  clienteHttp() entrega un cliente sin conectar para HTTPClient.
//   v2 (MQTT)  abrir() entrega un cliente ya conectado, para CanalMqtt (M15).
// Los bytes solo se cuentan en los flujos de abrir()/abrirPlano().
//
// El portal de configuración (portal.cpp) sigue manejando la radio por su cuenta:
// mientras está activo, quien use este enlace no debe llamar a atender().
#pragma once

#include <Arduino.h>
#include <IPAddress.h>
#include <WiFiClient.h>
#include <WiFiClientSecure.h>

#include <vector>

#include "Enlace.h"
#include "RedWifi.h"

class EnlaceWifi final : public Enlace {
 public:
  using Registro = void (*)(const String &);
  // Las redes guardadas, en orden de preferencia. Se consulta en cada atender(), así
  // que una red nueva del portal se tiene en cuenta sin avisar.
  using ListaRedes = const std::vector<RedWifi> &(*)();

  static constexpr uint32_t ESPERA_RED_MS = 20000;  // para conectar antes de volver a buscar
  static constexpr uint32_t ESCANEO_MS = 15000;     // sin conexión, cada cuánto se buscan las redes guardadas
  static constexpr uint32_t REPETIR_ESCANEO_MS = 5000;  // tras un escaneo fallido (el driver puede seguir con él)
  static constexpr uint16_t MS_POR_CANAL = 200;
  static constexpr uint32_t ESPERA_INICIAL_MS = 1500;  // de encender la radio al primer escaneo
  static constexpr uint32_t LIMITE_ESCANEO_MS = 8000;  // un escaneo que sigue "en curso" pasado esto se da por colgado
  static constexpr uint32_t CASTIGO_FALLIDA_MS = 60000;  // una red que acaba de fallar cede ante otra a la vista
  // Canal del AP al que se va a conectar o con el que se conectó (0: ninguno, desconectado)
  using AvisoCanal = void (*)(uint8_t canal);
  using PuedeEscanear = bool (*)();  // false mientras no conviene escanear (ESP-NOW a mitad de un mensaje)
  // Una red a la vista que no deja conectar (motivo 2, 202...) se reintenta a los PAUSA_REINTENTO_MS, hasta
  // REINTENTOS_RED veces, en vez de esperar ESPERA_RED_MS: sin la reconexión automática del framework, el driver se
  // rinde tras el primer fallo (medido: dos motivos 2 y nada más en 20 s). Pasa sobre todo al volver a conectar
  // enseguida tras un reinicio estando conectada (el AP aún recuerda la conexión anterior): medido con el router GUFI y
  // ESP-NOW apagado, 2-3 rechazos con motivo 2 y al siguiente entra
  static constexpr uint8_t REINTENTOS_RED = 5;
  static constexpr uint32_t PAUSA_REINTENTO_MS = 2000;
  static constexpr uint32_t ESPERA_MDNS_MS = 2000;  // para encontrar un "nombre.local"

  // raicesPem: raíces de confianza en PEM (raices_tls.h); no puede ser nullptr
  EnlaceWifi(ListaRedes redes, Registro log, const char *raicesPem);

  // --- Enlace ------------------------------------------------------------------------

  const char *nombre() const override { return "wifi"; }
  uint8_t codigo() const override { return 1; }
  void encender() override;
  void apagar() override;
  void atender() override;
  // Apagado, Conectando, Listo, o Error si no hay ninguna red guardada
  EstadoEnlace estado() const override;
  int8_t senal() const override;
  // Flujo TLS conectado a host:puerto, verificado con las raíces. Reutiliza el que ya
  // está abierto al mismo destino. nullptr si el enlace no está Listo o falla.
  Client *abrir(const char *host, uint16_t puerto) override;
  void cerrar() override;
  uint32_t bytesTx() const override { return tx_; }
  uint32_t bytesRx() const override { return rx_; }

  // --- Además del contrato -----------------------------------------------------------

  // Igual que abrir(), pero sin TLS: solo para servidores de pruebas en la red local
  Client *abrirPlano(const char *host, uint16_t puerto);
  // Cliente sin conectar para HTTPClient (v1). Con tls, ya lleva las raíces puestas.
  WiFiClient &clienteHttp(bool tls);
  // Vuelve a buscar las redes enseguida (tras cambiar las redes)
  void reiniciar();
  void alElegirCanal(AvisoCanal aviso) { avisoCanal_ = aviso; }
  bool conectando() const { return intentando_; }  // hay un intento de conexión a un AP en curso
  void permitirEscaneo(PuedeEscanear puede) { puedeEscanear_ = puede; }
  // Nombre con que este validador se anuncia por mDNS (p. ej. "validador-107"); hay que
  // fijarlo antes de la primera búsqueda
  void nombreLocal(const String &nombre) { miNombre_ = nombre; }
  // IP de "host" (que termina en ".local") buscándola por mDNS. La recuerda hasta
  // olvidarNombres(). IPAddress() si no la halla.
  IPAddress resolverLocal(const String &host);
  void olvidarNombres();
  // Último fallo al abrir un flujo, para el registro ("" si no hubo)
  const String &ultimoError() const { return ultimoError_; }

 private:
  // Envuelve el cliente real para contar bytes sin que CanalMqtt lo sepa
  class Contado final : public Client {
   public:
    void usar(WiFiClient *c, uint32_t *tx, uint32_t *rx) { c_ = c; tx_ = tx; rx_ = rx; }
    int connect(IPAddress ip, uint16_t puerto) override { return c_->connect(ip, puerto); }
    int connect(const char *host, uint16_t puerto) override { return c_->connect(host, puerto); }
    size_t write(uint8_t b) override { return contarTx(c_->write(b)); }
    size_t write(const uint8_t *buf, size_t n) override { return contarTx(c_->write(buf, n)); }
    int available() override { return c_->available(); }
    int read() override {
      const int b = c_->read();
      if (b >= 0) ++*rx_;
      return b;
    }
    int read(uint8_t *buf, size_t n) override {
      const int leidos = c_->read(buf, n);
      if (leidos > 0) *rx_ += leidos;
      return leidos;
    }
    int peek() override { return c_->peek(); }
    void flush() override { c_->flush(); }
    void stop() override { c_->stop(); }
    uint8_t connected() override { return c_->connected(); }
    operator bool() override { return c_ != nullptr && static_cast<bool>(*c_); }

   private:
    size_t contarTx(size_t n) { *tx_ += n; return n; }
    WiFiClient *c_ = nullptr;
    uint32_t *tx_ = nullptr, *rx_ = nullptr;
  };

  Client *abrirCon(WiFiClient &cliente, bool tls, const char *host, uint16_t puerto);
  void prepararTls();
  void registrarEventos();
  void empezarEscaneo();
  void elegirYConectar(const std::vector<RedWifi> &redes, int16_t vistas);
  void conectarA(const RedWifi &r, int32_t canal, const uint8_t *bssid, int32_t rssi);
  void empezarIntento();
  void avisarCanal(uint8_t canal) {
    if (avisoCanal_) avisoCanal_(canal);
  }

  ListaRedes redes_;
  Registro log_;
  const char *raices_;

  WiFiClient plano_;
  WiFiClientSecure seguro_;
  Contado contado_;
  bool tlsListo_ = false;
  String destino_;  // "tls|host:puerto" del flujo abierto, para reutilizarlo
  String ultimoError_;

  bool encendido_ = false, iniciado_ = false, conectado_ = false, mdnsIniciado_ = false;
  uint32_t inicioIntento_ = 0;
  uint8_t reintentos_ = 0;  // de la red actual, tras un rechazo
  bool intentando_ = false, escaneando_ = false, hayEscaneo_ = false;
  uint32_t ultimoEscaneoMs_ = 0, fallidaMs_ = 0, avisoEscaneoMs_ = 0;
  uint16_t escaneosFallidos_ = 0;  // seguidos
  String intentoSsid_, intentoClave_, fallidaSsid_, vistas_ = "?";
  int32_t intentoCanal_ = 0;
  uint8_t intentoBssid_[6] = {};
  AvisoCanal avisoCanal_ = nullptr;
  PuedeEscanear puedeEscanear_ = nullptr;
  // Por qué se cayó o no conectó (reason del driver, WIFI_REASON_*), escrito desde la tarea del WiFi: para el registro
  static constexpr uint8_t MOTIVOS = 8;
  static volatile uint8_t motivos_[MOTIVOS];  // los primeros de este intento, en orden
  static volatile uint16_t desconexiones_;
  static volatile uint16_t fallos_;  // desconexiones que no son la propia (motivo 8, WiFi.disconnect())
  bool eventosRegistrados_ = false;
  void anotarFallo(const String &ssid);
  String miNombre_ = "validador", hostLocal_;
  IPAddress ipLocal_;
  uint32_t tx_ = 0, rx_ = 0;
};
