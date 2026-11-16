// Comprueba en la ESP32 que los paquetes C1 en C++ coinciden byte a byte con la
// biblioteca Python (vectores.h, generado desde docs/contratos/vectores_c1.json).
// Ejecutar:  pio test -e validador -f test_protocolo
#include <Arduino.h>
#include <Fragmentos.h>
#include <ProtocoloSP.h>
#include <string.h>
#include <unity.h>

#include "vectores.h"

using namespace vectores;

constexpr uint32_t CREADO = 1793000000;

// Los Paquete miden ~2.3 KB: estáticos para no llenar la pila del loop
static sp::Paquete paq;
static uint8_t buf[sp::TAM_MAXIMO];

const uint8_t *claveDe(uint16_t validador) {
  return validador == VALIDADOR ? CLAVE_HMAC : nullptr;
}

sp::Motivo leer(const uint8_t *datos, size_t largo) {
  sp::Motivo m;
  sp::desempaquetar(datos, largo, claveDe, CLAVE_PUBLICA, paq, &m);
  return m;
}

const Valido &vector(const char *nombre) {
  for (size_t i = 0; i < N_VALIDOS; i++)
    if (strcmp(VALIDOS[i].nombre, nombre) == 0) return VALIDOS[i];
  TEST_FAIL_MESSAGE(nombre);
  return VALIDOS[0];
}

// Igual que _viaje(i) en generar_vectores_c1.py
sp::Evento viaje(uint32_t i) {
  sp::Evento e = {};
  e.idEvento = i;
  e.tipo = sp::VIAJE;
  e.largoUid = 7;
  const uint8_t uid[7] = {0x04, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF};
  memcpy(e.uid, uid, 7);
  e.operacion = i;
  e.fecha = CREADO + 60 * i;
  e.idTarjeta = 7;
  e.monto = -80;
  e.saldoFinal = 1000 - 80 * int32_t(i);
  e.contador = 40 + i;
  return e;
}

// --- Vectores de Python ---------------------------------------------------------------

void test_lee_y_recodifica_todos_los_validos() {
  for (size_t i = 0; i < N_VALIDOS; i++) {
    const Valido &v = VALIDOS[i];
    TEST_ASSERT_EQUAL_STRING_MESSAGE("ok", sp::describir(leer(v.datos, v.largo)), v.nombre);
    TEST_ASSERT_EQUAL_HEX8_MESSAGE(v.tipo, paq.cabecera.tipo, v.nombre);
    TEST_ASSERT_EQUAL_UINT16_MESSAGE(v.origen, paq.cabecera.origen, v.nombre);
    TEST_ASSERT_EQUAL_UINT16_MESSAGE(v.destino, paq.cabecera.destino, v.nombre);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(v.secuencia, paq.cabecera.secuencia, v.nombre);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(v.creado, paq.cabecera.creado, v.nombre);
    // Sin firma, los bytes deben ser los mismos; los del bus también con firma
    size_t firma = v.origen == sp::SERVIDOR ? sp::FIRMA_ECDSA : sp::FIRMA_HMAC;
    TEST_ASSERT_EQUAL_MESSAGE(v.largo - firma, sp::codificar(paq, buf, sizeof(buf)), v.nombre);
    TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(v.datos, buf, v.largo - firma, v.nombre);
    if (v.origen != sp::SERVIDOR) {
      TEST_ASSERT_EQUAL_MESSAGE(v.largo, sp::empaquetar(paq, CLAVE_HMAC, buf, sizeof(buf)),
                                v.nombre);
      TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(v.datos, buf, v.largo, v.nombre);
    }
  }
}

void test_rechaza_los_invalidos_con_su_motivo() {
  for (size_t i = 0; i < N_INVALIDOS; i++) {
    const Invalido &v = INVALIDOS[i];
    TEST_ASSERT_EQUAL_STRING(v.motivo, sp::describir(leer(v.datos, v.largo)));
  }
}

// --- Lo que emite el validador, armado desde cero -------------------------------------

void test_arma_eventos_1() {
  paq.cabecera = {sp::EVENTOS, VALIDADOR, sp::SERVIDOR, 1, CREADO};
  paq.eventos.n = 1;
  paq.eventos.eventos[0] = viaje(1);
  const Valido &v = vector("eventos_1");
  TEST_ASSERT_EQUAL(v.largo, sp::empaquetar(paq, CLAVE_HMAC, buf, sizeof(buf)));
  TEST_ASSERT_EQUAL_HEX8_ARRAY(v.datos, buf, v.largo);
}

