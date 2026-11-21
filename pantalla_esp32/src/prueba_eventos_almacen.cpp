/*
 * Prueba de los eventos del Almacen del validador (M3-01) sobre la microSD real: registrar viajes e incidencias en la
 * cola binaria, subirlos como el JSON de siempre, confirmarlos, reiniciar, y la migración de un /eventos.txt de una
 * versión anterior (con una línea cortada y otra ya subida).
 *
 * Cargar:  pio run -e prueba_eventos_almacen -t upload      Ver:  pio device monitor
 * OJO: usa los archivos de la RAÍZ de la microSD (/eventos.*, /estado.*, /ultima_op.txt). Los que hubiera los aparta
 * como *.prueba al empezar y los devuelve al terminar (si se corta a mitad, quedan con ese sufijo).
 * Termina escribiendo "RESULTADO: CORRECTO" o "RESULTADO: FALLO".
 */

#include <Arduino.h>
#include <SD.h>

#include <memory>
#include <vector>

#include "validador/almacen.h"

bool fallo = false;

void comprueba(bool ok, const char *que) {
  Serial.printf("  %s %s\n", ok ? "ok   " : "FALLA", que);
  if (!ok) fallo = true;
}

const char *ARCHIVOS[] = {"/eventos.txt", "/estado.txt", "/eventos.bin", "/eventos.tmp", "/estado.bin", "/estado.tmp",
                          "/estado.bak",  "/ultima_op.txt", "/eventos.migrado", "/estado.migrado"};

String sufijo(const char *r) { return String(r) + ".prueba"; }

void apartar() {
  for (const char *r : ARCHIVOS) {
    if (SD.exists(sufijo(r).c_str())) SD.remove(sufijo(r).c_str());
    if (SD.exists(r)) SD.rename(r, sufijo(r).c_str());
  }
}

void limpiar() {
  for (const char *r : ARCHIVOS)
    if (SD.exists(r)) SD.remove(r);
}

void devolver() {
  limpiar();
  for (const char *r : ARCHIVOS)
    if (SD.exists(sufijo(r).c_str())) SD.rename(sufijo(r).c_str(), r);
}

void tamanos(const char *cuando) {
  Serial.printf("  [%s]", cuando);
  for (const char *r : {"/eventos.bin", "/eventos.tmp", "/estado.bin", "/eventos.txt", "/eventos.bin.prueba"}) {
    File f = SD.open(r, FILE_READ);
    if (f) Serial.printf(" %s=%lu", r, (unsigned long)f.size());
    else Serial.printf(" %s=-", r);
    if (f) f.close();
  }
  Serial.println();
}

void escribir(const char *ruta, const String &t) {
  File f = SD.open(ruta, FILE_WRITE);
  f.print(t);
  f.close();
}

// Como los escribía el firmware anterior (formato de siempre)
String viajeViejo(uint32_t id, uint32_t tarjeta, int op) {
  return "{\"id\":" + String(id) + ",\"tipo\":\"viaje\",\"fecha\":\"2026-11-21T14:03:09+00:00\",\"tarjeta_id\":" + String(tarjeta) +
         ",\"uid\":\"04A1B2C3\",\"monto\":130,\"saldo_final\":870,\"operacion\":" + String(op) +
         ",\"contador\":9,\"recarga_hasta\":null,\"detalle\":null}";
}

uint32_t ms() { return millis(); }

// Regresión: File::size() de un archivo recién creado daba basura (211, 1073450320 bytes) y la cola lo tomaba por un
// registro incompleto en el primer arranque con una tarjeta limpia
void archivoNuevo() {
  Serial.println("Archivo recién creado:");
  ArchivosSD fs;
  bool todoCero = true;
  for (int i = 0; i < 4; i++) {
    if (SD.exists("/eventos.bin")) SD.remove("/eventos.bin");
    auto a = fs.abrir("/eventos.bin", Modo::ABRIR);
    todoCero = todoCero && a && a->tamano() == 0;
  }
  comprueba(todoCero, "el tamaño de un archivo nuevo es 0 (4 veces)");
  auto a = fs.abrir("/eventos.bin", Modo::ABRIR);
  const uint8_t datos[100] = {1};
  comprueba(a && a->agregar(datos, 100) && a->tamano() == 100 && a->agregar(datos, 28) && a->tamano() == 128, "y crece con lo que se agrega, sin vaciar");
  a.reset();
  a = fs.abrir("/eventos.bin", Modo::ABRIR);
  comprueba(a && a->tamano() == 128 && a->agregar(datos, 2) && a->tamano() == 130, "reabierto: 128 y sigue agregando al final");
  a.reset();
  SD.remove("/eventos.bin");
}

