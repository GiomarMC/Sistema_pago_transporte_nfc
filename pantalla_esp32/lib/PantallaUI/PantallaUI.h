// Pantallas del validador: espera, PASE, RECHAZADA y ATENCION. Compartido por el
// firmware de pantalla y el del validador. Dos implementaciones con la misma API:
//   - LCD SSD1283A 130x130 por SPI (PantallaUI.cpp, la de por defecto)
//   - OLED 1,3" 128x64 por I2C (PantallaUI_OLED.cpp, con -DPANTALLA_OLED)
#pragma once
#include <Arduino.h>

namespace ui {

// Inicia la pantalla (pines en PantallaUI.cpp) y dibuja la pantalla de espera.
bool iniciar();

// tipo: 'P' = pasa (verde), 'R' = rechazo (rojo), 'A' = aviso (ámbar).
// Vuelve sola a la pantalla de espera a los 3 segundos (llamar a actualizar()).
void resultado(char tipo, const String &l1, const String &l2 = "", const String &l3 = "");

// Cambia las dos líneas pequeñas de la pantalla de espera (no interrumpe un resultado).
void espera(const String &l1, const String &l2);

// Barra superior de espera: canal activo (W, BT o -), señal en dBm/2
// y cantidad de eventos pendientes. No interrumpe la pantalla de resultado.
void estadoCanal(const char *canal, int8_t senal, uint16_t pendientes);

// Indicador de sincronización: un circulito que gira en la barra superior de la espera mientras se envían o reciben
// datos. Solo redibuja su esquina (no toca el resto ni interrumpe un resultado); llamar con el estado en cada loop().
void actividad(bool activa);

// Llamar en cada vuelta de loop(): vuelve a la espera cuando toca y hace girar el indicador.
void actualizar();

// Pantalla fija con un título y hasta 6 líneas (p. ej. los datos del portal de
// configuración WiFi). Se queda hasta llamar a cerrarInformacion().
void informacion(const String &titulo, const String lineas[], int n);
void cerrarInformacion();

}  // namespace ui
