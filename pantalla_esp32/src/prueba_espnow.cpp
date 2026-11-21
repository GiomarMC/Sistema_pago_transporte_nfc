/*
 * Prueba de ESP-NOW entre buses (M8-01): balizas y tabla de vecinos.
 *
 * Cargar en DOS placas:  pio run -e prueba_espnow -t upload
 * Ver los mensajes:      pio device monitor
 *
 * No usa WiFi, pantalla, microSD ni PN532: solo la ESP32. Cada placa difunde una baliza
 * cada ~2 s en el canal 1 y escribe por el puerto serie cuándo detecta o pierde a otra.
 * Cada 5 s imprime la tabla de vecinos. El LED azul de la placa (GPIO2) se enciende
 * mientras haya al menos un vecino, para ver el resultado sin ordenador.
 *
 * Esperado: al encender las dos placas, cada una escribe "vecino ... detectado" en menos
 * de 3 s. Al apagar una, la otra escribe "vecino ... perdido" a los 10 s.
 *
 * El número de validador de prueba sale de la MAC, así que cada placa tiene el suyo.
 */

#include <Arduino.h>
#include <VecinosEspNow.h>
#include <WiFi.h>

constexpr uint8_t PIN_LED = 2;

void registro(const String &texto) { Serial.printf("[%8lu ms] %s\n", (unsigned long)millis(), texto.c_str()); }

VecinosEspNow vecinos(registro);
uint32_t ultimaTabla = 0;

void imprimirTabla() {
  Serial.printf("[%8lu ms] --- %u vecino(s) | enviadas %lu, recibidas %lu, errores de envío %lu, perdidas %lu\n",
                (unsigned long)millis(), (unsigned)vecinos.cantidad(), (unsigned long)vecinos.balizasEnviadas(),
                (unsigned long)vecinos.balizasRecibidas(), (unsigned long)vecinos.erroresDeEnvio(),
                (unsigned long)vecinos.balizasPerdidas());
  for (size_t i = 0; i < vecinos.cantidad(); i++) {
    const Vecino &v = vecinos.vecino(i);
    Serial.printf("             validador %u, pendientes %u, hace %lu ms\n", v.baliza.validador, v.baliza.pendientes,
                  (unsigned long)(millis() - v.ultimaMs));
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_LED, OUTPUT);
  WiFi.mode(WIFI_STA);
  uint8_t mac[6];
  WiFi.macAddress(mac);

  sp::Baliza propia;
  propia.validador = 1 + ((mac[4] << 8) | mac[5]) % 899;  // 1-899
  propia.flags = sp::BALIZA_ACEPTA_AJENOS;
  vecinos.publicar(propia);
  registro("Prueba ESP-NOW: validador de prueba " + String(propia.validador));
  vecinos.iniciar(1);
}

void loop() {
  vecinos.atender();
  digitalWrite(PIN_LED, vecinos.cantidad() > 0 ? HIGH : LOW);
  if (millis() - ultimaTabla >= 5000) {
    ultimaTabla = millis();
    imprimirTabla();
  }
  delay(5);
}
