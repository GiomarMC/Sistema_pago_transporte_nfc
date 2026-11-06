// Ejemplo de uso de las interfaces de enlace (C2) con sus implementaciones falsas (M0-03).
// Ejecutar:  pio test -e validador -f test_enlaces
#include <Arduino.h>
#include <CanalFalso.h>
#include <EnlaceFalso.h>
#include <ProtocoloSP.h>
#include <string.h>
#include <unity.h>

constexpr uint16_t VALIDADOR = 101;
const uint8_t CLAVE[sp::TAM_CLAVE_HMAC] = {0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11,
                                           0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11,
                                           0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11,
                                           0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11};

static sp::Paquete paq;  // ~2.3 KB: estático para no llenar la pila del loop
static uint8_t buf[sp::TAM_MAXIMO], otro[sp::TAM_MAXIMO];

const uint8_t *claveDe(uint16_t v) { return v == VALIDADOR ? CLAVE : nullptr; }

// Lo que hará el sincronizador (M4), en pequeño: solo conoce CanalPaquetes. Arma un HOLA
// con el enlace y la señal del canal, y lo envía si el canal está listo.
size_t enviarHola(CanalPaquetes &canal, uint32_t secuencia) {
  if (!canal.listo()) return 0;
  paq.cabecera = {sp::HOLA, VALIDADOR, sp::SERVIDOR, secuencia, 1793000000};
  paq.hola = {103, 1792913600, 30, 3, 1, canal.codigoEnlace(), -40, 0, 0, 20261106};
  size_t largo = sp::empaquetar(paq, CLAVE, buf, sizeof(buf));
  return largo > 0 && canal.enviar(buf, largo) ? largo : 0;
}

// Cliente TCP que no hace nada: solo para que EnlaceFalso tenga algo que devolver
class ClienteNulo : public Client {
 public:
  int connect(IPAddress, uint16_t) override { return 1; }
  int connect(const char *, uint16_t) override { return 1; }
  size_t write(uint8_t) override { return 1; }
  size_t write(const uint8_t *, size_t n) override { return n; }
  int available() override { return 0; }
  int read() override { return -1; }
  int read(uint8_t *, size_t) override { return 0; }
  int peek() override { return -1; }
  void flush() override {}
  void stop() override {}
  uint8_t connected() override { return 1; }
  operator bool() override { return true; }
};

// --- CanalFalso ----------------------------------------------------------------------------

void test_el_hola_sale_por_el_canal_byte_a_byte() {
  CanalFalso canal("mqtt-4g", sp::RED_4G);
  size_t largo = enviarHola(canal, 7);
  TEST_ASSERT_GREATER_THAN(0, largo);
  TEST_ASSERT_EQUAL(1, canal.enviados());

  // Lo que salió es un HOLA válido, firmado, con el enlace del canal
  memcpy(otro, buf, largo);
  TEST_ASSERT_EQUAL(largo, canal.sacarEnviado(buf, sizeof(buf)));
  TEST_ASSERT_EQUAL_MEMORY(otro, buf, largo);
  TEST_ASSERT_TRUE(sp::desempaquetar(buf, largo, claveDe, nullptr, paq));
  TEST_ASSERT_EQUAL_UINT8(sp::HOLA, paq.cabecera.tipo);
  TEST_ASSERT_EQUAL_UINT32(7, paq.cabecera.secuencia);
  TEST_ASSERT_EQUAL_UINT8(sp::RED_4G, paq.hola.enlace);
  TEST_ASSERT_EQUAL(0, canal.enviados());
}

void test_sin_canal_listo_no_se_envia_nada() {
  CanalFalso canal;
  canal.ponerListo(false);
  TEST_ASSERT_EQUAL(0, enviarHola(canal, 1));
  TEST_ASSERT_EQUAL(0, canal.enviados());
  // Directo a enviar(): también lo rechaza y lo cuenta
  const uint8_t algo[] = {1, 2, 3};
  TEST_ASSERT_FALSE(canal.enviar(algo, sizeof(algo)));
  TEST_ASSERT_EQUAL_UINT32(1, canal.rechazados());

  canal.ponerListo(true);
  TEST_ASSERT_GREATER_THAN(0, enviarHola(canal, 2));
  TEST_ASSERT_EQUAL(1, canal.enviados());
}

void test_envios_en_orden() {
  CanalFalso canal;
  for (uint32_t s = 1; s <= 3; s++) TEST_ASSERT_GREATER_THAN(0, enviarHola(canal, s));
  for (uint32_t s = 1; s <= 3; s++) {
    size_t largo = canal.sacarEnviado(buf, sizeof(buf));
    TEST_ASSERT_TRUE(sp::desempaquetar(buf, largo, claveDe, nullptr, paq));
    TEST_ASSERT_EQUAL_UINT32(s, paq.cabecera.secuencia);
  }
  TEST_ASSERT_EQUAL(0, canal.sacarEnviado(buf, sizeof(buf)));
}

