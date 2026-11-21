/*
 * Prueba de AlmacenSyncBinario (M4/M15) sobre la microSD real: el SincronizadorV2 de verdad (firmas ECDSA y HMAC reales,
 * con la clave de PRUEBA de test_host/vectores_*.h) sobre un CanalFalso y todo el almacenamiento binario. Mide cuánto
 * tarda cada llamada a atender() (que NO debe escribir en la microSD) y cada paso() (una operación).
 *
 * Cargar:  pio run -e prueba_almacen_sync -t upload      Ver:  pio device monitor
 * Usa la carpeta /m4sync de la microSD: no toca los archivos del validador.
 * Termina escribiendo "RESULTADO: CORRECTO" o "RESULTADO: FALLO".
 */

#include <Arduino.h>
#include <ArchivosSD.h>
#include <CanalFalso.h>
#include <SD.h>
#include <SPI.h>

#include <memory>

#include "../test_host/vectores_listas.h"
#include "../test_host/vectores_sync.h"
#include "validador/almacen_sync_binario.h"

constexpr int8_t SD_SCK = 14, SD_MOSI = 13, SD_MISO = 27, SD_CS = 26;
#ifndef SD_HZ
#define SD_HZ 4000000
#endif
constexpr uint16_t MI_107 = 107;
SPIClass spiSD(HSPI);
ArchivosSD archivos("/m4sync");
bool fallo = false;

void comprueba(bool ok, const char *que) {
  Serial.printf("  %s %s\n", ok ? "ok   " : "FALLA", que);
  if (!ok) fallo = true;
}

uint32_t horaFalsa() { return HORA_SERVIDOR_PRUEBA; }

struct Sistema {
  EstadoPersistente estado;
  ColaEventos cola;
  PaquetesAjenos ajenos;
  ListaNegra lista;
  Recargas recargas;
  TarifasGuardadas tarifas;
  PaquetesFirmados firmados;
  AplicadorListas aplicador;
  AlmacenSyncBinario alm;
  Sistema()
      : estado(archivos), cola(archivos, estado), ajenos(archivos), lista(archivos), recargas(archivos), tarifas(archivos),
        firmados(archivos), aplicador(estado, lista, recargas, tarifas, firmados), alm(cola, estado, ajenos, aplicador, horaFalsa) {
    estado.cargar();
    cola.iniciar();
    ajenos.iniciar();
    lista.iniciar();
    recargas.iniciar();
    tarifas.iniciar();
    firmados.iniciar();
  }
};

void limpiar() {
  for (const char *r : {"/estado.bin", "/estado.tmp", "/estado.bak", "/eventos.bin", "/eventos.tmp", "/lista_negra.bin",
                        "/lista_negra.tmp", "/lista_negra.bak", "/urgentes.bin", "/urgentes.tmp", "/recargas.bin", "/recargas.tmp",
                        "/recargas.bak", "/tarifas.bin", "/tarifas.tmp", "/tarifas.bak", "/firmados.log", "/firmados.tmp",
                        "/ajenos.log", "/ajenos.tmp", "/firmados/200-205.bin", "/firmados/205-210.bin"})
    if (archivos.existe(r)) archivos.borrar(r);
  char n[48];
  for (uint32_t s = 1; s <= 3; s++) {
    snprintf(n, sizeof(n), "/ajenos/105-%lu.bin", (unsigned long)s);
    if (archivos.existe(n)) archivos.borrar(n);
  }
}

// Un EVENTOS del validador 105 (HMAC) con eventos desde `primero`
size_t eventosAjenos(uint8_t *salida, uint32_t secuencia, uint32_t primero) {
  static const uint8_t CLAVE_105[32] = {1, 0, 5};
  static sp::Paquete p;
  memset(&p, 0, sizeof(p));
  p.cabecera = {sp::EVENTOS, 105, sp::SERVIDOR, secuencia, HORA_SERVIDOR_PRUEBA};
  p.eventos.n = 3;
  for (uint8_t i = 0; i < 3; i++) {
    p.eventos.eventos[i].idEvento = primero + i;
    p.eventos.eventos[i].tipo = sp::VIAJE;
  }
  return sp::empaquetar(p, CLAVE_105, salida, sp::TAM_MAXIMO);
}

uint32_t peorAtender = 0, peorPaso = 0;
uint32_t ms = 1000;