void migracion() {
  Serial.println("Migración de /eventos.txt y /estado.txt de una versión anterior:");
  // ids 1 a 7; el servidor ya tiene hasta el 4; la última línea (8) se cortó a mitad
  String texto;
  for (uint32_t i = 1; i <= 7; i++) texto += viajeViejo(i, 1000 + i, 10 + i) + "\n";
  texto += viajeViejo(8, 1008, 18).substring(0, 60);
  escribir("/eventos.txt", texto);
  escribir("/estado.txt", "siguiente_id=8\nultimo_subido=4\n");

  uint32_t t0 = ms();
  tamanos("antes del primer iniciar");
  std::unique_ptr<Almacen> a(new Almacen);
  comprueba(a->iniciar(), "Almacen::iniciar()");
  tamanos("tras el primer iniciar");
  Serial.printf("  MEDIDA iniciar() con la migración de 3 eventos: %lu ms\n", (unsigned long)(ms() - t0));
  comprueba(a->pendientes() == 3, "3 pendientes (5, 6 y 7)");
  std::vector<uint32_t> ids;
  const String json = a->eventosPendientes(10, ids);
  comprueba(ids.size() == 3 && ids[0] == 5 && ids[2] == 7, "ids 5, 6 y 7 (los mismos que esperaba el servidor)");
  comprueba(json == "[" + viajeViejo(5, 1005, 15) + "," + viajeViejo(6, 1006, 16) + "," + viajeViejo(7, 1007, 17) + "]",
            "el JSON que sube es idéntico al que escribía el firmware anterior");
  comprueba(!SD.exists("/eventos.txt") && SD.exists("/eventos.migrado") && SD.exists("/estado.migrado"), "el texto quedó como *.migrado");
  // el siguiente evento sigue la numeración
  t0 = ms();
  comprueba(a->registrarViaje(1795000000, 2000, "04A1B2C3D4E5F6", 130, 500, 3, 1, 0), "registrar un viaje");
  Serial.printf("  MEDIDA registrarViaje(): %lu ms\n", (unsigned long)(ms() - t0));
  comprueba(a->pendientes() == 4, "4 pendientes (5, 6, 7 y el nuevo, el 8)");

  // un corte a mitad de la migración: queda el texto otra vez y la cola ya tiene los eventos. No se duplica nada
  a.reset();
  texto = "";
  for (uint32_t i = 1; i <= 7; i++) texto += viajeViejo(i, 1000 + i, 10 + i) + "\n";
  escribir("/eventos.txt", texto);
  escribir("/estado.txt", "siguiente_id=8\nultimo_subido=4\n");
  a.reset(new Almacen);
  comprueba(a->iniciar() && a->pendientes() == 4, "repetir la migración no duplica (siguen 4 pendientes)");
  comprueba(!SD.exists("/eventos.txt"), "y el texto vuelve a quedar migrado");
}

void uso_normal() {
  Serial.println("Uso normal: registrar, subir, confirmar y reiniciar:");
  std::unique_ptr<Almacen> a(new Almacen);
  comprueba(a->iniciar() && a->pendientes() == 4, "tras reiniciar siguen 4 pendientes");
  uint32_t t0 = ms();
  comprueba(a->registrarIncidencia(1795000100, 77, "04112233", "operación 12 menor que la ya vista: saldo restaurado", true), "registrar un fraude");
  Serial.printf("  MEDIDA registrarIncidencia(): %lu ms\n", (unsigned long)(ms() - t0));
  std::vector<uint32_t> ids;
  t0 = ms();
  const String json = a->eventosPendientes(50, ids);
  Serial.printf("  MEDIDA eventosPendientes() de 5: %lu ms\n", (unsigned long)(ms() - t0));
  comprueba(ids.size() == 5 && ids[4] == 9, "5 pendientes, el último es el 9");
  comprueba(json.indexOf("\"tipo\":\"fraude\"") > 0 && json.indexOf("\"tarjeta_id\":77,\"uid\":\"04112233\"") > 0,
            "el fraude lleva su tarjeta y UID");
  comprueba(json.indexOf("\"detalle\":\"operaci??n 12 menor \"") > 0, "el detalle se corta a 20 caracteres ASCII");
  // la operación 3 de la tarjeta 2000 ya está registrada, la 4 no
  comprueba(a->existeViaje(2000, 3) && !a->existeViaje(2000, 4), "existeViaje(): la 3 sí, la 4 no");
  t0 = ms();
  a->marcarSubidos(7);
  Serial.printf("  MEDIDA marcarSubidos(): %lu ms\n", (unsigned long)(ms() - t0));
  comprueba(a->pendientes() == 2, "confirmados hasta el 7: quedan 2 (8 y 9)");
  // el acuse solo guardó el estado; compactar la cola es trabajo de mantenimiento(), con el lector libre
  uint32_t pasos = 0, peor = 0;
  for (int i = 0; i < 10; i++) {
    t0 = ms();
    if (!a->mantenimiento()) break;
    const uint32_t t = ms() - t0;
    pasos++;
    if (t > peor) peor = t;
  }
  Serial.printf("  MEDIDA mantenimiento(): %lu pasos, el más largo %lu ms\n", (unsigned long)pasos, (unsigned long)peor);
  comprueba(pasos == 1 && !a->mantenimiento(), "compactar con registros que quedan es un solo paso");
  a.reset(new Almacen);
  comprueba(a->iniciar() && a->pendientes() == 2 && a->existeViaje(2000, 3), "tras reiniciar: 2 pendientes y la operación de la tarjeta se recuerda");
  ids.clear();
  a->eventosPendientes(50, ids);
  comprueba(ids.size() == 2 && ids[0] == 8 && ids[1] == 9, "los pendientes son el 8 y el 9");
  a->marcarSubidos(9);
  comprueba(a->pendientes() == 0, "todo confirmado");
  comprueba(a->registrarViaje(1795000200, 2000, "04A1B2C3D4E5F6", 130, 370, 4, 2, 0), "otro viaje con la cola vacía");
  ids.clear();
  a->eventosPendientes(50, ids);
  comprueba(ids.size() == 1 && ids[0] == 10, "con la cola compactada los ids siguen (el 10)");
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  {
    Almacen montar;  // solo para montar la microSD
    if (!montar.iniciar()) {
      Serial.println("ERROR microSD no disponible");
      Serial.println("RESULTADO: FALLO");
      return;
    }
  }
  tamanos("antes de apartar");
  apartar();
  tamanos("tras apartar");
  archivoNuevo();
  migracion();
  uso_normal();
  devolver();
  Serial.println(fallo ? "RESULTADO: FALLO" : "RESULTADO: CORRECTO");
}

void loop() { delay(1000); }