void test_arma_hola() {
  paq.cabecera = {sp::HOLA, VALIDADOR, sp::SERVIDOR, 3, CREADO};
  paq.hola = {103, CREADO - 86400, 30, 3, 1, sp::WIFI, -40, 12345, 6789, 20261106};
  const Valido &v = vector("hola");
  TEST_ASSERT_EQUAL(v.largo, sp::empaquetar(paq, CLAVE_HMAC, buf, sizeof(buf)));
  TEST_ASSERT_EQUAL_HEX8_ARRAY(v.datos, buf, v.largo);
}

// --- Campos leídos ----------------------------------------------------------------------

void test_campos_de_eventos_30() {
  const Valido &v = vector("eventos_30");
  TEST_ASSERT_TRUE(sp::desempaquetar(v.datos, v.largo, claveDe, CLAVE_PUBLICA, paq));
  TEST_ASSERT_EQUAL_UINT8(30, paq.eventos.n);
  const sp::Evento &incidencia = paq.eventos.eventos[28];
  TEST_ASSERT_EQUAL_UINT8(sp::INCIDENCIA, incidencia.tipo);
  TEST_ASSERT_EQUAL_UINT8(0, incidencia.largoUid);
  TEST_ASSERT_EQUAL_STRING("lista firma invalida", incidencia.detalle);  // 20 caracteres
  const sp::Evento &fraude = paq.eventos.eventos[29];
  TEST_ASSERT_EQUAL_UINT8(sp::FRAUDE, fraude.tipo);
  TEST_ASSERT_EQUAL_UINT8(4, fraude.largoUid);
  TEST_ASSERT_EQUAL_UINT32(sp::SIN_CONTADOR, fraude.contador);
  TEST_ASSERT_EQUAL_UINT32(3, fraude.recargaHasta);
  TEST_ASSERT_EQUAL_STRING("contador repetido", fraude.detalle);
  TEST_ASSERT_EQUAL_INT32(-80, paq.eventos.eventos[0].monto);
}

void test_campos_de_listas_y_tarifas() {
  const Valido &l = vector("listas_normal");
  TEST_ASSERT_TRUE(sp::desempaquetar(l.datos, l.largo, claveDe, CLAVE_PUBLICA, paq));
  TEST_ASSERT_EQUAL_UINT32(100, paq.listas.versionDesde);
  TEST_ASSERT_EQUAL_UINT32(103, paq.listas.versionHasta);
  TEST_ASSERT_FALSE(paq.listas.urgente);
  TEST_ASSERT_EQUAL_UINT8(3, paq.listas.n);
  const sp::EntradaLista &recarga = paq.listas.entradas[2];
  TEST_ASSERT_EQUAL_UINT8(sp::RECARGA, recarga.tipo);
  TEST_ASSERT_EQUAL_UINT32(12, recarga.a);
  TEST_ASSERT_EQUAL_UINT32(3, recarga.b);
  TEST_ASSERT_EQUAL_UINT32(2000, recarga.c);

  const Valido &u = vector("listas_urgente");
  TEST_ASSERT_TRUE(sp::desempaquetar(u.datos, u.largo, claveDe, CLAVE_PUBLICA, paq));
  TEST_ASSERT_TRUE(paq.listas.urgente);
  TEST_ASSERT_EQUAL_UINT16(sp::TODOS, paq.cabecera.destino);

  const Valido &t = vector("tarifas");
  TEST_ASSERT_TRUE(sp::desempaquetar(t.datos, t.largo, claveDe, CLAVE_PUBLICA, paq));
  TEST_ASSERT_EQUAL_UINT8(3, paq.tarifas.n);
  TEST_ASSERT_EQUAL_STRING("Estudiante", paq.tarifas.tarifas[1].nombre);
  TEST_ASSERT_EQUAL_UINT32(50, paq.tarifas.tarifas[1].precio);
}

// --- Rechazos que no están en los vectores ----------------------------------------------

void test_validador_desconocido() {
  const Valido &v = vector("hola");
  sp::Motivo m;
  sp::desempaquetar(v.datos, v.largo, [](uint16_t) -> const uint8_t * { return nullptr; },
                    CLAVE_PUBLICA, paq, &m);
  TEST_ASSERT_EQUAL_STRING("validador", sp::describir(m));
}

void test_bus_no_puede_mandar_listas() {
  const Valido &v = vector("hola");
  memcpy(buf, v.datos, v.largo);
  buf[3] = sp::LISTAS;
  TEST_ASSERT_EQUAL_STRING("origen", sp::describir(leer(buf, v.largo)));
  // Y la biblioteca no lo arma
  paq.cabecera = {sp::LISTAS, VALIDADOR, sp::SERVIDOR, 1, CREADO};
  paq.listas.n = 0;
  TEST_ASSERT_EQUAL(0, sp::empaquetar(paq, CLAVE_HMAC, buf, sizeof(buf)));
}

