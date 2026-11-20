/*
 * Prueba de la OLED de 1,3" (módulo JMD1.3A, 128x64, I2C).
 *
 * Cargar:  pio run -e prueba_oled -t upload      Ver resultados:  pio device monitor
 *
 * 1. Escanea el bus I2C y escribe en el monitor serie las direcciones que responden.
 *    Esperado: 0x3C (OLED) y, si el PN532 está conectado, 0x24.
 * 2. Recorre en la OLED las mismas pantallas que usa el validador: espera, PASE,
 *    RECHAZADA, ATENCION y la pantalla de información del portal WiFi.
 *
 * Cableado (la placa rotula los pines como P21, P22...):
 *   OLED: GND->GND  VCC->3V3  SCL->P22  SDA->P21     (¡el orden del módulo es GND, VCC, SCL, SDA!)
 *
 * Cómo saber si el controlador es el correcto (la barra superior de PASE/RECHAZADA
 * llega de borde a borde, así que se nota enseguida):
 *   - Barra completa, sin ruido ni franjas: el controlador es el correcto.
 *   - Imagen corrida 2 píxeles a la derecha, con una franja de ruido en un borde
 *     o con basura: el módulo lleva el otro controlador. Cambia de entorno:
 *       pio run -e prueba_oled_ssd1306 -t upload     (si estabas con prueba_oled)
 *   - Pantalla en negro y el escáner no ve 0x3C ni 0x3D: revisa GND, VCC, SCL y SDA.
 *   - El escáner ve 0x3D en vez de 0x3C: la resistencia del selector ADD_SELECT está
 *     del lado 0X7A. Compila con -DOLED_DIRECCION=0x3D (build_flags en platformio.ini).
 */

#include <Arduino.h>
#include <PantallaUI.h>
#include <Wire.h>

constexpr int8_t PIN_SDA = 21, PIN_SCL = 22;

void escanearBus() {
  Serial.println("[1] Escaneando el bus I2C (SDA=P21, SCL=P22)...");
  int encontrados = 0;
  for (uint8_t dir = 1; dir < 127; dir++) {
    Wire.beginTransmission(dir);
    if (Wire.endTransmission() != 0) continue;
    encontrados++;
    const char *quien = dir == 0x24 ? "PN532" : (dir == 0x3C || dir == 0x3D) ? "OLED" : dir == 0x68 ? "reloj DS3231" : "desconocido";
    Serial.printf("    Dispositivo I2C en 0x%02X  (%s)\n", dir, quien);
  }
  if (encontrados == 0) {
    Serial.println("    No responde nada: revisa GND, VCC, SCL y SDA de la OLED (orden GND, VCC, SCL, SDA).");
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== Prueba de la OLED ===");
  Wire.begin(PIN_SDA, PIN_SCL, 100000);
  escanearBus();

  Serial.println("[2] Iniciando la pantalla...");
  if (!ui::iniciar()) {
    Serial.println("    AVISO: nada responde en la dirección de la OLED (0x3C por defecto).");
  }
  ui::espera("Bus 101  WiFi-casa", "Prueba de la OLED");
}

void loop() {
  static const char *pasos[] = {"espera", "PASE", "RECHAZADA", "ATENCION", "informacion"};
  static uint8_t paso = 0;
  static uint32_t ultimo = 0;
  ui::actualizar();
  if (millis() - ultimo < 3500) return;
  ultimo = millis();

  Serial.printf("    Pantalla: %s\n", pasos[paso]);
  switch (paso) {
    case 0: ui::cerrarInformacion(); break;
    case 1: ui::resultado('P', "Adulto S/ 1.50", "Saldo S/ 18.50", "Recarga +S/ 5.00"); break;
    case 2: ui::resultado('R', "Saldo insuficiente", "Saldo S/ 0.50"); break;
    case 3: ui::resultado('A', "Acerque de nuevo"); break;
    case 4: {
      const String lineas[] = {"Conecte el celular a", "*SubePe-AB12", "Contrasena:", "*12345678", "o abra 192.168.4.1"};
      ui::informacion("CONFIGURAR WIFI", lineas, 5);
      break;
    }
  }
  paso = (paso + 1) % 5;
}
