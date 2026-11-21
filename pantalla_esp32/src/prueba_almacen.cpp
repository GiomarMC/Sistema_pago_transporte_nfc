/*
 * Prueba del almacenamiento binario (M3-01 y M3-02) sobre la microSD real.
 *
 * Cargar:  pio run -e prueba_almacen -t upload      Ver:  pio device monitor
 * Usa la carpeta /m3prueba de la microSD: no toca los archivos del validador.
 *
 * Cada arranque:
 *   1. recupera el estado y la cola de eventos y escribe un resumen ("ARRANQUE ...");
 *   2. verifica que todos los eventos que quedan sean los que se escribieron (INTEGRIDAD);
 *   3. mide cuánto tarda un cobro (agregar un evento) y una compactación;
 *   4. entra en un bucle de cobros seguidos, escribiendo "C <id>" cuando cada uno queda
 *      guardado (es lo que en el validador se mostraría en pantalla).
 *
 * Para probar los cortes de energía: reiniciar o desenchufar la placa en pleno bucle. Al arrancar,
 * el ultimoId tiene que ser, como mínimo, el del último "C <id>" que se vio, la integridad tiene que
 * salir sin fallos y la secuencia tiene que seguir creciendo. test_host/cortes_almacen.py lo
 * hace 20 veces seguidas, reiniciando la placa por el puerto serie.
 *
 * Los eventos se generan a partir de su id, así se puede comprobar su contenido sin guardar copias.
 */

#include <Arduino.h>
#include <ArchivosSD.h>
#include <ColaEventos.h>
#include <EstadoPersistente.h>
#include <SD.h>
#include <SPI.h>

constexpr int8_t SD_SCK = 14, SD_MOSI = 13, SD_MISO = 27, SD_CS = 26;
SPIClass spiSD(HSPI);

ArchivosSD archivos("/m3prueba");
EstadoPersistente *estado = nullptr;
ColaEventos *cola = nullptr;
uint32_t ultimaSecuencia = 0;

void incidencia(const char *texto) { Serial.printf("INCIDENCIA %s\n", texto); }

// El evento número `id`: todo su contenido sale del id
sp::Evento eventoDe(uint32_t id) {
  sp::Evento e = {};
  e.idEvento = id;
  e.tipo = sp::VIAJE;
  e.largoUid = 4;
  e.operacion = id & 0xFFFF;
  e.fecha = 1795000000 + id;
  e.idTarjeta = 700 + id % 50;
  e.uid[0] = id;
  e.uid[1] = id >> 8;
  e.uid[2] = id >> 16;
  e.uid[3] = 0xA5;
  e.monto = -130;
  e.saldoFinal = 10000 - 130 * (int32_t)(id % 70);
  e.contador = id;
  snprintf(e.detalle, sizeof(e.detalle), "cobro %u", (unsigned)id);
  return e;
}

bool iguales(const sp::Evento &a, const sp::Evento &b) {
  uint8_t x[sp::TAM_EVENTO], y[sp::TAM_EVENTO];
  return sp::eventoABytes(a, x) && sp::eventoABytes(b, y) && memcmp(x, y, sizeof(x)) == 0;
}

void verificarIntegridad() {
  const uint32_t acusado = estado->estado().hastaAcusado;
  uint32_t verificados = 0, corruptos = 0, huecos = 0, esperado = acusado + 1;
  sp::Evento lote[30];
  uint32_t desde = acusado;
  const uint32_t t0 = millis();
  while (true) {
    const size_t n = cola->leerDesde(desde, lote, 30);
    if (n == 0) break;
    for (size_t i = 0; i < n; i++) {
      if (lote[i].idEvento != esperado) huecos++;
      if (!iguales(lote[i], eventoDe(lote[i].idEvento))) corruptos++;
      esperado = lote[i].idEvento + 1;
      verificados++;
    }
    desde = lote[n - 1].idEvento;
  }
  Serial.printf("INTEGRIDAD verificados %lu, corruptos %lu, huecos %lu, ultimo %lu (%lu ms) %s\n", (unsigned long)verificados,
                (unsigned long)corruptos, (unsigned long)huecos, (unsigned long)cola->ultimoId(), (unsigned long)(millis() - t0),
                corruptos == 0 && huecos == 0 && esperado - 1 == cola->ultimoId() ? "OK" : "FALLO");
}