void test_tipo_desconocido_y_cortado() {
  const Valido &v = vector("hola");
  memcpy(buf, v.datos, v.largo);
  buf[3] = 0x05;
  TEST_ASSERT_EQUAL_STRING("tipo", sp::describir(leer(buf, v.largo)));
  TEST_ASSERT_EQUAL_STRING("largo", sp::describir(leer(v.datos, 10)));
  TEST_ASSERT_EQUAL_STRING("largo", sp::describir(leer(v.datos, v.largo - 1)));
}

void test_lista_con_firma_de_otra_clave() {
  const Valido &v = vector("listas_urgente");
  memcpy(buf, v.datos, v.largo);
  buf[v.largo - 40] ^= 0x01;  // un bit de la s
  TEST_ASSERT_EQUAL_STRING("firma", sp::describir(leer(buf, v.largo)));
}

void test_limites_al_armar() {
  paq.cabecera = {sp::EVENTOS, VALIDADOR, sp::SERVIDOR, 1, CREADO};
  paq.eventos.n = 0;
  TEST_ASSERT_EQUAL(0, sp::empaquetar(paq, CLAVE_HMAC, buf, sizeof(buf)));
  paq.eventos.n = 31;
  TEST_ASSERT_EQUAL(0, sp::empaquetar(paq, CLAVE_HMAC, buf, sizeof(buf)));
  paq.eventos.n = 1;
  paq.eventos.eventos[0] = viaje(1);
  TEST_ASSERT_EQUAL(0, sp::empaquetar(paq, CLAVE_HMAC, buf, 50));  // no cabe
}

void test_medir_paquetes_concatenados() {
  const Valido &a = vector("hola"), &b = vector("acuse");
  memcpy(buf, a.datos, a.largo);
  memcpy(buf + a.largo, b.datos, b.largo);
  TEST_ASSERT_EQUAL(a.largo, sp::medirPaquete(buf, a.largo + b.largo));
  TEST_ASSERT_EQUAL(b.largo, sp::medirPaquete(buf + a.largo, b.largo));
  TEST_ASSERT_EQUAL(0, sp::medirPaquete(buf + a.largo, b.largo - 1));  // cortado
  TEST_ASSERT_EQUAL(0, sp::medirPaquete(buf + 1, a.largo));            // sin magia
}

// --- Tiempo de la verificación ECDSA (criterio: < 100 ms) -------------------------------
// La ESP32 clásica no acelera curvas elípticas: con micro-ecc a -O3 tarda ~67 ms (mbedTLS,
// ~290 ms). No llega a 60 ms, así que la verificación no puede ir dentro de loop(): habrá
// que hacerla en una tarea aparte para que no retrase la lectura de tarjetas.
void test_tiempo_ecdsa() {
  const Valido &v = vector("acuse");
  const size_t firmado = v.largo - sp::FIRMA_ECDSA;
  uint32_t t0 = micros();
  bool ok = sp::ecdsaValida(CLAVE_PUBLICA, v.datos, firmado, v.datos + firmado);
  uint32_t primera = micros() - t0;
  TEST_ASSERT_TRUE(ok);
  uint32_t peor = 0, total = 0;
  for (int i = 0; i < 5; i++) {
    t0 = micros();
    TEST_ASSERT_TRUE(sp::ecdsaValida(CLAVE_PUBLICA, v.datos, firmado, v.datos + firmado));
    uint32_t t = micros() - t0;
    total += t;
    if (t > peor) peor = t;
  }
  char msg[128];
  snprintf(msg, sizeof(msg),
           "ECDSA a %lu MHz: primera %lu us (con carga de clave), media %lu us, peor %lu us",
           (unsigned long)getCpuFrequencyMhz(), (unsigned long)primera,
           (unsigned long)(total / 5), (unsigned long)peor);
  TEST_MESSAGE(msg);
  TEST_ASSERT_LESS_THAN_UINT32(100000, peor);
  TEST_ASSERT_LESS_THAN_UINT32(100000, primera);
}

// --- Fragmentos ----------------------------------------------------------------------------

static uint8_t mensaje[sp::TAM_MAXIMO];
static uint8_t tramas[sp::MAX_FRAGMENTOS][sp::TAM_TRAMA_MAXIMO];
static size_t largos[sp::MAX_FRAGMENTOS];
static sp::Reensamblador reens;

uint8_t partir(size_t largo, uint32_t id) {
  for (size_t i = 0; i < largo; i++) mensaje[i] = uint8_t(i * 7 + id);
  uint8_t n = sp::contarFragmentos(largo);
  for (uint8_t i = 0; i < n; i++)
    largos[i] = sp::armarFragmento(mensaje, largo, VALIDADOR, id, i, tramas[i]);
  return n;
}

