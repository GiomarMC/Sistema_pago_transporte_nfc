// Sincronización con el servidor por WiFi (POST /api/sync/), igual que
// transporte/validador_local.py: sube los eventos pendientes de la microSD y
// guarda la lista negra, las recargas pendientes, las tarifas y la hora.
//
// Necesita al menos una red WiFi guardada (portal de configuración o
// config.txt) y en config.txt: servidor y token (el del validador en el
// servidor). Sin ellos no hace nada: el validador sigue cobrando y acumula los
// eventos en la microSD. El servidor puede ser:
//   https://dominio          servidor en internet; se verifica su certificado
//                            con las raíces de Let's Encrypt (raices_tls.h)
//   http://IP:puerto         servidor de pruebas en la red local
//   http://nombre.local:puerto  ídem, buscando el ordenador por mDNS
#pragma once
#include <Arduino.h>
#include <HTTPClient.h>
#include <EnlaceWifi.h>

#include "almacen.h"

class Sincronizador {
 public:
  using Registro = void (*)(const String &);
  Sincronizador(Almacen &almacen, Registro log, EnlaceWifi &enlace)
      : enlace_(enlace), almacen_(almacen), log_(log) {}

  // Llamar en cada vuelta de loop(). Conecta el WiFi sin bloquear y sincroniza
  // cada cierto tiempo (cada petición tarda como mucho unos segundos).
  // Devuelve true justo después de una sincronización correcta.
  bool atender();

  // Hora del servidor en la última sincronización (unix, 0 si no hubo) y el
  // millis() en que se recibió.
  uint32_t horaServidor() const { return horaServidor_; }
  uint32_t millisHora() const { return millisHora_; }

  // "WiFi OK", "Sin WiFi", "Sin servidor" o "" (sincronización no configurada)
  const String &estado() const { return estado_; }
  // Vuelve a empezar con la primera red guardada (tras cambiar las redes)
  void reiniciarWifi();

 private:
  bool sincronizar();
  bool resolverNombre(String &url);  // "nombre.local" -> IP (mDNS)
  void fallo(const String &motivo);
  void cerrarConexion();

  // La radio (redes guardadas, mDNS y los clientes TCP/TLS que se conservan entre
  // sincronizaciones para reutilizar la conexión) es del EnlaceWifi
  EnlaceWifi &enlace_;
  HTTPClient http_;
  String urlActual_;  // a qué servidor apunta la conexión abierta

  Almacen &almacen_;
  Registro log_;
  bool wifiConectado_ = false, ultimaOk_ = true, primera_ = true;
  uint32_t proxima_ = 0, reintento_ = 0;
  uint32_t horaServidor_ = 0, millisHora_ = 0;
  String estado_;
};
