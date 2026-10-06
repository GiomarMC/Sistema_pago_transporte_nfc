// Portal de configuración WiFi desde el celular, sin cables ni ordenador.
//
// La ESP32 crea su propia red ("Validador-105", con una contraseña aleatoria que
// se muestra en la pantalla) y sirve una página para elegir la red WiFi a la que
// debe conectarse. Es un portal cautivo: responde a las comprobaciones de
// conexión de Android, iOS y Windows, así que el celular muestra "Iniciar sesión
// en la red" y abre la página solo, aunque tenga los datos móviles activos.
//
// Solo configura redes WiFi. La clave maestra, el token y el n.º de validador no
// se ven ni se cambian aquí: se graban una vez al instalarlo (puente_nfc.py
// --configurar), para que quien tenga el validador en la mano no pueda leerlos.
#pragma once
#include <Arduino.h>

#include "almacen.h"

class PortalWifi {
 public:
  using Registro = void (*)(const String &);
  PortalWifi(Almacen &almacen, Registro log) : almacen_(almacen), log_(log) {}

  void iniciar();         // crea la red y muestra sus datos en la pantalla
  bool activo() const { return activo_; }
  // Llamar en cada vuelta de loop() mientras esté activo. Devuelve true cuando el
  // portal se acaba de cerrar (guardado, "Terminar" o 10 min sin uso).
  bool atender();

 private:
  void cerrar();
  void registrarRutas();
  void paginaPrincipal(const String &aviso = "");
  bool redirigirSiOtroHost();

  Almacen &almacen_;
  Registro log_;
  bool activo_ = false;
  String nombreRed_, claveRed_;
  String redesVistas_;          // <option> de las redes encontradas al abrir el portal
  uint32_t ultimoUso_ = 0;
  uint32_t cerrarEn_ = 0;       // cierre diferido (deja enviar la última página)
};
