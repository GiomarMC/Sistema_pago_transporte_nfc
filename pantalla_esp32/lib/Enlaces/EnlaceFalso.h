// Enlace falso para tests (M0-03): el test decide su estado, señal y tráfico.
//
//   EnlaceFalso wifi;                      // "wifi", código 1, Apagado
//   wifi.encender();                       // pasa a Conectando (como uno real)
//   wifi.ponerEstado(EstadoEnlace::Listo);
//   wifi.ponerCliente(&clienteDePrueba);   // lo que devolverá abrir()
//   wifi.sumarBytes(120, 300);
#pragma once

#include "Enlace.h"

class EnlaceFalso : public Enlace {
 public:
  explicit EnlaceFalso(const char *nombre = "wifi", uint8_t codigo = 1)
      : nombre_(nombre), codigo_(codigo) {}

  // --- Enlace ------------------------------------------------------------------------

  const char *nombre() const override { return nombre_; }
  uint8_t codigo() const override { return codigo_; }

  // Como uno real: encender empieza a conectar; el test decide cuándo queda Listo
  void encender() override {
    encendidos_++;
    if (estado_ == EstadoEnlace::Apagado) estado_ = EstadoEnlace::Conectando;
  }
  void apagar() override {
    apagados_++;
    cerrar();
    estado_ = EstadoEnlace::Apagado;
  }
  void atender() override { atenciones_++; }
  EstadoEnlace estado() const override { return estado_; }
  int8_t senal() const override { return senal_; }

  // El cliente fijado con ponerCliente(), solo si el enlace está Listo
  Client *abrir(const char *host, uint16_t puerto) override {
    aperturas_++;
    ultimoHost_ = host;
    ultimoPuerto_ = puerto;
    abierto_ = estado_ == EstadoEnlace::Listo ? cliente_ : nullptr;
    return abierto_;
  }
  void cerrar() override {
    if (abierto_ != nullptr) cierres_++;
    abierto_ = nullptr;
  }
  uint32_t bytesTx() const override { return tx_; }
  uint32_t bytesRx() const override { return rx_; }

  // --- Control desde el test ---------------------------------------------------------

  void ponerEstado(EstadoEnlace e) {
    estado_ = e;
    if (e != EstadoEnlace::Listo) abierto_ = nullptr;  // un enlace caído pierde su conexión
  }
  void ponerSenal(int8_t dbmMedios) { senal_ = dbmMedios; }
  void ponerCliente(Client *c) { cliente_ = c; }
  void sumarBytes(uint32_t tx, uint32_t rx) {
    tx_ += tx;
    rx_ += rx;
  }

  bool abierto() const { return abierto_ != nullptr; }
  const char *ultimoHost() const { return ultimoHost_; }
  uint16_t ultimoPuerto() const { return ultimoPuerto_; }
  uint32_t encendidos() const { return encendidos_; }
  uint32_t apagados() const { return apagados_; }
  uint32_t atenciones() const { return atenciones_; }
  uint32_t aperturas() const { return aperturas_; }
  uint32_t cierres() const { return cierres_; }

 private:
  const char *nombre_;
  uint8_t codigo_;
  EstadoEnlace estado_ = EstadoEnlace::Apagado;
  int8_t senal_ = 0;
  Client *cliente_ = nullptr;
  Client *abierto_ = nullptr;
  const char *ultimoHost_ = nullptr;
  uint16_t ultimoPuerto_ = 0;
  uint32_t tx_ = 0, rx_ = 0;
  uint32_t encendidos_ = 0, apagados_ = 0, atenciones_ = 0, aperturas_ = 0, cierres_ = 0;
};
