// Pantallas del validador en la LCD SSD1283A (130x130): espera, PASE, RECHAZADA
// y ATENCION. Compartido por el firmware de pantalla y el del validador.
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

// Llamar en cada vuelta de loop(): vuelve a la espera cuando toca.
void actualizar();

}  // namespace ui
