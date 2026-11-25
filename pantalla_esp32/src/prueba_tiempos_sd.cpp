/*
 * Banco de tiempos de la microSD (#129): cuánto tarda cada operación de FAT que usa el almacenamiento del validador,
 * para saber qué hace que un guardado tarde 0,5-0,9 s y qué alternativa baja de 60 ms.
 *
 *   pio run -e prueba_tiempos_sd -t upload && pio device monitor
 *
 * Trabaja solo en la carpeta /banco129 (la crea y la borra al final); no toca los archivos del validador.
 * Cada medida se repite REPETICIONES veces y se da el mínimo, la mediana y el máximo en ms.
 */
#include <Arduino.h>
#include <SD.h>
#include <SPI.h>

#include <algorithm>
#include <vector>

namespace {

constexpr int8_t SD_SCK = 14, SD_MOSI = 13, SD_MISO = 27, SD_CS = 26;
constexpr int REPETICIONES = 12;
constexpr size_t TAM = 600;  // del orden de consumo.bin / estado.bin
SPIClass spiSD(HSPI);
uint8_t datos[4096];

struct Medida {
  const char *nombre;
  std::vector<uint32_t> us;
};
std::vector<Medida> medidas;

uint32_t *anotar(const char *nombre) {
  for (auto &m : medidas)
    if (!strcmp(m.nombre, nombre)) {
      m.us.push_back(0);
      return &m.us.back();
    }
  medidas.push_back({nombre, {0}});
  return &medidas.back().us.back();
}

// Cronometra `f` y lo anota con `nombre`
template <typename F>
bool medir(const char *nombre, F f) {
  const uint32_t t0 = micros();
  const bool ok = f();
  const uint32_t dt = micros() - t0;
  *anotar(nombre) = dt;
  if (!ok) Serial.printf("  FALLÓ: %s\n", nombre);
  return ok;
}

void informe() {
  Serial.println("\n# operación                                   mín    mediana   máx   (ms, n)");
  for (auto &m : medidas) {
    std::vector<uint32_t> v = m.us;
    std::sort(v.begin(), v.end());
    Serial.printf("# %-42s %7.1f %7.1f %7.1f  (%u)\n", m.nombre, v.front() / 1000.0, v[v.size() / 2] / 1000.0,
                  v.back() / 1000.0, (unsigned)v.size());
  }
}

bool escribirArchivo(const char *ruta, const char *modo, size_t n) {
  File f = SD.open(ruta, modo, true);
  if (!f) return false;
  const bool ok = f.write(datos, n) == n;
  f.flush();
  f.close();
  return ok;
}

// 1. Lo que hace hoy ArchivoAtomico::guardar(): .tmp nuevo, borrar .bak, .bin -> .bak, .tmp -> .bin
void guardadoAtomico() {
  medir("atomico: crear .tmp + escribir + flush", [] { return escribirArchivo("/banco129/a.tmp", FILE_WRITE, TAM); });
  medir("atomico: existe(.bak)", [] { return SD.exists("/banco129/a.bak") || true; });
  medir("atomico: borrar .bak", [] { return !SD.exists("/banco129/a.bak") || SD.remove("/banco129/a.bak"); });
  medir("atomico: renombrar .bin -> .bak",
        [] { return !SD.exists("/banco129/a.bin") || SD.rename("/banco129/a.bin", "/banco129/a.bak"); });
  medir("atomico: renombrar .tmp -> .bin", [] { return SD.rename("/banco129/a.tmp", "/banco129/a.bin"); });
}

// 2. Sobrescribir en su sitio un archivo que ya tiene el tamaño (dos ranuras A/B): sin tocar el directorio
void enSuSitio(int i) {
  const size_t ranura = (i % 2) * 1024;
  medir("en su sitio: abrir r+ + escribir + flush + cerrar", [ranura] {
    File f = SD.open("/banco129/ab.bin", "r+");
    if (!f || !f.seek(ranura)) return false;
    const bool ok = f.write(datos, TAM) == TAM;
    f.flush();
    f.close();
    return ok;
  });
}

// 3. Lo mismo con el archivo ya abierto (se abre una vez)
File abierto;
void enSuSitioAbierto(int i) {
  const size_t ranura = (i % 2) * 1024;
  medir("abierto: seek + escribir + flush", [ranura] {
    if (!abierto.seek(ranura)) return false;
    const bool ok = abierto.write(datos, TAM) == TAM;
    abierto.flush();
    return ok;
  });
}

// 4. Agregar un evento (64 B) al final de un archivo que crece, como la cola de eventos
void agregar() {
  medir("agregar 64 B + flush (abrir y cerrar)", [] {
    File f = SD.open("/banco129/cola.bin", FILE_APPEND, true);
    if (!f) return false;
    const bool ok = f.write(datos, 64) == 64;
    f.flush();
    f.close();
    return ok;
  });
}

void limpiar() {
  File dir = SD.open("/banco129");
  if (dir) {
    std::vector<String> nombres;
    for (File f = dir.openNextFile(); f; f = dir.openNextFile()) nombres.push_back(String("/banco129/") + f.name());
    dir.close();
    for (auto &n : nombres) SD.remove(n);
    SD.rmdir("/banco129");
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(500);
  for (size_t i = 0; i < sizeof(datos); i++) datos[i] = i * 7;
  spiSD.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  uint32_t hz = 20000000;
  if (!SD.begin(SD_CS, spiSD, hz)) {
    hz = 4000000;
    SD.end();
    spiSD.end();
    spiSD.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
    if (!SD.begin(SD_CS, spiSD, hz)) {
      Serial.println("# ERROR: la microSD no monta");
      return;
    }
  }
  Serial.printf("# microSD a %lu MHz, %llu MB, tipo %d\n", (unsigned long)(hz / 1000000), SD.cardSize() >> 20,
                SD.cardType());
  limpiar();
  SD.mkdir("/banco129");
  // El archivo de ranuras ya con su tamaño (2 KB) y la cola con algo dentro
  escribirArchivo("/banco129/ab.bin", FILE_WRITE, 2048);
  escribirArchivo("/banco129/cola.bin", FILE_WRITE, 64);
  abierto = SD.open("/banco129/ab.bin", "r+");

  for (int i = 0; i < REPETICIONES; i++) {
    guardadoAtomico();
    enSuSitio(i);
    enSuSitioAbierto(i);
    for (int k = 0; k < 8; k++) agregar();
    Serial.printf("# vuelta %d de %d\n", i + 1, REPETICIONES);
  }
  abierto.close();
  informe();
  limpiar();
  Serial.println("# FIN");
}

void loop() { delay(1000); }