template <size_t N>
void servidor(CanalFalso &canal, SincronizadorV2 &sinc, const char *nombre, const uint8_t (&d)[N]) {
  canal.inyectar(d, N);
  ms += 10;
  const uint32_t t0 = micros();
  sinc.atender(&canal, ms);
  const uint32_t t = (micros() - t0) / 1000;
  if (t > peorAtender) peorAtender = t;
  Serial.printf("  MEDIDA atender() con %-18s %lu ms\n", nombre, (unsigned long)t);
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
  {
    // OJO: SincronizadorV2 lleva sus búferes dentro (unos 10 KB) y la pila de loop() es de 8 KB: va en el montón
    // (en el validador, como variable global). En la pila esta misma prueba se reinicia con "stack overflow".
    Serial.printf("  sizeof(SincronizadorV2) = %u, sizeof(AlmacenSyncBinario) = %u\n", (unsigned)sizeof(SincronizadorV2),
                  (unsigned)sizeof(AlmacenSyncBinario));
    std::unique_ptr<Sistema> sistema(new Sistema);
    Sistema &s = *sistema;
    CanalFalso canal;
    ConfigSyncV2 c;
    c.validador = MI_107;
    c.claveHmac = CLAVE_HMAC_107;
    c.clavePublicaServidor = CLAVE_PUBLICA_PRUEBA;
    c.versionFirmware = 20261121;
    c.ahoraUnix = horaFalsa;
    std::unique_ptr<SincronizadorV2> sincP(new SincronizadorV2(s.alm, c));
    SincronizadorV2 &sinc = *sincP;

    // Estado de partida: 30 viajes sin acuse, versión de listas 200 y tres paquetes de otro bus
    for (int i = 0; i < 30; i++) {
      sp::Evento e;
      memset(&e, 0, sizeof(e));
      e.tipo = sp::VIAJE;
      e.fecha = HORA_SERVIDOR_PRUEBA;
      e.contador = sp::SIN_CONTADOR;
      s.cola.agregar(e);
    }
    s.estado.fijarVersiones(200, 0);
    static uint8_t buf[sp::TAM_MAXIMO];
    for (uint32_t k = 1; k <= 3; k++) {
      const size_t n = eventosAjenos(buf, k, 1 + (k - 1) * 3);
      s.ajenos.guardar(buf, n);
    }
    ms += 1000;
    sinc.atender(&canal, ms);  // el primer HOLA y el primer paquete de eventos
    canal.vaciar();

    Serial.println("Lo que manda el servidor llega por atender(), que no debe escribir en la microSD:");
    const uint32_t versionAntes = s.estado.estado().versionListas;
    servidor(canal, sinc, "ACUSE hasta 30", ACUSE_107_HASTA_30);
    servidor(canal, sinc, "ACUSE del 105", ACUSE_105_HASTA_12);
    servidor(canal, sinc, "LISTAS 200 -> 205", LISTAS_N1_200_205);
    servidor(canal, sinc, "LISTAS 205 -> 210", LISTAS_N2_205_210);
    servidor(canal, sinc, "TARIFAS A", TARIFAS_A);
    comprueba(s.estado.estado().versionListas == versionAntes && s.cola.ultimoAcusado() == 0, "nada se guardó todavía");
    comprueba(s.alm.ultimoAcusado() == 30 && s.alm.trabajosEnCola() == 3, "el acuse se recuerda y hay 3 trabajos en cola");

    Serial.println("paso() con el lector libre, una operación por llamada:");
    int pasos = 0;
    uint32_t total = 0;
    while (s.alm.trabajoPendiente() && pasos < 500) {
      const char *fase = s.aplicador.ocupado() ? s.aplicador.fase() : (s.alm.trabajosEnCola() ? "empezar trabajo" : "acuse/ajeno/incidencia");
      const uint32_t t0 = micros();
      const bool hizo = s.alm.paso(true);
      const uint32_t t = (micros() - t0) / 1000;
      if (!hizo) break;
      pasos++;
      total += t;
      if (t > peorPaso) peorPaso = t;
      if (t >= 200) Serial.printf("  MEDIDA paso %2d (%s) tardó %lu ms\n", pasos, fase, (unsigned long)t);
    }
    Serial.printf("  MEDIDA %d pasos, %lu ms en total, el más largo %lu ms\n", pasos, (unsigned long)total, (unsigned long)peorPaso);
    comprueba(s.estado.estado().versionListas == 210 && s.lista.bloqueada(2000) && s.lista.bloqueada(2001), "LISTAS aplicadas: versión 210");
    comprueba(s.estado.estado().versionTarifas == TARIFAS_A_VERSION, "TARIFAS aplicadas");
    comprueba(s.cola.ultimoAcusado() == 30, "acuse guardado");
    comprueba(s.alm.ajenos() == 0 && s.alm.cuenta().ajenosBorrados == 3, "paquetes del 105 confirmados y borrados");
    comprueba(s.alm.cuenta().fallos == 0, "sin fallos de escritura");
  }
  Serial.println("Tras reiniciar (objetos nuevos que leen la microSD):");
  {
    uint32_t t0 = micros();
    std::unique_ptr<Sistema> sistema(new Sistema);
    Sistema &s = *sistema;
    Serial.printf("  MEDIDA arrancar todo el almacenamiento: %lu ms\n", (unsigned long)((micros() - t0) / 1000));
    comprueba(s.estado.estado().versionListas == 210 && s.estado.estado().versionTarifas == TARIFAS_A_VERSION, "versiones guardadas");
    comprueba(s.cola.ultimoAcusado() == 30 && s.alm.ultimoEvento() == 30, "acuse y ids de eventos");
    comprueba(s.lista.bloqueada(2001) && !s.lista.bloqueada(1001), "lista negra intacta");
  }
  Serial.printf("  MEDIDA atender() más largo: %lu ms; paso() más largo: %lu ms\n", (unsigned long)peorAtender, (unsigned long)peorPaso);
  limpiar();
  Serial.println(fallo ? "RESULTADO: FALLO" : "RESULTADO: CORRECTO");
}

void loop() { delay(1000); }
