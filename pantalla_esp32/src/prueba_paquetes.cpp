/*
 * Prueba de los paquetes ajenos y firmados (M3-05) sobre la microSD real: mide el coste con 500 archivos.
 *
 * Cargar:  pio run -e prueba_paquetes -t upload      Ver:  pio device monitor
 * Usa la carpeta /m3paquetes de la microSD: no toca los archivos del validador.
 * Termina escribiendo "RESULTADO: CORRECTO" o "RESULTADO: FALLO".
 */

#include <Arduino.h>
#include <ArchivosSD.h>
#include <PaquetesGuardados.h>
#include <ProtocoloSP.h>
#include <SD.h>
#include <SPI.h>

#include <memory>

constexpr int8_t SD_SCK = 14, SD_MOSI = 13, SD_MISO = 27, SD_CS = 26;
#ifndef SD_HZ
#define SD_HZ 4000000  // el reloj de SPI de la microSD; se cambia con -DSD_HZ=...
#endif
SPIClass spiSD(HSPI);
ArchivosSD archivos("/m3paquetes");
bool fallo = false;
const uint8_t CLAVE[32] = {1, 2, 3};

void comprueba(bool ok, const char *que) {
  Serial.printf("  %s %s\n", ok ? "ok   " : "FALLA", que);
  if (!ok) fallo = true;
}

uint32_t ms(uint32_t desde) { return (micros() - desde) / 1000; }

size_t eventos(uint8_t *salida, uint16_t origen, uint32_t secuencia, uint32_t creado, uint32_t primero) {
  std::unique_ptr<sp::Paquete> p(new sp::Paquete());
  memset(p.get(), 0, sizeof(*p));
  p->cabecera = {sp::EVENTOS, origen, sp::SERVIDOR, secuencia, creado};
  p->eventos.n = 3;
  for (uint8_t i = 0; i < 3; i++) {
    p->eventos.eventos[i].idEvento = primero + i;
    p->eventos.eventos[i].tipo = sp::VIAJE;
  }
  return sp::empaquetar(*p, CLAVE, salida, sp::TAM_MAXIMO);
}

size_t listas(uint8_t *b, uint32_t desde, uint32_t hasta, uint32_t secuencia) {
  const size_t cuerpo = 12 + 2 * 16, total = sp::TAM_CABECERA + cuerpo + sp::FIRMA_ECDSA;
  memset(b, 0xEE, total);
  memcpy(b, "SP", 2);
  b[2] = sp::VERSION;
  b[3] = sp::LISTAS;
  memset(b + 4, 0, 2);
  memcpy(b + 8, &secuencia, 4);
  const uint32_t creado = 1000;
  memcpy(b + 12, &creado, 4);
  b[16] = cuerpo;
  b[17] = 0;
  memset(b + sp::TAM_CABECERA, 0, cuerpo);
  memcpy(b + sp::TAM_CABECERA, &desde, 4);
  memcpy(b + sp::TAM_CABECERA + 4, &hasta, 4);
  b[sp::TAM_CABECERA + 8] = 2;
  return total;
}

// Borra lo que crea esta prueba, por nombre: recorrer la carpeta con 500 archivos tarda ~90 s
void quitar(const char *ruta) {
  if (archivos.existe(ruta)) archivos.borrar(ruta);
}

void limpiar() {
  char r[48];
  for (uint32_t i = 0; i < PaquetesAjenos::MAXIMO; i++) {
    snprintf(r, sizeof(r), "/ajenos/%lu-%lu.bin", (unsigned long)(1 + i % 4), (unsigned long)(1000 + i));
    quitar(r);
  }
  quitar("/ajenos/9-1.bin");
  for (uint32_t i = 0; i < 60; i++) {
    snprintf(r, sizeof(r), "/firmados/%lu-%lu.bin", (unsigned long)(100 + i), (unsigned long)(101 + i));
    quitar(r);
  }
  for (const char *d : {"/ajenos.log", "/ajenos.tmp", "/firmados.log", "/firmados.tmp"}) quitar(d);
}

