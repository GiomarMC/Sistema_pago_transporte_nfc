/*
 * Prueba de la lista negra binaria (M3-03) sobre la microSD real: mide los dos criterios del issue.
 *
 * Cargar:  pio run -e prueba_listanegra -t upload      Ver:  pio device monitor
 * Usa la carpeta /m3lista de la microSD: no toca los archivos del validador.
 *
 *   1. Con 50 000 identificadores, una consulta tarda menos de 20 ms.
 *   2. Aplicar 120 cambios tarda menos de 2 s y no bloquea el cobro más de 50 ms seguidos
 *      (el trabajo se hace por partes, una llamada a avanzar() por vuelta de loop()).
 * Termina escribiendo "RESULTADO: CUMPLE" o "RESULTADO: NO CUMPLE" con cada medida.
 */

#include <Arduino.h>
#include <ArchivosSD.h>
#include <Crc32.h>
#include <ListaNegra.h>
#include <SD.h>
#include <SPI.h>

constexpr int8_t SD_SCK = 14, SD_MOSI = 13, SD_MISO = 27, SD_CS = 26;
constexpr uint32_t N_IDS = 50000;
#ifndef SD_HZ
#define SD_HZ 4000000  // el reloj de SPI de la microSD; se cambia con -DSD_HZ=...
#endif
SPIClass spiSD(HSPI);
ArchivosSD archivos("/m3lista");

uint32_t idDe(uint32_t i) { return i * 7 + 3; }  // la lista de prueba: 3, 10, 17...

// Escribe /lista_negra.bin ya hecha (formato del encabezado de ListaNegra.h), sin cargarla en RAM
bool generar(uint32_t n) {
  auto f = archivos.abrir("/lista_negra.bin", Modo::NUEVO);
  if (!f) return false;
  uint32_t buf[256], crc = 0;
  for (uint32_t i = 0; i < n;) {
    const uint32_t k = min<uint32_t>(256, n - i);
    for (uint32_t j = 0; j < k; j++) buf[j] = idDe(i + j);
    if (!f->agregar(buf, k * 4)) return false;
    crc = crc32(buf, k * 4, crc);
    i += k;
  }
  const uint32_t pie[4] = {0x314E4C53, 1, n, crc};
  return f->agregar(pie, 16) && f->vaciar();
}

bool esperada(uint32_t id, bool tocada, bool bloqueadaTrasCambio) {
  if (tocada) return bloqueadaTrasCambio;
  return id >= 3 && (id - 3) % 7 == 0 && (id - 3) / 7 < N_IDS;
}


// Cuánto cuesta cada operación suelta de la microSD (para saber dónde se va el tiempo)
struct Estad {
  uint32_t n = 0, suma = 0, peor = 0;
  void anota(uint32_t us) { n++; suma += us; if (us > peor) peor = us; }
  void imprime(const char *nombre) const {
    Serial.printf("  %-34s n=%3lu media %6lu us, peor %7lu us\n", nombre, (unsigned long)n, (unsigned long)(n ? suma / n : 0), (unsigned long)peor);
  }
};

