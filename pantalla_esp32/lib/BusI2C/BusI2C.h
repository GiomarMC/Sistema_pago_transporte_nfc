// Soltar un bus I2C que un esclavo dejó retenido (#200).
//
// Si la ESP32 se reinicia a mitad de una lectura por I2C, el esclavo que estaba enviando (el PN532 o la OLED, que no
// se reinician con ella) se queda esperando reloj con SDA a nivel bajo, y así sigue en cada reinicio hasta que se le
// quita la corriente. Con SCL libre se suelta dando pulsos de reloj hasta que deje SDA (le faltan como mucho 8 bits y
// el ACK) y una condición de STOP. Medido en la 106 el 24 nov: 2 veces en 50 reinicios, y se soltó con 7 pulsos.
//
// Con SCL a nivel bajo no sirve: un módulo la retiene (medido: la OLED) y solo se arregla quitando la corriente.
#pragma once

#include <stdint.h>

// Llamar con el bus sin iniciar (o tras Wire.end()): toma los pines como GPIO y los deja con pull-up.
// Devuelve los pulsos que hicieron falta, 0 si el bus ya estaba libre y -1 si sigue retenido.
int liberarBusI2C(uint8_t sda, uint8_t scl);
