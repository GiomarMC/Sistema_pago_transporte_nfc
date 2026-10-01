/*
 * Pantalla del validador de bus.
 *
 * ESP32 (WROOM-32) + LCD SSD1283A de 1.6" (130x130) por SPI.
 * El ordenador que lee las tarjetas (validador.py) envía por USB una línea de
 * texto por cada evento; esta placa solo la dibuja.
 *
 * Protocolo (115200 baudios, una línea por mensaje, campos separados por '|',
 * solo ASCII):
 *   PASA|linea1|linea2|linea3      pantalla verde  (cobro aceptado)
 *   RECHAZO|linea1|linea2|linea3   pantalla roja   (saldo insuficiente, bloqueada...)
 *   AVISO|linea1|linea2|linea3     pantalla ámbar  (lectura fallida: acerque de nuevo)
 *   ESPERA|linea1|linea2           cambia el texto pequeño de la pantalla de espera
 *   PING                           responde "PANTALLA_OK" (para detectar la placa)
 * Las pantallas de resultado vuelven solas a la de espera a los 3 segundos.
 *
 * Conexiones (ver README; la placa rotula los pines como P5, P18...):
 *   LCD VCC -> 3V3    LCD GND -> GND    LCD LED -> 3V3
 *   LCD SCK -> GPIO18 LCD SDA -> GPIO23 LCD CS  -> GPIO5
 *   LCD A0  -> GPIO16 LCD RST -> GPIO17
 * El módulo microSD (si está montado) va en otro bus SPI; aquí no se usa, pero
 * su CS (GPIO26) se deja en alto para que no interfiera.
 */

#include <Arduino.h>
#include <PantallaUI.h>

constexpr int8_t PIN_SD_CS = 26;

void procesar(String linea) {
  linea.trim();
  if (linea.length() == 0) return;
  if (linea == "PING") {
    Serial.println("PANTALLA_OK");
    return;
  }
  // Separar en tipo y hasta 3 campos
  String campos[4];
  int n = 0, inicio = 0;
  while (n < 4) {
    int sep = linea.indexOf('|', inicio);
    if (sep < 0) {
      campos[n++] = linea.substring(inicio);
      break;
    }
    campos[n++] = linea.substring(inicio, sep);
    inicio = sep + 1;
  }
  if (campos[0] == "PASA") ui::resultado('P', campos[1], campos[2], campos[3]);
  else if (campos[0] == "RECHAZO") ui::resultado('R', campos[1], campos[2], campos[3]);
  else if (campos[0] == "AVISO") ui::resultado('A', campos[1], campos[2], campos[3]);
  else if (campos[0] == "ESPERA") ui::espera(campos[1], campos[2]);
  else {
    Serial.println("ERROR mensaje desconocido");
    return;
  }
  Serial.println("OK");
}

void setup() {
  pinMode(PIN_SD_CS, OUTPUT);
  digitalWrite(PIN_SD_CS, HIGH);
  Serial.begin(115200);
  if (!ui::iniciar()) {
    Serial.println("ERROR no se pudo iniciar la pantalla");
  }
  Serial.println("PANTALLA_OK");
}

String buffer;

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      procesar(buffer);
      buffer = "";
    } else if (c != '\r' && buffer.length() < 200) {
      buffer += c;
    }
  }
  ui::actualizar();
}