void microMedidas() {
  Serial.println("Microbancos de la microSD:");
  uint8_t buf[1024];
  memset(buf, 0xA5, sizeof(buf));
  {
    auto f = archivos.abrir("/lista_negra.bin", Modo::LECTURA);
    Estad aleatoria, secuencial;
    uint32_t semilla = 777;
    for (int i = 0; i < 200; i++) {
      semilla = semilla * 1664525u + 1013904223u;
      const size_t pos = ((semilla >> 8) % (N_IDS * 4 / 512)) * 512;
      const uint32_t t0 = micros();
      f->leerEn(pos, buf, 512);
      aleatoria.anota(micros() - t0);
    }
    for (int i = 0; i < 100; i++) {
      const uint32_t t0 = micros();
      f->leerEn(i * 1024, buf, 1024);
      secuencial.anota(micros() - t0);
    }
    aleatoria.imprime("leer 512 B en posición al azar");
    secuencial.imprime("leer 1 KB seguido");
  }
  {
    auto f = archivos.abrir("/micro.tmp", Modo::NUEVO);
    Estad escribe, vacia16, vacia1;
    for (int tanda = 0; tanda < 3; tanda++) {
      for (int i = 0; i < 16; i++) {
        const uint32_t t0 = micros();
        f->agregar(buf, 1024);
        escribe.anota(micros() - t0);
      }
      const uint32_t t0 = micros();
      f->vaciar();
      vacia16.anota(micros() - t0);
    }
    f->agregar(buf, 16);
    const uint32_t t1 = micros();
    f->vaciar();
    vacia1.anota(micros() - t1);
    escribe.imprime("escribir 1 KB (sin vaciar)");
    vacia16.imprime("vaciar tras 16 KB");
    vacia1.imprime("vaciar tras 16 B");
  }
  {
    Estad ren, borra, abre;
    for (int i = 0; i < 5; i++) {
      uint32_t t0 = micros();
      archivos.renombrar("/micro.tmp", "/micro.bak");
      ren.anota(micros() - t0);
      t0 = micros();
      archivos.borrar("/micro.bak");
      borra.anota(micros() - t0);
      auto g = archivos.abrir("/micro.tmp", Modo::NUEVO);
      g.reset();
      t0 = micros();
      auto h = archivos.abrir("/lista_negra.bin", Modo::LECTURA);
      abre.anota(micros() - t0);
    }
    ren.imprime("renombrar");
    borra.imprime("borrar");
    abre.imprime("abrir para leer");
    archivos.borrar("/micro.tmp");
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.printf("PRUEBA LISTA NEGRA (M3-03), microSD a %lu MHz\n", (unsigned long)(SD_HZ / 1000000));
  spiSD.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  if (!SD.begin(SD_CS, spiSD, SD_HZ) || !archivos.iniciar()) {
    Serial.println("ERROR microSD no disponible");
    return;
  }
  archivos.borrar("/lista_negra.tmp");
  archivos.borrar("/lista_negra.bak");
  archivos.borrar("/urgentes.bin");

  uint32_t t = millis();
  if (!generar(N_IDS)) {
    Serial.println("ERROR no se pudo generar la lista");
    return;
  }
  Serial.printf("Generar %u ids en la microSD: %lu ms\n", (unsigned)N_IDS, (unsigned long)(millis() - t));

  microMedidas();

  ListaNegra lista(archivos);
  t = millis();
  const bool hay = lista.iniciar();
  const uint32_t msArranque = millis() - t;
  Serial.printf("Arranque (CRC, orden e índice de %u ids): %lu ms, hay=%d, cantidad=%u\n", (unsigned)lista.cantidad(),
                (unsigned long)msArranque, hay, (unsigned)lista.cantidad());
  if (!hay || lista.cantidad() != N_IDS) {
    Serial.println("RESULTADO: NO CUMPLE (la lista no cargó)");
    return;
  }

  // 1. Consultas: la mitad están en la lista y la otra mitad no
  uint32_t peor = 0, suma = 0, errores = 0;
  uint32_t cubos[6] = {};  // <2 ms, <5, <10, <20, <50, más
  constexpr int CONSULTAS = 2000;
  uint32_t semilla = 12345;
  for (int i = 0; i < CONSULTAS; i++) {
    semilla = semilla * 1664525u + 1013904223u;
    const uint32_t id = (i % 2 == 0) ? idDe((semilla >> 8) % N_IDS) : idDe((semilla >> 8) % N_IDS) + 1 + (semilla & 3);
    const uint32_t t0 = micros();
    const bool r = lista.bloqueada(id);
    const uint32_t us = micros() - t0;
    suma += us;
    if (us > peor) peor = us;
    cubos[us < 2000 ? 0 : us < 5000 ? 1 : us < 10000 ? 2 : us < 20000 ? 3 : us < 50000 ? 4 : 5]++;
    if (r != esperada(id, false, false)) errores++;
  }
  Serial.printf("Consultas: %d, media %lu us, peor %lu us, errores %lu\n", CONSULTAS, (unsigned long)(suma / CONSULTAS),
                (unsigned long)peor, (unsigned long)errores);
  Serial.printf("  reparto: <2ms %lu, <5ms %lu, <10ms %lu, <20ms %lu, <50ms %lu, más %lu\n", (unsigned long)cubos[0], (unsigned long)cubos[1],
                (unsigned long)cubos[2], (unsigned long)cubos[3], (unsigned long)cubos[4], (unsigned long)cubos[5]);
  const bool cumple1 = peor < 20000 && errores == 0;

  // 2. 120 cambios: 60 bajas de ids que están y 60 altas nuevas, procesados por partes
  static ListaNegra::Cambio cambios[120];
  for (uint32_t i = 0; i < 60; i++) cambios[i] = {idDe(i * 811), false};
  for (uint32_t i = 0; i < 60; i++) cambios[60 + i] = {5000000 + i * 13, true};
  const uint32_t t1 = millis();
  uint32_t llamadas = 0, peorLlamada = 0, peorCierre = 0, peorTrozo = 0, llamadasCierre = 0;
  uint32_t peorConsultaDurante = 0;
  bool ok = lista.aplicarCambios(cambios, 120);
  ListaNegra::Progreso p = ListaNegra::Progreso::EN_CURSO;
  while (ok && p == ListaNegra::Progreso::EN_CURSO) {
    const bool cierre = lista.proximoPasoBloquea();
    const uint32_t t0 = micros();
    p = lista.avanzar();
    const uint32_t us = micros() - t0;
    llamadas++;
    if (us > peorLlamada) peorLlamada = us;
    if (cierre) {
      llamadasCierre++;
      if (us > peorCierre) peorCierre = us;
    } else if (us > peorTrozo) {
      peorTrozo = us;
    }
    if (us > 50000) Serial.printf("  llamada > 50 ms nº %lu: %lu us%s\n", (unsigned long)llamadas, (unsigned long)us, cierre ? " (cierre)" : "");
    // entre llamadas el cobro sigue consultando la lista (la vieja) sin esperar a la mezcla
    const uint32_t t2 = micros();
    lista.bloqueada(idDe(llamadas * 37));
    const uint32_t uq = micros() - t2;
    if (uq > peorConsultaDurante) peorConsultaDurante = uq;
    yield();
  }
  const uint32_t msMezcla = millis() - t1;
  Serial.printf("Mezcla de 120 cambios: %lu ms en %lu llamadas, estado %d\n", (unsigned long)msMezcla, (unsigned long)llamadas, (int)p);
  Serial.printf("  peor llamada de trabajo (64 ids): %lu us | peor llamada de cierre (%lu llamadas): %lu us | consulta entre llamadas: %lu us\n",
                (unsigned long)peorTrozo, (unsigned long)llamadasCierre, (unsigned long)peorCierre, (unsigned long)peorConsultaDurante);
  const bool cumple2 = ok && p == ListaNegra::Progreso::TERMINADA && msMezcla < 2000 && peorLlamada < 50000;

  // Comprobar el resultado, y que sigue igual tras reiniciar
  uint32_t malos = 0;
  for (int i = 0; i < 120; i++)
    if (lista.bloqueada(cambios[i].id) != cambios[i].alta) malos++;
  ListaNegra otra(archivos);
  t = millis();
  otra.iniciar();
  const uint32_t msArranque2 = millis() - t;
  for (int i = 0; i < 120; i++)
    if (otra.bloqueada(cambios[i].id) != cambios[i].alta) malos++;
  Serial.printf("Tras la mezcla: cantidad %u (esperada %u), cambios mal aplicados %lu, rearranque %lu ms\n",
                (unsigned)lista.cantidad(), (unsigned)(N_IDS - 60 + 60), (unsigned long)malos, (unsigned long)msArranque2);
  const bool cumple3 = malos == 0 && lista.cantidad() == N_IDS && otra.cantidad() == N_IDS;

  Serial.printf("Criterio 1 (consulta < 20 ms con 50 000 ids): %s (peor %lu us)\n", cumple1 ? "CUMPLE" : "NO CUMPLE", (unsigned long)peor);
  Serial.printf("Criterio 2 (120 cambios < 2 s y sin bloquear > 50 ms): %s (%lu ms, peor llamada %lu us)\n", cumple2 ? "CUMPLE" : "NO CUMPLE",
                (unsigned long)msMezcla, (unsigned long)peorLlamada);
  Serial.printf("RESULTADO: %s\n", cumple1 && cumple2 && cumple3 ? "CUMPLE" : "NO CUMPLE");
}

void loop() { delay(1000); }
