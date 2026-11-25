/*
 * Prueba de AplicadorListas (M4-02) sobre la microSD real: aplica paquetes LISTAS y TARIFAS firmados (los de
 * test_host/vectores_listas.h, con la clave de PRUEBA), comprueba el resultado y mide cuánto bloquea cada uno.
 *
 * Cargar:  pio run -e prueba_listas -t upload      Ver:  pio device monitor
 * Usa la carpeta /m4listas de la microSD: no toca los archivos del validador.
 * Termina escribiendo "RESULTADO: CORRECTO" o "RESULTADO: FALLO".
 */

#include <Arduino.h>
#include <AplicadorListas.h>
#include <ArchivosSD.h>
#include <SD.h>
#include <SPI.h>

#include <memory>

#include "../test_host/vectores_listas.h"
#include "../test_host/vectores_sync.h"

constexpr int8_t SD_SCK = 14, SD_MOSI = 13, SD_MISO = 27, SD_CS = 26;
#ifndef SD_HZ
#define SD_HZ 4000000
#endif
SPIClass spiSD(HSPI);
ArchivosSD archivos("/m4listas");
bool fallo = false;
uint32_t peorMs = 0;

void comprueba(bool ok, const char *que) {
  Serial.printf("  %s %s\n", ok ? "ok   " : "FALLA", que);
  if (!ok) fallo = true;
}

const uint8_t *sinClaves(uint16_t) { return nullptr; }

struct Sistema {
  EstadoPersistente estado;
  ListaNegra lista;
  Recargas recargas;
  TarifasGuardadas tarifas;
  PaquetesFirmados firmados;
  AplicadorListas ap;
  Sistema()
      : estado(archivos), lista(archivos), recargas(archivos), tarifas(archivos), firmados(archivos),
        ap(estado, lista, recargas, tarifas, firmados) {
    estado.cargar();
    lista.iniciar();
    recargas.iniciar();
    tarifas.iniciar();
    firmados.iniciar();
  }
};

// Verifica la firma del paquete (ECDSA) y lo aplica; imprime lo que tardó cada cosa
template <size_t N>
AplicadorListas::Resultado aplicar(Sistema &s, const char *nombre, const uint8_t (&datos)[N]) {
  static sp::Paquete p;
  sp::Motivo m;
  uint32_t t0 = micros();
  const bool valido = sp::desempaquetar(datos, N, sinClaves, CLAVE_PUBLICA_PRUEBA, p, &m);
  const uint32_t verificar = (micros() - t0) / 1000;
  if (!valido) {
    Serial.printf("  FALLA %s: no se pudo verificar (%s)\n", nombre, sp::describir(m));
    fallo = true;
    return AplicadorListas::Resultado::INVALIDA;
  }
  t0 = micros();
  const auto r = p.cabecera.tipo == sp::LISTAS ? s.ap.listas(p.listas, datos, N) : s.ap.tarifas(p.tarifas);
  const uint32_t aplicar = (micros() - t0) / 1000;
  if (aplicar > peorMs) peorMs = aplicar;
  Serial.printf("  MEDIDA %-24s resultado %d: verificar firma %lu ms, aplicar %lu ms\n", nombre, (int)r, (unsigned long)verificar,
                (unsigned long)aplicar);
  return r;
}

