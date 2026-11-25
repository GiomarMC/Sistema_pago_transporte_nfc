#include "BusI2C.h"

#include <Arduino.h>

int liberarBusI2C(uint8_t sda, uint8_t scl) {
  pinMode(sda, INPUT_PULLUP);
  pinMode(scl, INPUT_PULLUP);
  delayMicroseconds(10);
  if (digitalRead(scl) == 0) return -1;
  if (digitalRead(sda) == 1) return 0;
  pinMode(scl, OUTPUT_OPEN_DRAIN);
  int pulsos = 0;
  while (pulsos < 18 && digitalRead(sda) == 0) {  // 9 de sobra por si el primero no llega a contar
    digitalWrite(scl, LOW);
    delayMicroseconds(10);
    digitalWrite(scl, HIGH);
    delayMicroseconds(10);
    pulsos++;
  }
  // STOP: SDA sube mientras SCL está alta
  pinMode(sda, OUTPUT_OPEN_DRAIN);
  digitalWrite(scl, LOW);
  delayMicroseconds(10);
  digitalWrite(sda, LOW);
  delayMicroseconds(10);
  digitalWrite(scl, HIGH);
  delayMicroseconds(10);
  digitalWrite(sda, HIGH);
  delayMicroseconds(10);
  pinMode(sda, INPUT_PULLUP);
  pinMode(scl, INPUT_PULLUP);
  delayMicroseconds(10);
  return digitalRead(sda) == 1 && digitalRead(scl) == 1 ? pulsos : -1;
}
