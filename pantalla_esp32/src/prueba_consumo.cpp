/*
 * Prueba del consumo de datos (M3-06) sobre la microSD real: lo guardado sobrevive a un reinicio de verdad.
 *
 * Cargar:  pio run -e prueba_consumo -t upload      Ver:  pio device monitor
 * Usa la carpeta /m3consumo de la microSD: no toca los archivos del validador.
 *
 * Etapa 1 (no hay archivo): cuenta tráfico inventado de 3 canales en 2 días, mide el guardado y REINICIA la placa.
 * Etapa 2 (ya hay archivo): comprueba que tras el reinicio está todo, sigue contando sobre lo guardado, y limpia.
 * Termina escribiendo "RESULTADO: CORRECTO" o "RESULTADO: FALLO".
 */

#include <Arduino.h>
#include <ArchivosSD.h>
#include <ConsumoDatos.h>
#include <SD.h>
#include <SPI.h>

constexpr int8_t SD_SCK = 14, SD_MOSI = 13, SD_MISO = 27, SD_CS = 26;
#ifndef SD_HZ
#define SD_HZ 4000000
#endif
SPIClass spiSD(HSPI);
ArchivosSD archivos("/m3consumo");
bool fallo = false;

void comprueba(bool ok, const char *que) {
  Serial.printf("  %s %s\n", ok ? "ok   " : "FALLA", que);
  if (!ok) fallo = true;
}

constexpr uint32_t D1 = 20261120, D2 = 20261121;

void etapa1() {
  Serial.println("Etapa 1: contar, guardar y reiniciar");
  ConsumoDatos c(archivos);
  comprueba(!c.iniciar() && c.dias() == 0, "sin archivo arranca sin historial");
  c.fijarFecha(D1);
  c.observar(CanalDatos::WIFI, 1000, 400);
  c.observar(CanalDatos::WIFI, 4000, 900);   // el contador del enlace creció: 4000 / 900 en total
  c.registrar(CanalDatos::BLE, 30, 40);
  c.fijarFecha(D2);
  c.observar(CanalDatos::WIFI, 4500, 950);   // hoy: 500 / 50
  c.registrar(CanalDatos::ESPNOW, 7, 8);
  uint32_t tx, rx;
  c.bytesHoy(tx, rx);
  comprueba(tx == 507 && rx == 58, "hoy: 507 B enviados y 58 recibidos (WiFi + ESP-NOW)");
  uint32_t t = micros();
  const bool ok = c.guardar();
  Serial.printf("  MEDIDA guardar 2 días: %lu ms\n", (unsigned long)((micros() - t) / 1000));
  comprueba(ok && !c.hayCambios(), "guardado");
  Serial.println("REINICIANDO...");
  Serial.flush();
  delay(200);
  ESP.restart();
}

void etapa2() {
  Serial.println("Etapa 2: tras el reinicio");
  ConsumoDatos c(archivos);
  uint32_t t = micros();
  const bool ok = c.iniciar();
  Serial.printf("  MEDIDA cargar el historial: %lu ms\n", (unsigned long)((micros() - t) / 1000));
  comprueba(ok && c.dias() == 2, "el historial tiene 2 días");
  comprueba(c.dia(0).fecha == D1 && c.dia(0).tx[0] == 4000 && c.dia(0).rx[0] == 900 && c.dia(0).tx[1] == 30 && c.dia(0).rx[1] == 40,
            "día 1: WiFi 4000/900, BLE 30/40");
  comprueba(c.dia(1).fecha == D2 && c.dia(1).tx[0] == 500 && c.dia(1).rx[0] == 50 && c.dia(1).tx[2] == 7 && c.dia(1).rx[2] == 8,
            "día 2: WiFi 500/50, ESP-NOW 7/8");
  // el enlace se reinició (su contador vuelve a cero) y se sigue contando sobre lo guardado
  c.fijarFecha(D2);
  c.observar(CanalDatos::WIFI, 120, 30);
  uint32_t tx, rx;
  c.bytesHoy(tx, rx);
  comprueba(tx == 627 && rx == 88, "hoy sigue sumando: 507 + 120 = 627 enviados, 58 + 30 = 88 recibidos");
  comprueba(c.guardar(), "se vuelve a guardar");
  ConsumoDatos otra(archivos);
  comprueba(otra.iniciar() && otra.dia(1).tx[0] == 620, "y también sobrevive: WiFi de hoy 620");
}

void limpiar() {
  for (const char *r : {"/consumo.bin", "/consumo.tmp", "/consumo.bak", "/consumo.ab", "/consumo.abn"})
    if (archivos.existe(r)) archivos.borrar(r);
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  spiSD.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  if (!SD.begin(SD_CS, spiSD, SD_HZ) || !archivos.iniciar()) {
    Serial.println("ERROR microSD no disponible");
    Serial.println("RESULTADO: FALLO");
    return;
  }
  if (!archivos.existe("/consumo.ab")) {
    limpiar();
    etapa1();  // termina reiniciando
    return;
  }
  etapa2();
  limpiar();
  Serial.println(fallo ? "RESULTADO: FALLO" : "RESULTADO: CORRECTO");
}

void loop() { delay(1000); }
