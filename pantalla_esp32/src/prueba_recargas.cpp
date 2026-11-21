/*
 * Prueba de recargas y tarifas binarias (M3-04) sobre la microSD real.
 *
 * Cargar:  pio run -e prueba_recargas -t upload      Ver:  pio device monitor
 * Usa la carpeta /m3recargas de la microSD: no toca los archivos del validador.
 *
 * Comprueba que lo guardado se vuelve a leer igual tras "reiniciar" (objetos nuevos que cargan de la
 * tarjeta) y mide cuánto cuesta cada operación. Termina escribiendo "RESULTADO: CORRECTO" o
 * "RESULTADO: FALLO".
 */

#include <Arduino.h>
#include <ArchivosSD.h>
#include <ProtocoloSP.h>
#include <Recargas.h>
#include <SD.h>
#include <SPI.h>
#include <TarifasGuardadas.h>
#ifdef PROBAR_ALMACEN
#include "validador/almacen.h"  // -DPROBAR_ALMACEN: prueba también el Almacen del validador
#endif

#include <memory>

constexpr int8_t SD_SCK = 14, SD_MOSI = 13, SD_MISO = 27, SD_CS = 26;
#ifndef SD_HZ
#define SD_HZ 4000000  // el reloj de SPI de la microSD; se cambia con -DSD_HZ=...
#endif
SPIClass spiSD(HSPI);
ArchivosSD archivos("/m3recargas");
bool fallo = false;

void comprueba(bool ok, const char *que) {
  Serial.printf("  %s %s\n", ok ? "ok   " : "FALLA", que);
  if (!ok) fallo = true;
}

// /m3recargas/* siempre vacío al empezar
void limpiar() {
  for (const char *base : {"/recargas", "/tarifas"})
    for (const char *ext : {".bin", ".tmp", ".bak"}) {
      char r[40];
      snprintf(r, sizeof(r), "%s%s", base, ext);
      if (archivos.existe(r)) archivos.borrar(r);
    }
}

uint32_t ms(uint32_t desde) { return (micros() - desde) / 1000; }

void probarRecargas() {
  Serial.println("Recargas:");
  Recargas r(archivos);
  comprueba(!r.iniciar() && r.cantidad() == 0, "sin archivo arranca vacía");

  std::vector<Recarga> todas;
  for (uint32_t i = 0; i < 1000; i++) todas.push_back({1 + i / 10, 1 + i % 10, int32_t(100 + i)});
  uint32_t t = micros();
  comprueba(r.reemplazar(todas), "reemplazar 1000 recargas");
  Serial.printf("  MEDIDA guardar 1000 recargas (12 KB): %lu ms\n", (unsigned long)ms(t));

  t = micros();
  Recargas otra(archivos);
  const bool hay = otra.iniciar();
  Serial.printf("  MEDIDA cargar 1000 recargas: %lu ms\n", (unsigned long)ms(t));
  comprueba(hay && otra.cantidad() == 1000 && otra.todas() == r.todas(), "tras reiniciar son las mismas 1000");

  int32_t total;
  uint32_t hasta;
  otra.pendientes(5, 0, total, hasta);  // cuenta 5: seq 1 a 10, montos 140 a 149
  comprueba(hasta == 10 && total == 1445, "pendientes de la cuenta 5 (10 recargas, 1445)");
  otra.pendientes(5, 4, total, hasta);  // desde la seq 5: montos 144 a 149
  comprueba(hasta == 10 && total == 144 + 145 + 146 + 147 + 148 + 149, "pendientes desde la 5");

  // un paquete LISTAS con 120 entradas: 60 aplicadas y 60 nuevas
  std::unique_ptr<sp::Listas> l(new sp::Listas());
  memset(l.get(), 0, sizeof(*l));
  l->versionDesde = 5;
  l->versionHasta = 6;
  for (uint8_t i = 0; i < 60; i++) l->entradas[l->n++] = {sp::RECARGA, sp::APLICADA, 1 + i / 10u, 1 + i % 10u, 0};
  for (uint8_t i = 0; i < 60; i++) l->entradas[l->n++] = {sp::RECARGA, sp::PENDIENTE, 500, 1u + i, 7};
  t = micros();
  comprueba(otra.aplicar(*l), "aplicar un LISTAS de 120 entradas");
  Serial.printf("  MEDIDA aplicar 120 entradas: %lu ms\n", (unsigned long)ms(t));
  comprueba(otra.cantidad() == 1000 - 60 + 60, "quedan 1000 (60 quitadas, 60 nuevas)");
  t = micros();
  uint32_t consultas = 0;
  for (uint32_t i = 0; i < 1000; i++) {
    otra.pendientes(500, 0, total, hasta);
    consultas++;
  }
  Serial.printf("  MEDIDA pendientes() (60 correlativas) x%lu: %lu us cada una\n", (unsigned long)consultas,
                (unsigned long)((micros() - t) / consultas));
  comprueba(hasta == 60 && total == 420, "pendientes de la cuenta 500 (60 x 7)");

  Recargas final(archivos);
  comprueba(final.iniciar() && final.todas() == otra.todas(), "tras reiniciar quedó lo aplicado");
}