void test_recibe_lo_inyectado_en_orden() {
  CanalFalso canal;
  CanalPaquetes &comoInterfaz = canal;
  TEST_ASSERT_EQUAL(0, comoInterfaz.recibir(buf, sizeof(buf)));  // nada todavía
  const uint8_t a[] = {'S', 'P', 1}, b[] = {'S', 'P', 2, 2};
  canal.inyectar(a, sizeof(a));
  canal.inyectar(b, sizeof(b));
  TEST_ASSERT_EQUAL(2, canal.porRecibir());
  TEST_ASSERT_EQUAL(sizeof(a), comoInterfaz.recibir(buf, sizeof(buf)));
  TEST_ASSERT_EQUAL_MEMORY(a, buf, sizeof(a));
  TEST_ASSERT_EQUAL(sizeof(b), comoInterfaz.recibir(buf, sizeof(buf)));
  TEST_ASSERT_EQUAL_MEMORY(b, buf, sizeof(b));
  TEST_ASSERT_EQUAL(0, comoInterfaz.recibir(buf, sizeof(buf)));
}

void test_paquete_que_no_cabe_se_descarta() {
  CanalFalso canal;
  const uint8_t grande[8] = {0}, chico[2] = {9, 9};
  canal.inyectar(grande, sizeof(grande));
  canal.inyectar(chico, sizeof(chico));
  TEST_ASSERT_EQUAL(0, canal.recibir(buf, 4));
  TEST_ASSERT_EQUAL_UINT32(1, canal.descartados());
  TEST_ASSERT_EQUAL(2, canal.recibir(buf, 4));  // la cola no se atasca
}

void test_nombre_codigo_y_atender() {
  CanalFalso ble("ble", sp::BLE);
  CanalPaquetes &c = ble;
  TEST_ASSERT_EQUAL_STRING("ble", c.nombre());
  TEST_ASSERT_EQUAL_UINT8(sp::BLE, c.codigoEnlace());
  c.atender();
  c.atender();
  TEST_ASSERT_EQUAL_UINT32(2, ble.atenciones());
  CanalFalso porDefecto;
  TEST_ASSERT_EQUAL_STRING("mqtt-wifi", porDefecto.nombre());
  TEST_ASSERT_EQUAL_UINT8(sp::WIFI, porDefecto.codigoEnlace());
}

// --- EnlaceFalso ---------------------------------------------------------------------------

void test_enlace_controlado_por_el_test() {
  ClienteNulo cliente;
  EnlaceFalso falso("4g", 2);
  Enlace &enlace = falso;
  TEST_ASSERT_EQUAL_STRING("4g", enlace.nombre());
  TEST_ASSERT_EQUAL_UINT8(2, enlace.codigo());
  TEST_ASSERT_TRUE(enlace.estado() == EstadoEnlace::Apagado);

  enlace.encender();
  TEST_ASSERT_TRUE(enlace.estado() == EstadoEnlace::Conectando);
  falso.ponerCliente(&cliente);
  TEST_ASSERT_NULL(enlace.abrir("subepe.app", 8883));  // aún no está Listo

  falso.ponerEstado(EstadoEnlace::Listo);
  falso.ponerSenal(-45);
  TEST_ASSERT_EQUAL_PTR(&cliente, enlace.abrir("subepe.app", 8883));
  TEST_ASSERT_EQUAL_STRING("subepe.app", falso.ultimoHost());
  TEST_ASSERT_EQUAL_UINT16(8883, falso.ultimoPuerto());
  TEST_ASSERT_EQUAL_INT8(-45, enlace.senal());
  falso.sumarBytes(120, 300);
  TEST_ASSERT_EQUAL_UINT32(120, enlace.bytesTx());
  TEST_ASSERT_EQUAL_UINT32(300, enlace.bytesRx());

  falso.ponerEstado(EstadoEnlace::Error);  // se cae: pierde la conexión
  TEST_ASSERT_FALSE(falso.abierto());
  TEST_ASSERT_NULL(enlace.abrir("subepe.app", 8883));

  falso.ponerEstado(EstadoEnlace::Listo);
  TEST_ASSERT_NOT_NULL(enlace.abrir("subepe.app", 8883));
  enlace.apagar();
  TEST_ASSERT_TRUE(enlace.estado() == EstadoEnlace::Apagado);
  TEST_ASSERT_FALSE(falso.abierto());
  TEST_ASSERT_EQUAL_UINT32(1, falso.cierres());
  TEST_ASSERT_EQUAL_UINT32(1, falso.encendidos());
  TEST_ASSERT_EQUAL_UINT32(1, falso.apagados());
  TEST_ASSERT_EQUAL_UINT32(4, falso.aperturas());
}

void setup() {
  delay(2000);
  UNITY_BEGIN();
  RUN_TEST(test_el_hola_sale_por_el_canal_byte_a_byte);
  RUN_TEST(test_sin_canal_listo_no_se_envia_nada);
  RUN_TEST(test_envios_en_orden);
  RUN_TEST(test_recibe_lo_inyectado_en_orden);
  RUN_TEST(test_paquete_que_no_cabe_se_descarta);
  RUN_TEST(test_nombre_codigo_y_atender);
  RUN_TEST(test_enlace_controlado_por_el_test);
  UNITY_END();
}

void loop() {}
