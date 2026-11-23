// Implementación de las pantallas del validador para la OLED de 1,3" (128x64,
// monocroma, I2C, módulo JMD1.3A). Misma API que la de la LCD: ver PantallaUI.h.
//
// Se activa compilando con -DPANTALLA_OLED (entornos *_oled de platformio.ini).
//   -DOLED_SSD1306        el módulo lleva un SSD1306 (por defecto se asume SH1106)
//   -DOLED_DIRECCION=0x3D dirección I2C de 7 bits (por defecto 0x3C; la serigrafía
//                         0X78 / 0X7A del módulo son 0x3C / 0x3D escritas en 8 bits)
//
// Cableado: SDA = P21, SCL = P22, los mismos pines que el PN532 (comparten bus).
#ifdef PANTALLA_OLED
#include "PantallaUI.h"

#include <U8g2lib.h>
#include <Wire.h>

#ifndef OLED_DIRECCION
#define OLED_DIRECCION 0x3C
#endif

namespace {

constexpr int8_t PIN_SDA = 21;
constexpr int8_t PIN_SCL = 22;
// Igual que el PN532: así los dos módulos comparten bus sin cambiarse la velocidad
constexpr uint32_t VELOCIDAD_I2C = 100000;

#ifdef OLED_SSD1306
U8G2_SSD1306_128X64_NONAME_F_HW_I2C pantalla(U8G2_R0, U8X8_PIN_NONE);
#else
U8G2_SH1106_128X64_NONAME_F_HW_I2C pantalla(U8G2_R0, U8X8_PIN_NONE);
#endif

bool oledLista = false;        // false mientras el bus I2C esté bloqueado o la OLED no conteste
uint32_t ultimoReintentoMs = 0;

constexpr int ANCHO = 128;
constexpr int ALTO = 64;
constexpr int ANCHO_UTIL = ANCHO - 2;
constexpr uint32_t TIEMPO_RESULTADO_MS = 3000;

// Fuentes de grande a pequeña: se usa la mayor en la que el texto cabe
const uint8_t *const FUENTES_TITULO[] = {u8g2_font_helvB12_tf, u8g2_font_helvB10_tf, u8g2_font_6x10_tf};
const uint8_t *const FUENTES_L1[] = {u8g2_font_7x13B_tf, u8g2_font_6x13B_tf, u8g2_font_6x10_tf};
const uint8_t *const FUENTES_L2[] = {u8g2_font_6x10_tf, u8g2_font_5x8_tf};
const uint8_t *const FUENTES_L3[] = {u8g2_font_6x10_tf, u8g2_font_5x8_tf};
const uint8_t *const FUENTES_ESPERA[] = {u8g2_font_7x13B_tf, u8g2_font_6x13B_tf, u8g2_font_6x10_tf};
const uint8_t *const FUENTES_PEQUENA[] = {u8g2_font_6x10_tf, u8g2_font_5x8_tf};

String esperaLinea1 = "Validador";
String esperaLinea2 = "";
String canalActual = "-";       // barra superior de la espera (ver ui::estadoCanal)
int8_t senalActual = 0;
uint16_t pendientesActuales = 0;
uint32_t volverAEsperaEn = 0;   // 0 = la pantalla de espera ya está dibujada
bool informacionFija = false;   // pantalla de información: no se sustituye sola

// Indicador de sincronización: circulito de radio 3 que gira (le falta un cuarto) en el bloque de 8x8 píxeles de la
// barra superior que va de x=88 a 95, libre entre las ondas del símbolo de la tarjeta y "P:n". Se redibuja solo ese
// bloque (updateDisplayArea): por I2C son unos bytes, no la pantalla entera.
constexpr uint8_t GIRO_BLOQUE_X = 11, GIRO_BLOQUE_Y = 0;  // en bloques de 8 px
constexpr int GIRO_CX = 91, GIRO_CY = 4, GIRO_R = 3;
constexpr uint32_t GIRO_PASO_MS = 200;
bool giroActivo = false;
uint8_t giroFase = 0;
uint32_t giroSiguienteMs = 0;

// Escribe `texto` con su borde superior en `y`, centrado entre `x0` y `x1`, con la
// fuente más grande de la lista en la que cabe (si en ninguna, se recorta con la
// más pequeña). Devuelve el alto de la fuente usada.
template <size_t N>
int texto(const String &texto, int y, const uint8_t *const (&fuentes)[N], int x0 = 0, int x1 = ANCHO) {
  const int disponible = (x1 - x0) - 2;
  String t = texto;
  size_t i = 0;
  while (i < N) {
    pantalla.setFont(fuentes[i]);
    if (pantalla.getUTF8Width(t.c_str()) <= disponible) break;
    i++;
  }
  if (i == N) {   // no cabe ni con la más pequeña: recortar
    i = N - 1;
    pantalla.setFont(fuentes[i]);
    while (t.length() > 1 && pantalla.getUTF8Width(t.c_str()) > disponible) {
      t.remove(t.length() - 1);
    }
  }
  int x = x0 + ((x1 - x0) - pantalla.getUTF8Width(t.c_str())) / 2;
  pantalla.drawUTF8(x, y, t.c_str());
  return pantalla.getMaxCharHeight();
}

// Iconos de 16x16 dibujados con líneas (no dependen de la fuente). `color` es el
// color del trazo: 1 = encendido, 0 = apagado (sobre la barra encendida).
void icono(char tipo, int x, int y, uint8_t color) {
  pantalla.setDrawColor(color);
  if (tipo == 'P') {            // visto
    for (int d = 0; d < 3; d++) {
      pantalla.drawLine(x + 1, y + 8 + d, x + 6, y + 13 + d);
      pantalla.drawLine(x + 6, y + 13 + d, x + 15, y + 3 + d);
    }
  } else if (tipo == 'R') {     // aspa
    for (int d = 0; d < 3; d++) {
      pantalla.drawLine(x + 2 + d, y + 2, x + 13 + d, y + 13);
      pantalla.drawLine(x + 13 + d, y + 2, x + 2 + d, y + 13);
    }
  } else {                      // exclamación
    pantalla.drawBox(x + 6, y + 1, 4, 9);
    pantalla.drawBox(x + 6, y + 12, 4, 3);
  }
  pantalla.setDrawColor(1);
}

bool enEspera() { return oledLista && volverAEsperaEn == 0 && !informacionFija; }

void dibujarGiro() {
  pantalla.setDrawColor(0);
  pantalla.drawBox(GIRO_BLOQUE_X * 8, GIRO_BLOQUE_Y * 8, 8, 8);
  pantalla.setDrawColor(1);
  if (!giroActivo) return;
  static const uint8_t CUARTOS[4] = {U8G2_DRAW_UPPER_RIGHT, U8G2_DRAW_LOWER_RIGHT, U8G2_DRAW_LOWER_LEFT,
                                     U8G2_DRAW_UPPER_LEFT};
  for (uint8_t i = 0; i < 4; i++)
    if (i != giroFase) pantalla.drawCircle(GIRO_CX, GIRO_CY, GIRO_R, CUARTOS[i]);
}

void enviarGiro() {
  dibujarGiro();
  pantalla.updateDisplayArea(GIRO_BLOQUE_X, GIRO_BLOQUE_Y, 1, 1);
}

void pantallaResultado(char tipo, const String lineas[3]) {
  const char *titulo = tipo == 'P' ? "PASE" : tipo == 'R' ? "RECHAZADA" : "ATENCION";
  pantalla.clearBuffer();
  pantalla.setFontPosTop();

  // Barra superior encendida con el icono y el título en negativo
  pantalla.setDrawColor(1);
  pantalla.drawBox(0, 0, ANCHO, 20);
  icono(tipo, 4, 2, 0);
  pantalla.setDrawColor(0);
  texto(titulo, 2, FUENTES_TITULO, 22, ANCHO);
  pantalla.setDrawColor(1);

  int y = 23;   // con las tres líneas: 23-36, 38-48 y 50-60 (la pantalla tiene 64 de alto)
  if (lineas[0].length() > 0) y += texto(lineas[0], y, FUENTES_L1) + 2;
  if (lineas[1].length() > 0) y += texto(lineas[1], y, FUENTES_L2) + 2;
  if (lineas[2].length() > 0) texto(lineas[2], y, FUENTES_L3);
  pantalla.sendBuffer();

  volverAEsperaEn = millis() + TIEMPO_RESULTADO_MS;
  if (volverAEsperaEn == 0) volverAEsperaEn = 1;
}

void pantallaEspera() {
  pantalla.clearBuffer();
  pantalla.setFontPosTop();
  pantalla.setDrawColor(1);
  // Canal y señal a la izquierda, eventos pendientes a la derecha (la tarjeta queda en medio)
  static const uint8_t *const FUENTE_BARRA[] = {u8g2_font_5x8_tf};
  pantalla.setFont(FUENTE_BARRA[0]);
  pantalla.drawUTF8(1, 0, (senalActual ? canalActual + " " + String(senalActual) : canalActual).c_str());
  const String pendientes = "P:" + String(pendientesActuales);
  pantalla.drawUTF8(ANCHO - 1 - pantalla.getUTF8Width(pendientes.c_str()), 0, pendientes.c_str());
  // Símbolo de "acercar tarjeta": tarjeta con chip y ondas a la derecha
  pantalla.drawRFrame(42, 2, 28, 18, 3);
  pantalla.drawBox(47, 7, 8, 6);
  for (int r = 5; r <= 13; r += 4) {
    pantalla.drawCircle(74, 11, r, U8G2_DRAW_UPPER_RIGHT | U8G2_DRAW_LOWER_RIGHT);
  }
  texto("Acerque su tarjeta", 25, FUENTES_ESPERA);
  texto(esperaLinea1, 41, FUENTES_PEQUENA);
  texto(esperaLinea2, 52, FUENTES_PEQUENA);
  dibujarGiro();
  pantalla.sendBuffer();
  volverAEsperaEn = 0;
}

// Pone en marcha la OLED si el bus I2C está libre y ella responde. A veces, tras un reinicio de la ESP32 (puerto
// serie, botón EN, watchdog), un módulo del bus (casi seguro el PN532, que no se reinicia con ella) deja SCL a nivel
// bajo y ahí se queda hasta quitar la corriente: medido con SCL=0 antes de Wire.begin(), sin soltarse en 20 s ni con
// pulsos de reloj. Con el bus así, pantalla.begin() de U8g2 no vuelve nunca y el watchdog reinicia la placa en bucle
// (#200). Por eso se mira antes y, si el bus está bloqueado, se sigue SIN pantalla y se reintenta cada 5 s.
bool intentarArrancar() {
  if (digitalRead(PIN_SCL) == 0 || digitalRead(PIN_SDA) == 0) return false;
  Wire.begin(PIN_SDA, PIN_SCL, VELOCIDAD_I2C);
  Wire.beginTransmission(OLED_DIRECCION);
  if (Wire.endTransmission() != 0) return false;   // nadie responde en esa dirección (o el bus se bloqueó)

  pantalla.setI2CAddress(OLED_DIRECCION << 1);   // U8g2 pide la dirección en 8 bits
  pantalla.begin();
  pantalla.setBusClock(VELOCIDAD_I2C);
  pantalla.enableUTF8Print();
  pantalla.setFontMode(1);   // texto transparente: así se puede escribir en negativo sobre la barra
  oledLista = true;
  pantallaEspera();
  return true;
}

}  // namespace