void probarTarifas() {
  Serial.println("Tarifas:");
  TarifasGuardadas t(archivos);
  comprueba(!t.iniciar() && t.cantidad() == 0, "sin archivo arranca sin tarifas");
  std::unique_ptr<sp::Tarifas> x(new sp::Tarifas());
  memset(x.get(), 0, sizeof(*x));
  x->version = 1793000000;
  x->n = 3;
  const struct { uint8_t codigo; uint32_t precio; const char *nombre; } datos[3] = {
      {1, 100, "General"}, {2, 50, "Estudiante"}, {3, 0, "Libre"}};
  for (int i = 0; i < 3; i++) {
    x->tarifas[i].codigo = datos[i].codigo;
    x->tarifas[i].precio = datos[i].precio;
    strncpy(x->tarifas[i].nombre, datos[i].nombre, 16);
  }
  uint32_t t0 = micros();
  comprueba(t.guardar(*x), "guardar 3 tarifas");
  Serial.printf("  MEDIDA guardar tarifas: %lu ms\n", (unsigned long)ms(t0));
  TarifasGuardadas otra(archivos);
  sp::Tarifa u;
  comprueba(otra.iniciar() && otra.cantidad() == 3 && otra.version() == 1793000000, "tras reiniciar siguen las 3");
  comprueba(otra.tarifa(2, u) && u.precio == 50 && strcmp(u.nombre, "Estudiante") == 0, "tarifa 2 = Estudiante, 50");
  comprueba(!otra.tarifa(9, u), "la 9 no existe");
}

#ifdef PROBAR_ALMACEN
// OJO: usa el Almacen de verdad, así que toca /recargas.*, /tarifas.* y /lista_negra.txt de la raíz de la
// microSD (el servidor los vuelve a mandar en la siguiente sincronización).
Almacen almacen;

void escribe(const char *ruta, const char *texto) {
  File f = SD.open(ruta, FILE_WRITE);
  f.print(texto);
  f.close();
}

void probarAlmacen() {
  Serial.println("Almacen del validador:");
  for (const char *r : {"/recargas.bin", "/recargas.tmp", "/recargas.bak", "/tarifas.bin", "/tarifas.tmp", "/tarifas.bak"})
    SD.remove(r);
  // como los dejaba la versión anterior (texto)
  escribe("/recargas.txt", "7 1 100\n7 2 200\n7 4 999\n8 1 5\n");
  escribe("/tarifas.txt", "1 general 130\n2 estudiante 80\n3 tarifa social 40\n");

  comprueba(almacen.iniciar(), "Almacen::iniciar() con microSD");
  comprueba(!SD.exists("/recargas.txt") && !SD.exists("/tarifas.txt"), "los .txt se migraron y se borraron");
  comprueba(SD.exists("/recargas.bin") && SD.exists("/tarifas.bin"), "existen los .bin");
  int32_t total;
  uint32_t hasta;
  almacen.recargasPendientes(7, 0, total, hasta);
  comprueba(total == 300 && hasta == 2, "cuenta 7: 100 + 200 hasta la seq 2 (la 4 no, falta la 3)");
  almacen.recargasPendientes(8, 0, total, hasta);
  comprueba(total == 5 && hasta == 1, "cuenta 8: 5 hasta la seq 1");
  Tarifa t;
  comprueba(almacen.tarifa(3, t) && t.precio == 40 && t.nombre == "tarifa social", "tarifa 3 = 'tarifa social', 40");
  comprueba(almacen.tarifa(1, t) && t.precio == 130 && !almacen.tarifa(9, t), "tarifa 1 = 130 y la 9 no existe");

  // la sincronización v1: listas nuevas completas en texto
  comprueba(almacen.guardarListas("5\n6\n", "1 general 140\n", "9 1 50\n9 2 60\n"), "guardarListas() con texto");
  almacen.recargasPendientes(9, 0, total, hasta);
  comprueba(total == 110 && hasta == 2 && almacen.numRecargas() == 2, "ahora solo hay las 2 de la cuenta 9");
  comprueba(almacen.tarifa(1, t) && t.precio == 140 && !almacen.tarifa(3, t), "ahora solo la tarifa 1 = 140");
  comprueba(almacen.bloqueada(5) && almacen.bloqueada(6) && !almacen.bloqueada(7), "lista negra (texto) sigue igual");

  // reiniciar: otro Almacen lee lo mismo de los .bin
  static Almacen otro;
  comprueba(otro.iniciar() && otro.numRecargas() == 2 && otro.tarifa(1, t) && t.precio == 140, "tras reiniciar es lo mismo");
  // sin tarifas guardadas, las de siempre
  comprueba(almacen.guardarListas("", "", "") && almacen.tarifa(1, t) && t.precio == 130 && almacen.tarifa(2, t) &&
                t.precio == 80 && !almacen.tarifa(3, t),
            "sin tarifas: general 130 y estudiante 80 por defecto");
}
#endif

void setup() {
  Serial.begin(115200);
  delay(1500);
  spiSD.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  if (!SD.begin(SD_CS, spiSD, SD_HZ) || !archivos.iniciar()) {
    Serial.println("ERROR microSD no disponible");
    Serial.println("RESULTADO: FALLO");
    return;
  }
  Serial.printf("microSD a %lu Hz\n", (unsigned long)SD_HZ);
  limpiar();
  probarRecargas();
  probarTarifas();
  limpiar();
#ifdef PROBAR_ALMACEN
  probarAlmacen();
#endif
  Serial.println(fallo ? "RESULTADO: FALLO" : "RESULTADO: CORRECTO");
}

void loop() { delay(1000); }