void probarAjenos() {
  Serial.println("Ajenos:");
  static uint8_t buf[sp::TAM_MAXIMO];
  PaquetesAjenos p(archivos);
  comprueba(p.iniciar() && p.cantidad() == 0, "carpeta vacía");

  uint32_t peor = 0, suma = 0;
  uint32_t t0 = micros();
  for (uint32_t i = 0; i < PaquetesAjenos::MAXIMO; i++) {
    const size_t n = eventos(buf, 1 + i % 4, 1000 + i, 10000 + i, 1 + i * 3);
    const uint32_t t = micros();
    if (!p.guardar(buf, n)) comprueba(false, "guardar");
    const uint32_t d = (micros() - t) / 1000;
    suma += d;
    if (d > peor) peor = d;
  }
  Serial.printf("  MEDIDA guardar 500 paquetes (%lu s en total): medio %lu ms, peor %lu ms\n",
                (unsigned long)(ms(t0) / 1000), (unsigned long)(suma / 500), (unsigned long)peor);
  comprueba(p.cantidad() == 500, "500 guardados");

  // "reiniciar": un objeto nuevo lee el diario, sin abrir los 500 paquetes ni recorrer la carpeta
  t0 = micros();
  PaquetesAjenos q(archivos);
  const bool ok = q.iniciar();
  Serial.printf("  MEDIDA arranque con 500 paquetes (leer el diario): %lu ms\n", (unsigned long)ms(t0));
  comprueba(ok && q.cantidad() == 500, "tras reiniciar hay 500");

  // guardar uno más con 500: borra exactamente el más antiguo
  const size_t n = eventos(buf, 9, 1, 20000, 1);
  t0 = micros();
  comprueba(q.guardar(buf, n) && q.cantidad() == 500, "guardar el 501.º borra uno y quedan 500");
  Serial.printf("  MEDIDA guardar con 500 (escribir uno y borrar el más antiguo): %lu ms\n", (unsigned long)ms(t0));
  comprueba(!archivos.existe("/ajenos/1-1000.bin"), "se borró el más antiguo (origen 1, secuencia 1000)");

  static InfoPaquete l[PaquetesAjenos::MAXIMO + 5];
  t0 = micros();
  const size_t k = q.listar(l, PaquetesAjenos::MAXIMO + 5);
  Serial.printf("  MEDIDA listar 500: %lu ms\n", (unsigned long)ms(t0));
  comprueba(k == 500 && l[0].creado <= l[1].creado && l[k - 1].creado == 20000, "listar: 500, el más nuevo el último");

  // el acuse del origen 1 cubre 124 paquetes: se borran de 2 en 2 para no bloquear (cada borrado cuesta 100-300 ms)
  t0 = micros();
  const size_t primeros = q.acusar(1, 1000000);
  const uint32_t unaVez = ms(t0);
  Serial.printf("  MEDIDA acusar el origen 1: %lu paquetes borrados en una llamada, %lu ms (quedan %lu por borrar)\n",
                (unsigned long)primeros, (unsigned long)unaVez, (unsigned long)q.confirmadosPorBorrar());
  comprueba(primeros == 2 && q.confirmadosPorBorrar() == 122, "la primera llamada borra 2 y quedan 122");
  t0 = micros();
  size_t borrados = primeros;
  uint32_t peorLlamada = unaVez;
  for (;;) {
    const uint32_t t1 = micros();
    const size_t k = q.borrarConfirmados(2);
    const uint32_t d = (micros() - t1) / 1000;
    if (d > peorLlamada) peorLlamada = d;
    if (k == 0) break;
    borrados += k;
  }
  Serial.printf("  MEDIDA borrar los 124 en total: %lu ms (la llamada más larga, %lu ms)\n", (unsigned long)(ms(t0) + unaVez), (unsigned long)peorLlamada);
  comprueba(borrados == 124 && q.cantidad() == 500 - borrados, "al final se borraron los 124 del origen 1");
  PaquetesAjenos z(archivos);
  comprueba(z.iniciar() && z.cantidad() == q.cantidad(), "tras reiniciar quedó lo mismo");
  const size_t leido = q.leer(9, 1, buf, sizeof(buf));
  comprueba(leido == n, "leer el último guardado");
}

void probarFirmados() {
  Serial.println("Firmados:");
  static uint8_t buf[sp::TAM_MAXIMO];
  PaquetesFirmados p(archivos);
  comprueba(p.iniciar() && p.cantidad() == 0, "carpeta vacía");
  uint32_t t0 = micros();
  for (uint32_t i = 0; i < 60; i++) {
    const size_t n = listas(buf, 100 + i, 101 + i, 300 + i);
    if (!p.guardar(buf, n)) comprueba(false, "guardar LISTAS");
  }
  Serial.printf("  MEDIDA guardar 60 LISTAS: %lu ms\n", (unsigned long)ms(t0));
  comprueba(p.cantidad() == 50, "quedan los 50 últimos");
  PaquetesFirmados q(archivos);
  static InfoPaquete l[60];
  comprueba(q.iniciar() && q.listar(l, 60) == 50 && l[0].secuencia == 310 && l[49].secuencia == 359, "tras reiniciar: secuencias 310 a 359");
  comprueba(q.leer(159, 160, buf, sizeof(buf)) > 0 && q.leer(100, 101, buf, sizeof(buf)) == 0, "leer uno guardado y uno que ya salió");
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
  Serial.printf("microSD a %lu Hz\n", (unsigned long)SD_HZ);
  limpiar();
  probarAjenos();
  probarFirmados();
  limpiar();
  Serial.println(fallo ? "RESULTADO: FALLO" : "RESULTADO: CORRECTO");
}

void loop() { delay(1000); }