void limpiar() {
  for (const char *r : {"/estado.bin", "/estado.tmp", "/estado.bak", "/estado.ab", "/estado.abn", "/lista_negra.bin", "/lista_negra.tmp", "/lista_negra.bak",
                        "/urgentes.bin", "/urgentes.tmp", "/recargas.bin", "/recargas.tmp", "/recargas.bak", "/recargas.ab", "/recargas.abn", "/tarifas.bin",
                        "/tarifas.tmp", "/tarifas.bak", "/tarifas.ab", "/tarifas.abn", "/firmados.log", "/firmados.tmp"})
    if (archivos.existe(r)) archivos.borrar(r);
  // los LISTAS que esta prueba guarda en /firmados, por nombre (recorrer la carpeta es lento en la microSD)
  const uint32_t pares[][2] = {{0, FOTO_INTERMEDIA_1}, {FOTO_INTERMEDIA_1, FOTO_INTERMEDIA_2}, {FOTO_INTERMEDIA_2, 200},
                               {200, 205}, {205, 212}, {211, 212}};
  char n[48];
  for (const auto &p : pares) {
    snprintf(n, sizeof(n), "/firmados/%lu-%lu.bin", (unsigned long)p[0], (unsigned long)p[1]);
    if (archivos.existe(n)) archivos.borrar(n);
  }
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
  using R = AplicadorListas::Resultado;
  {
    Sistema s;
    Serial.println("Foto completa en 3 paquetes, cambios en orden y urgentes:");
    comprueba(aplicar(s, "foto 1 (0 -> inter.)", LISTAS_FOTO_1) == R::APLICADA, "foto 1 aplicada");
    comprueba(aplicar(s, "foto 3 fuera de orden", LISTAS_FOTO_3) == R::SALTO, "la 3 sin la 2: se descarta");
    comprueba(aplicar(s, "foto 2", LISTAS_FOTO_2) == R::APLICADA, "foto 2 aplicada");
    comprueba(aplicar(s, "foto 3 (-> 200)", LISTAS_FOTO_3) == R::APLICADA && s.estado.estado().versionListas == 200, "foto 3: versión 200");
    comprueba(s.lista.bloqueada(1000) && s.lista.bloqueada(1005) && !s.lista.bloqueada(2000), "lista negra: 1000 a 1005");
    comprueba(aplicar(s, "urgente 3000", LISTAS_U_3000_212) == R::URGENTE && s.lista.bloqueada(3000), "urgente: 3000 bloqueada");
    comprueba(aplicar(s, "cambios 200 -> 205", LISTAS_N1_200_205) == R::APLICADA && s.estado.estado().versionListas == 205, "cambios: versión 205");
    comprueba(aplicar(s, "cambios 205 -> 212", LISTAS_N3_205_212) == R::APLICADA && s.lista.cantidadUrgentes() == 0, "recoge el urgente 3000");
    comprueba(s.lista.bloqueada(3000) && !s.lista.bloqueada(1001), "3000 sigue bloqueada, 1001 desbloqueada");
    comprueba(aplicar(s, "tarifas A", TARIFAS_A) == R::APLICADA && aplicar(s, "tarifas B", TARIFAS_B) == R::APLICADA, "tarifas A y B");
    comprueba(aplicar(s, "tarifas viejas", TARIFAS_VIEJA) == R::REPETIDA, "tarifas viejas descartadas");
  }
  Serial.println("Tras reiniciar (objetos nuevos que leen la microSD):");
  {
    uint32_t t0 = micros();
    Sistema s;
    Serial.printf("  MEDIDA arrancar todo el almacenamiento: %lu ms\n", (unsigned long)((micros() - t0) / 1000));
    comprueba(s.estado.estado().versionListas == 212 && s.estado.estado().versionTarifas == TARIFAS_B_VERSION, "versiones 212 y la de tarifas B");
    comprueba(s.lista.bloqueada(3000) && s.lista.bloqueada(1005) && s.lista.bloqueada(2000) && !s.lista.bloqueada(1001), "lista negra intacta");
    uint32_t hasta;
    int32_t total;
    s.recargas.pendientes(60, 0, total, hasta);
    comprueba(total == 100 && hasta == 1, "recargas: cuenta 60, 100 hasta la seq 1");
    sp::Tarifa t;
    comprueba(s.tarifas.tarifa(1, t) && t.precio == 150, "tarifa 1 = 150");
    comprueba(s.firmados.cantidad() >= 5, "paquetes firmados guardados para otros buses");
  }
  Serial.printf("  MEDIDA el paquete que más tardó en aplicarse: %lu ms\n", (unsigned long)peorMs);
  limpiar();
  Serial.println(fallo ? "RESULTADO: FALLO" : "RESULTADO: CORRECTO");
}

void loop() { delay(1000); }
