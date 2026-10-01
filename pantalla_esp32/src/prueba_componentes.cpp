/*
 * Prueba de componentes: pantalla LCD SSD1283A, módulo microSD y conexión USB.
 *
 * Cargar:  pio run -e prueba -t upload     Ver resultados:  pio device monitor
 *
 * 1. Pantalla: rojo, verde, azul y blanco a pantalla completa, y luego un resumen.
 *    Si ves los cuatro colores y el texto, la pantalla está bien conectada.
 * 2. microSD: detecta la tarjeta, escribe un archivo, lo lee y lo borra.
 * 3. USB: todo lo que escribas en el monitor serie aparece en la pantalla
 *    y vuelve como "ECO: ...".
 *
 * Conexiones (la placa rotula los pines como P5, P18...):
 *   Pantalla (bus VSPI): SCK->18  SDA->23  CS->5  A0->16  RST->17  VCC,LED->3V3  GND->GND
 *   microSD  (bus HSPI): CLK->14  MOSI->13 MISO->27  CS->26  3V3->3V3  GND->GND
 */

#include <Arduino.h>
#include <Arduino_GFX_Library.h>
#include <SD.h>
#include <SPI.h>

constexpr int8_t LCD_SCK = 18, LCD_MOSI = 23, LCD_CS = 5, LCD_DC = 16, LCD_RST = 17;
constexpr int8_t SD_SCK = 14, SD_MOSI = 13, SD_MISO = 27, SD_CS = 26;

Arduino_DataBus *bus = new Arduino_ESP32SPI(LCD_DC, LCD_CS, LCD_SCK, LCD_MOSI, GFX_NOT_DEFINED);
Arduino_GFX *gfx = new Arduino_SSD1283A(bus, LCD_RST, 0);
SPIClass spiSD(HSPI);

String resultadoSD = "SD: sin probar";
bool sdOk = false;

void linea(const String &texto, int y, uint16_t color, uint8_t tam = 1) {
  gfx->setTextSize(tam);
  gfx->setTextColor(color);
  gfx->setCursor(4, y);
  gfx->print(texto);
}

void probarPantalla() {
  Serial.println("[1] Pantalla: mostrando rojo, verde, azul y blanco...");
  const uint16_t colores[] = {RGB565_RED, RGB565_GREEN, RGB565_BLUE, RGB565_WHITE};
  const char *nombres[] = {"ROJO", "VERDE", "AZUL", "BLANCO"};
  for (int i = 0; i < 4; i++) {
    gfx->fillScreen(colores[i]);
    linea(nombres[i], 50, i == 3 ? RGB565_BLACK : RGB565_WHITE, 3);
    delay(800);
  }
  Serial.println("    Si viste los 4 colores, la pantalla funciona.");
}

void probarSD() {
  Serial.println("[2] microSD: iniciando...");
  spiSD.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  if (!SD.begin(SD_CS, spiSD, 4000000)) {
    resultadoSD = "SD: NO DETECTADA";
    Serial.println("    ERROR: no se detecta la tarjeta. Revisa cables, alimentacion y que");
    Serial.println("    la tarjeta este insertada y formateada en FAT32.");
    return;
  }
  uint64_t mb = SD.cardSize() / (1024ULL * 1024ULL);
  Serial.printf("    Tarjeta detectada: %llu MB\n", mb);

  const char *ruta = "/prueba_validador.txt";
  String escrito = "prueba " + String(millis());
  File f = SD.open(ruta, FILE_WRITE);
  if (!f) {
    resultadoSD = "SD: NO ESCRIBE";
    Serial.println("    ERROR: no se pudo crear el archivo de prueba.");
    return;
  }
  f.print(escrito);
  f.close();
  f = SD.open(ruta, FILE_READ);
  String leido = f ? f.readString() : "";
  if (f) f.close();
  SD.remove(ruta);

  if (leido == escrito) {
    sdOk = true;
    resultadoSD = "SD: OK " + String((uint32_t)(mb / 1024)) + " GB";
    Serial.println("    OK: escritura y lectura correctas.");
  } else {
    resultadoSD = "SD: LECTURA MAL";
    Serial.println("    ERROR: lo leido no coincide con lo escrito.");
  }
}

void resumen(const String &eco) {
  gfx->fillScreen(RGB565_BLACK);
  linea("PRUEBA", 4, RGB565_WHITE, 2);
  linea("Pantalla: OK", 30, RGB565_GREEN);
  linea(resultadoSD, 44, sdOk ? RGB565_GREEN : RGB565_RED);
  linea("USB: escribe en", 66, RGB565_WHITE);
  linea("el monitor serie", 78, RGB565_WHITE);
  if (eco.length() > 0) {
    linea("Recibido:", 100, RGB565_YELLOW);
    linea(eco.substring(0, 20), 112, RGB565_YELLOW);
  }
}

void setup() {
  pinMode(SD_CS, OUTPUT);
  digitalWrite(SD_CS, HIGH);
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println("=== Prueba de componentes del validador ===");
  if (!gfx->begin()) {
    Serial.println("[1] ERROR: no se pudo iniciar la pantalla");
  }
  gfx->setTextWrap(false);
  probarPantalla();
  probarSD();
  resumen("");
  Serial.println("[3] USB: escribe un texto y pulsa Enter; debe volver como ECO y verse en la pantalla.");
}

String buffer;

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (buffer.length() > 0) {
        Serial.println("ECO: " + buffer);
        resumen(buffer);
        buffer = "";
      }
    } else if (buffer.length() < 100) {
      buffer += c;
    }
  }
}