namespace ui {

bool iniciar() {
  if (intentarArrancar()) return true;
  Serial.println(String("#AVISO: la OLED no responde (SCL=") + digitalRead(PIN_SCL) + ", SDA=" + digitalRead(PIN_SDA) +
                 "): se sigue sin pantalla y se reintenta cada 5 s. Si SCL=0, hay que quitar la corriente de la placa");
  return false;
}

void resultado(char tipo, const String &l1, const String &l2, const String &l3) {
  if (!oledLista) return;
  const String lineas[3] = {l1, l2, l3};
  pantallaResultado(tipo, lineas);
}

void espera(const String &l1, const String &l2) {
  esperaLinea1 = l1;
  esperaLinea2 = l2;
  if (oledLista && volverAEsperaEn == 0 && !informacionFija) pantallaEspera();
}

void estadoCanal(const char *canal, int8_t senal, uint16_t pendientes) {
  canalActual = canal ? canal : "-";
  senalActual = senal;
  pendientesActuales = pendientes;
}

void actividad(bool activa) {
  if (activa == giroActivo) return;
  giroActivo = activa;
  giroFase = 0;
  giroSiguienteMs = millis() + GIRO_PASO_MS;
  if (enEspera()) enviarGiro();
}

void actualizar() {
  if (!oledLista) {
    if (millis() - ultimoReintentoMs >= 5000) {
      ultimoReintentoMs = millis();
      if (intentarArrancar()) Serial.println("#La OLED respondió: pantalla recuperada");
    }
    return;
  }
  if (!informacionFija && volverAEsperaEn != 0 && (int32_t)(millis() - volverAEsperaEn) >= 0) {
    pantallaEspera();
  }
  if (giroActivo && enEspera() && (int32_t)(millis() - giroSiguienteMs) >= 0) {
    giroFase = (giroFase + 1) % 4;
    giroSiguienteMs = millis() + GIRO_PASO_MS;
    enviarGiro();
  }
}

void informacion(const String &titulo, const String lineas[], int n) {
  if (!oledLista) return;
  informacionFija = true;
  pantalla.clearBuffer();
  pantalla.setFontPosTop();
  pantalla.setDrawColor(1);
  pantalla.drawBox(0, 0, ANCHO, 10);
  pantalla.setDrawColor(0);
  static const uint8_t *const FUENTE_TITULO[] = {u8g2_font_5x8_tf};
  texto(titulo, 1, FUENTE_TITULO);
  pantalla.setDrawColor(1);
  int y = 12;
  for (int i = 0; i < n && y < ALTO - 7; i++) {
    // Las líneas que empiezan por '*' van más grandes (datos que hay que teclear)
    if (lineas[i].startsWith("*")) {
      static const uint8_t *const FUENTE_DATO[] = {u8g2_font_6x10_tf, u8g2_font_5x8_tf};
      y += texto(lineas[i].substring(1), y, FUENTE_DATO) + 1;
    } else {
      static const uint8_t *const FUENTE_NORMAL[] = {u8g2_font_5x8_tf};
      y += texto(lineas[i], y, FUENTE_NORMAL) + 1;
    }
  }
  pantalla.sendBuffer();
}

void cerrarInformacion() {
  informacionFija = false;
  if (oledLista) pantallaEspera();
}

}  // namespace ui

#endif  // PANTALLA_OLED
