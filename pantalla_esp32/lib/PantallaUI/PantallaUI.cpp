// Implementación de las pantallas del validador. Ver PantallaUI.h.
#include "PantallaUI.h"

#include <Arduino_GFX_Library.h>

namespace {

// Pantalla en el bus SPI principal (VSPI)
constexpr int8_t PIN_SCK = 18;
constexpr int8_t PIN_MOSI = 23;
constexpr int8_t PIN_CS = 5;
constexpr int8_t PIN_DC = 16;   // "A0" en la placa de la pantalla
constexpr int8_t PIN_RST = 17;

// --- Colores (RGB565) ----------------------------------------------------------
constexpr uint16_t COLOR_PASA = RGB565(0, 140, 60);
constexpr uint16_t COLOR_RECHAZO = RGB565(200, 0, 0);
constexpr uint16_t COLOR_AVISO = RGB565(230, 140, 0);
constexpr uint16_t COLOR_ESPERA = RGB565(0, 40, 90);
constexpr uint16_t BLANCO = RGB565_WHITE;

constexpr uint32_t TIEMPO_RESULTADO_MS = 3000;
constexpr int ANCHO = 130;
constexpr int ALTO = 130;
constexpr int ANCHO_LETRA = 6;   // fuente por defecto: 6x8 píxeles (a tamaño 1)
constexpr int ALTO_LETRA = 8;

Arduino_DataBus *bus = new Arduino_ESP32SPI(PIN_DC, PIN_CS, PIN_SCK, PIN_MOSI, GFX_NOT_DEFINED);
Arduino_GFX *gfx = new Arduino_SSD1283A(bus, PIN_RST, 0 /* rotación */);

String esperaLinea1 = "Validador";
String esperaLinea2 = "";
uint32_t volverAEsperaEn = 0;   // 0 = la pantalla de espera ya está dibujada

// Escribe `texto` centrado en la línea `y`, con el tamaño más grande (hasta
// `tamMax`) con el que cabe en el ancho. Devuelve la altura usada.
int textoCentrado(const String &texto, int y, uint8_t tamMax, uint16_t color) {
  uint8_t tam = tamMax;
  while (tam > 1 && (int)texto.length() * ANCHO_LETRA * tam > ANCHO - 4) {
    tam--;
  }
  String t = texto;
  int maxLetras = (ANCHO - 4) / (ANCHO_LETRA * tam);
  if ((int)t.length() > maxLetras) {
    t = t.substring(0, maxLetras);
  }
  int ancho = t.length() * ANCHO_LETRA * tam;
  gfx->setTextSize(tam);
  gfx->setTextColor(color);
  gfx->setCursor((ANCHO - ancho) / 2, y);
  gfx->print(t);
  return ALTO_LETRA * tam;
}

// Iconos dibujados con líneas gruesas (no dependen de la fuente)
void icono(char tipo, int cx, int cy) {
  gfx->fillCircle(cx, cy, 17, BLANCO);
  uint16_t c = tipo == 'P' ? COLOR_PASA : tipo == 'R' ? COLOR_RECHAZO : COLOR_AVISO;
  for (int d = -2; d <= 2; d++) {
    if (tipo == 'P') {            // visto
      gfx->drawLine(cx - 9, cy + d, cx - 3, cy + 6 + d, c);
      gfx->drawLine(cx - 3, cy + 6 + d, cx + 9, cy - 7 + d, c);
    } else if (tipo == 'R') {     // aspa
      gfx->drawLine(cx - 8 + d, cy - 8, cx + 8 + d, cy + 8, c);
      gfx->drawLine(cx + 8 + d, cy - 8, cx - 8 + d, cy + 8, c);
    }
  }
  if (tipo == 'A') {              // exclamación
    gfx->fillRect(cx - 2, cy - 11, 5, 14, c);
    gfx->fillRect(cx - 2, cy + 6, 5, 5, c);
  }
}

void pantallaResultado(char tipo, const String lineas[3]) {
  uint16_t fondo = tipo == 'P' ? COLOR_PASA : tipo == 'R' ? COLOR_RECHAZO : COLOR_AVISO;
  const char *titulo = tipo == 'P' ? "PASE" : tipo == 'R' ? "RECHAZADA" : "ATENCION";
  gfx->fillScreen(fondo);
  icono(tipo, ANCHO / 2, 24);
  int y = 48;
  y += textoCentrado(titulo, y, 3, BLANCO) + 8;
  for (int i = 0; i < 3; i++) {
    if (lineas[i].length() > 0) {
      y += textoCentrado(lineas[i], y, i == 0 ? 2 : 1, BLANCO) + 5;
    }
  }
  volverAEsperaEn = millis() + TIEMPO_RESULTADO_MS;
  if (volverAEsperaEn == 0) volverAEsperaEn = 1;
}

void pantallaEspera() {
  gfx->fillScreen(COLOR_ESPERA);
  // Símbolo de "acercar tarjeta": rectángulo con ondas
  gfx->drawRoundRect(45, 14, 40, 28, 4, BLANCO);
  gfx->fillRect(51, 20, 10, 8, BLANCO);
  for (int r = 6; r <= 14; r += 4) {
    gfx->drawCircle(ANCHO / 2, 50, r, BLANCO);
  }
  gfx->fillRect(0, 50, ANCHO, 16, COLOR_ESPERA);  // deja solo la mitad superior de las ondas
  textoCentrado("Acerque su", 64, 2, BLANCO);
  textoCentrado("tarjeta", 82, 2, BLANCO);
  textoCentrado(esperaLinea1, 108, 1, BLANCO);
  textoCentrado(esperaLinea2, 118, 1, BLANCO);
  volverAEsperaEn = 0;
}

}  // namespace

namespace ui {

bool iniciar() {
  bool ok = gfx->begin();
  gfx->setTextWrap(false);
  pantallaEspera();
  return ok;
}

void resultado(char tipo, const String &l1, const String &l2, const String &l3) {
  const String lineas[3] = {l1, l2, l3};
  pantallaResultado(tipo, lineas);
}

void espera(const String &l1, const String &l2) {
  esperaLinea1 = l1;
  esperaLinea2 = l2;
  if (volverAEsperaEn == 0) pantallaEspera();
}

void actualizar() {
  if (volverAEsperaEn != 0 && (int32_t)(millis() - volverAEsperaEn) >= 0) {
    pantallaEspera();
  }
}

}  // namespace ui