void test_fragmentos_ida_y_vuelta() {
  const size_t tamanos[] = {1, 220, 221, 2048};
  const uint8_t esperados[] = {1, 1, 2, 10};
  for (size_t k = 0; k < 4; k++) {
    uint8_t n = partir(tamanos[k], k + 1);
    TEST_ASSERT_EQUAL_UINT8(esperados[k], n);
    for (uint8_t i = 0; i < n; i++) TEST_ASSERT_LESS_OR_EQUAL(sp::TAM_TRAMA_MAXIMO, largos[i]);
    // Llegan en desorden: el último primero
    for (int i = n - 1; i >= 0; i--) {
      auto r = reens.recibir(tramas[i], largos[i], 1000);
      TEST_ASSERT_EQUAL(int(i == 0 ? sp::Reensamblador::Resultado::COMPLETO
                                   : sp::Reensamblador::Resultado::INCOMPLETO),
                        int(r));
    }
    TEST_ASSERT_EQUAL(tamanos[k], reens.largo());
    TEST_ASSERT_EQUAL_UINT16(VALIDADOR, reens.emisor());
    TEST_ASSERT_EQUAL_HEX8_ARRAY(mensaje, reens.mensaje(), tamanos[k]);
  }
  TEST_ASSERT_EQUAL_UINT8(0, sp::contarFragmentos(2201));  // más de 10 fragmentos
}

void test_fragmento_perdido_descarta_el_mensaje() {
  uint32_t antes = reens.descartados();
  uint8_t n = partir(1000, 50);
  for (uint8_t i = 0; i < n; i++)
    if (i != 2) reens.recibir(tramas[i], largos[i], 5000);
  TEST_ASSERT_TRUE(reens.enCurso());
  reens.revisar(5000 + sp::ESPERA_FRAGMENTOS_MS + 1);
  TEST_ASSERT_FALSE(reens.enCurso());
  TEST_ASSERT_EQUAL_UINT32(antes + 1, reens.descartados());
  // El que faltaba llega tarde: no completa nada
  TEST_ASSERT_EQUAL(int(sp::Reensamblador::Resultado::INCOMPLETO),
                    int(reens.recibir(tramas[2], largos[2], 9000)));
}

void test_otro_mensaje_descarta_el_anterior() {
  reens.revisar(100000);  // limpia lo que quedó del test anterior
  uint32_t antes = reens.descartados();
  partir(500, 60);
  reens.recibir(tramas[0], largos[0], 20000);
  uint8_t n = partir(300, 61);
  for (uint8_t i = 0; i < n; i++) reens.recibir(tramas[i], largos[i], 20100);
  TEST_ASSERT_EQUAL_UINT32(antes + 1, reens.descartados());
  TEST_ASSERT_EQUAL(300, reens.largo());
}

void test_trama_invalida() {
  partir(500, 70);
  uint8_t t[sp::TAM_TRAMA_MAXIMO];
  memcpy(t, tramas[0], largos[0]);
  t[0] = 'X';
  TEST_ASSERT_EQUAL(int(sp::Reensamblador::Resultado::INVALIDA),
                    int(reens.recibir(t, largos[0], 30000)));
  memcpy(t, tramas[0], largos[0]);
  t[12] = 100;  // un fragmento que no es el último debe llevar 220 B
  TEST_ASSERT_EQUAL(int(sp::Reensamblador::Resultado::INVALIDA),
                    int(reens.recibir(t, 13 + 100, 30000)));
}

void setup() {
  delay(2000);
  UNITY_BEGIN();
  RUN_TEST(test_lee_y_recodifica_todos_los_validos);
  RUN_TEST(test_rechaza_los_invalidos_con_su_motivo);
  RUN_TEST(test_arma_eventos_1);
  RUN_TEST(test_arma_hola);
  RUN_TEST(test_campos_de_eventos_30);
  RUN_TEST(test_campos_de_listas_y_tarifas);
  RUN_TEST(test_validador_desconocido);
  RUN_TEST(test_bus_no_puede_mandar_listas);
  RUN_TEST(test_tipo_desconocido_y_cortado);
  RUN_TEST(test_lista_con_firma_de_otra_clave);
  RUN_TEST(test_limites_al_armar);
  RUN_TEST(test_medir_paquetes_concatenados);
  RUN_TEST(test_tiempo_ecdsa);
  RUN_TEST(test_fragmentos_ida_y_vuelta);
  RUN_TEST(test_fragmento_perdido_descarta_el_mensaje);
  RUN_TEST(test_otro_mensaje_descarta_el_anterior);
  RUN_TEST(test_trama_invalida);
  UNITY_END();
}

void loop() {}