void medirTiempos() {
  uint32_t minimo = 0xFFFFFFFF, maximo = 0, total = 0;
  const int N = 100;
  for (int i = 0; i < N; i++) {
    sp::Evento e = eventoDe(cola->siguienteId());
    const uint32_t t = micros();
    if (!cola->agregar(e)) {
      Serial.println("ERROR agregar fallo");
      return;
    }
    const uint32_t d = micros() - t;
    total += d;
    if (d < minimo) minimo = d;
    if (d > maximo) maximo = d;
  }
  Serial.printf("TIEMPOS cobro (agregar+flush) en %d: min %lu us, medio %lu us, max %lu us\n", N, (unsigned long)minimo,
                (unsigned long)(total / N), (unsigned long)maximo);
  const uint32_t t = micros();
  const uint32_t antes = cola->registros();
  cola->acusarHasta(cola->ultimoId() > 5 ? cola->ultimoId() - 5 : 0);
  Serial.printf("TIEMPOS acuse + compactacion: %lu ms (registros %lu -> %lu)\n", (unsigned long)((micros() - t) / 1000),
                (unsigned long)antes, (unsigned long)cola->registros());
  const uint32_t t2 = micros();
  const uint32_t s = estado->siguienteSecuencia();
  Serial.printf("TIEMPOS siguienteSecuencia: %lu us (secuencia %lu)\n", (unsigned long)(micros() - t2), (unsigned long)s);
  ultimaSecuencia = s;
}

// Con la letra B por el puerto serie se borran los datos de la prueba y se reinicia
void borrarTodo() {
  for (const char *f : {"/eventos.bin", "/eventos.tmp", "/estado.bin", "/estado.tmp", "/estado.bak"}) archivos.borrar(f);
  Serial.println("BORRADO");
  delay(100);
  ESP.restart();
}

void setup() {
  Serial.begin(115200);
  delay(300);
  spiSD.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  if (!SD.begin(SD_CS, spiSD, 4000000) || !archivos.iniciar()) {
    Serial.println("ERROR microSD no disponible");
    return;
  }
  static EstadoPersistente est(archivos);
  static ColaEventos c(archivos, est, incidencia);
  estado = &est;
  cola = &c;

  const uint32_t t0 = millis();
  const bool habia = est.cargar();
  const bool ok = c.iniciar();
  Serial.printf("ARRANQUE estado %s, cola %s en %lu ms: registros %lu, ultimoId %lu, acusado %lu, secuenciaReservada %lu, "
                "generacion %lu\n",
                habia ? "recuperado" : "nuevo", ok ? "OK" : "FALLO", (unsigned long)(millis() - t0), (unsigned long)c.registros(),
                (unsigned long)c.ultimoId(), (unsigned long)est.estado().hastaAcusado, (unsigned long)est.estado().secuenciaPaquetes,
                (unsigned long)est.generacion());
  if (!ok) return;
  verificarIntegridad();
  medirTiempos();
  Serial.println("LISTO");
}

void loop() {
  if (Serial.available() && Serial.read() == 'B') borrarTodo();
  if (!cola) {
    delay(1000);
    return;
  }
  static uint32_t n = 0;
  sp::Evento e = eventoDe(cola->siguienteId());
  if (cola->agregar(e)) {
    Serial.printf("C %lu\n", (unsigned long)e.idEvento);  // el cobro ya está en la microSD
  } else {
    Serial.println("ERROR cobro no guardado");
  }
  if (++n % 25 == 0) {
    cola->acusarHasta(cola->ultimoId() > 4 ? cola->ultimoId() - 4 : 0);
    ultimaSecuencia = estado->siguienteSecuencia();
    Serial.printf("S %lu\n", (unsigned long)ultimaSecuencia);
  }
  delay(40);
}
