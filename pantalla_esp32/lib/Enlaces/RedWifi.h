// Una red WiFi guardada (portal de configuración o config.txt). La comparten el
// Almacen (que la guarda en la microSD) y EnlaceWifi (que se conecta a ella).
#pragma once

#include <Arduino.h>

struct RedWifi {
  String ssid, clave;
};
