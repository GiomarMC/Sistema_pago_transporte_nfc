// M4-01: SincronizadorV2 con un canal falso y paquetes del servidor firmados de verdad.
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <vector>

#include <CanalFalso.h>
#include "sincronizador_v2.h"
#include "vectores_sync.h"

namespace {

constexpr uint16_t MI_NUMERO = 107;
uint32_t horaFalsa() { return HORA_SERVIDOR_PRUEBA; }

// El almacenamiento que el sincronizador usaría, en memoria
class AlmacenFalso : public AlmacenSync {
 public:
  uint32_t ultimoEvento() const override { return ultimo_; }
  uint32_t ultimoAcusado() const override { return acusado_; }
  size_t eventosDesde(uint32_t desdeId, sp::Evento *salida, size_t max) override {
    size_t n = 0;
    for (uint32_t id = desdeId + 1; id <= ultimo_ && n < max; id++) salida[n++] = evento(id);
    return n;
  }
  void acusarHasta(uint32_t hastaId) override { acusado_ = hastaId; acusos_++; }
  uint32_t versionListas() const override { return vListas_; }
  uint32_t versionTarifas() const override { return vTarifas_; }
  uint32_t siguienteSecuencia() override { return ++secuencia_; }
  size_t ajenos() const override { return ajenos_.size(); }
  size_t leerAjeno(size_t i, uint8_t *buf, size_t max) override {
    if (i >= ajenos_.size() || ajenos_[i].bytes.size() > max) return 0;
    memcpy(buf, ajenos_[i].bytes.data(), ajenos_[i].bytes.size());
    return ajenos_[i].bytes.size();
  }
  void ajenosAcusados(uint16_t origen, uint32_t hasta) override {
    ultimoAjenoAcusado_ = {origen, hasta};
    for (size_t i = 0; i < ajenos_.size();)
      if (ajenos_[i].origen == origen && ajenos_[i].ultimoId <= hasta) ajenos_.erase(ajenos_.begin() + i);
      else i++;
  }
  void aplicarListas(const sp::Listas &l, const uint8_t *, size_t) override { listas_ = l; listasRecibidas_++; }
  void aplicarTarifas(const sp::Tarifas &t) override { tarifas_ = t; tarifasRecibidas_++; }

  static sp::Evento evento(uint32_t id) {
    sp::Evento e = {};
    e.idEvento = id;
    e.tipo = sp::VIAJE;
    e.largoUid = 4;
    e.operacion = id & 0xFFFF;
    e.fecha = 1795000000 + id;
    e.idTarjeta = 900 + id;
    e.uid[0] = id;
    e.monto = -130;
    e.saldoFinal = 5000 - 130 * (int32_t)id;
    e.contador = id;
    snprintf(e.detalle, sizeof(e.detalle), "viaje %u", (unsigned)id);
    return e;
  }
  void guardarViajes(uint32_t cuantos) { ultimo_ += cuantos; }

  struct Ajeno { uint16_t origen; uint32_t ultimoId; std::vector<uint8_t> bytes; };

  uint32_t ultimo_ = 0, acusado_ = 0, secuencia_ = 0, vListas_ = 11, vTarifas_ = 22;
  uint32_t acusos_ = 0, listasRecibidas_ = 0, tarifasRecibidas_ = 0;
  std::vector<Ajeno> ajenos_;
  std::pair<uint16_t, uint32_t> ultimoAjenoAcusado_{0, 0};
  sp::Listas listas_ = {};
  sp::Tarifas tarifas_ = {};
};

ConfigSyncV2 config() {
  ConfigSyncV2 c;
  c.validador = MI_NUMERO;
  c.claveHmac = CLAVE_HMAC_107;
  c.clavePublicaServidor = CLAVE_PUBLICA_PRUEBA;
  c.versionFirmware = 20261120;
  c.ahoraUnix = horaFalsa;
  return c;
}

const uint8_t *claveDe(uint16_t v) { return v == MI_NUMERO ? CLAVE_HMAC_107 : nullptr; }

// Lo que el validador mandó, ya verificado con su clave HMAC
struct Salida {
  sp::Paquete p;
  size_t largo;
};
Salida sacar(CanalFalso &canal) {
  static uint8_t buf[sp::TAM_MAXIMO];
  Salida s;
  s.largo = canal.sacarEnviado(buf, sizeof(buf));
  assert(s.largo > 0);
  sp::Motivo m = sp::Motivo::OK;
  const bool ok = sp::desempaquetar(buf, s.largo, claveDe, CLAVE_PUBLICA_PRUEBA, s.p, &m);
  assert(ok);
  assert(s.p.cabecera.origen == MI_NUMERO && s.p.cabecera.destino == sp::SERVIDOR);
  return s;
}

template <size_t N>
void servidorDice(CanalFalso &canal, const uint8_t (&paquete)[N]) { canal.inyectar(paquete, N); }

// --- 100 eventos en 4 paquetes --------------------------------------------------------

void cienEventosEnCuatroPaquetes() {
  AlmacenFalso alm;
  alm.guardarViajes(100);
  CanalFalso canal;
  SincronizadorV2 s(alm, config());

  s.atender(&canal, 1000);
  // Primero el HOLA, y después 3 paquetes de eventos (el máximo sin acuse)
  Salida hola = sacar(canal);
  assert(hola.p.cabecera.tipo == sp::HOLA);
  assert(hola.p.hola.ultimoEvento == 100 && hola.p.hola.pendientes == 100 && hola.p.hola.enlace == 1);
  assert(hola.p.hola.versionListas == 11 && hola.p.hola.versionTarifas == 22 && hola.p.hola.versionFirmware == 20261120);

  uint32_t siguiente = 1;
  for (int k = 0; k < 3; k++) {
    Salida e = sacar(canal);
    assert(e.p.cabecera.tipo == sp::EVENTOS && e.p.eventos.n == 30);
    for (int i = 0; i < 30; i++) assert(e.p.eventos.eventos[i].idEvento == siguiente++);
  }
  assert(canal.enviados() == 0 && s.paquetesSinAcuse() == 3);  // el 4.º espera: ya van 3 sin acuse
  s.atender(&canal, 1200);
  assert(canal.enviados() == 0);

  // Llega el acuse de los 90 primeros: sale el 4.º con los 10 restantes
  servidorDice(canal, ACUSE_107_HASTA_90);
  s.atender(&canal, 1500);
  Salida ultimo = sacar(canal);
  assert(ultimo.p.cabecera.tipo == sp::EVENTOS && ultimo.p.eventos.n == 10);
  assert(ultimo.p.eventos.eventos[0].idEvento == 91 && ultimo.p.eventos.eventos[9].idEvento == 100);
  assert(s.paquetesEventos() == 4 && alm.acusado_ == 90 && s.pendientes() == 10);
  assert(strcmp(ultimo.p.eventos.eventos[9].detalle, "viaje 100") == 0);  // el contenido llega entero
}

void acuseHastaSesenta() {
  AlmacenFalso alm;
  alm.guardarViajes(100);
  CanalFalso canal;
  SincronizadorV2 s(alm, config());
  s.atender(&canal, 0);
  while (canal.enviados()) sacar(canal);

  servidorDice(canal, ACUSE_107_HASTA_60);
  s.atender(&canal, 500);
  assert(alm.acusado_ == 60);
  assert(s.pendientes() == 40);  // exactamente 40 eventos sin confirmar
  assert(s.horaServidor() == HORA_SERVIDOR_PRUEBA && s.millisHora() == 500);  // M4-03

  // Un acuse más viejo que otro ya recibido no hace retroceder nada
  servidorDice(canal, ACUSE_107_HASTA_30);
  s.atender(&canal, 600);
  assert(alm.acusado_ == 60 && s.pendientes() == 40);
}

// --- Reenvío a los 30 s -----------------------------------------------------------------

void reenvioALos30Segundos() {
  AlmacenFalso alm;
  alm.guardarViajes(40);
  CanalFalso canal;
  SincronizadorV2 s(alm, config());
  s.atender(&canal, 10000);
  sacar(canal);  // HOLA
  Salida a = sacar(canal), b = sacar(canal);
  assert(a.p.eventos.n == 30 && b.p.eventos.n == 10 && s.paquetesSinAcuse() == 2);

  s.atender(&canal, 39999);  // 29,999 s: todavía no
  assert(canal.enviados() == 0 && s.reenvios() == 0);
  s.atender(&canal, 40000);  // 30 s sin acuse: se vuelve a mandar desde el primero sin confirmar
  assert(s.reenvios() == 1);
  Salida ra = sacar(canal), rb = sacar(canal);
  assert(ra.p.eventos.n == 30 && ra.p.eventos.eventos[0].idEvento == 1);
  assert(rb.p.eventos.n == 10 && rb.p.eventos.eventos[0].idEvento == 31);
  assert(ra.p.cabecera.secuencia > a.p.cabecera.secuencia);  // un paquete nuevo, no la misma secuencia

  // Con un acuse parcial, solo se reenvía lo que falta
  servidorDice(canal, ACUSE_107_HASTA_30);
  s.atender(&canal, 41000);
  s.atender(&canal, 71000);  // 30 s después del reenvío
  Salida rc = sacar(canal);
  assert(rc.p.eventos.eventos[0].idEvento == 31 && rc.p.eventos.n == 10);
}

// --- Viaje nuevo, HOLA y canal ----------------------------------------------------------

void viajeNuevoSaleEnLaMismaVuelta() {
  AlmacenFalso alm;
  CanalFalso canal;
  SincronizadorV2 s(alm, config());
  s.atender(&canal, 0);
  assert(sacar(canal).p.cabecera.tipo == sp::HOLA && canal.enviados() == 0);  // sin eventos: solo HOLA

  alm.guardarViajes(1);  // se cobra un viaje
  s.atender(&canal, 250);
  Salida e = sacar(canal);
  assert(e.p.eventos.n == 1 && e.p.eventos.eventos[0].idEvento == 1);  // sin esperar a juntar más
  servidorDice(canal, ACUSE_107_HASTA_30);  // el servidor ya sabía de más: nada que reenviar
  s.atender(&canal, 300);
  assert(s.pendientes() == 0 && canal.enviados() == 0);
}

void holaAlConectarYCada5Minutos() {
  AlmacenFalso alm;
  CanalFalso canal;
  SincronizadorV2 s(alm, config());
  s.atender(&canal, 0);
  assert(s.holasEnviados() == 1);
  s.atender(&canal, 299999);
  assert(s.holasEnviados() == 1);
  s.atender(&canal, 300000);
  assert(s.holasEnviados() == 2);

  // Sin canal no se manda nada; al volver, un HOLA
  canal.ponerListo(false);
  canal.vaciar();
  s.atender(&canal, 310000);
  s.atender(nullptr, 320000);
  assert(canal.enviados() == 0);
  canal.ponerListo(true);
  s.atender(&canal, 330000);
  assert(s.holasEnviados() == 3);
}

void sinCanalLosEventosSeConservan() {
  AlmacenFalso alm;
  alm.guardarViajes(45);
  CanalFalso canal;
  canal.ponerListo(false);
  SincronizadorV2 s(alm, config());
  s.atender(&canal, 0);
  assert(canal.enviados() == 0 && s.pendientes() == 45);

  canal.ponerListo(true);
  s.atender(&canal, 5000);
  sacar(canal);  // HOLA
  assert(sacar(canal).p.eventos.n == 30 && sacar(canal).p.eventos.n == 15);

  // Se cae el canal con paquetes en vuelo: al volver se manda todo desde lo no confirmado
  canal.ponerListo(false);
  s.atender(&canal, 6000);
  canal.vaciar();
  canal.ponerListo(true);
  s.atender(&canal, 7000);
  sacar(canal);  // HOLA
  Salida otra = sacar(canal);
  assert(otra.p.eventos.eventos[0].idEvento == 1 && otra.p.eventos.n == 30);
}

// --- Lo que llega del servidor ------------------------------------------------------------

void paquetesInvalidosSeRechazan() {
  AlmacenFalso alm;
  alm.guardarViajes(100);
  CanalFalso canal;
  SincronizadorV2 s(alm, config());
  s.atender(&canal, 0);
  while (canal.enviados()) sacar(canal);

  servidorDice(canal, ACUSE_107_FIRMA_ROTA);  // firma alterada
  s.atender(&canal, 100);
  assert(s.rechazados() == 1 && s.ultimoRechazo() == sp::Motivo::FIRMA);
  assert(alm.acusado_ == 0 && s.pendientes() == 100);  // no se confirma nada

  const uint8_t basura[] = {1, 2, 3, 4, 5};
  canal.inyectar(basura, sizeof(basura));
  s.atender(&canal, 200);
  assert(s.rechazados() == 2 && alm.acusado_ == 0);

  // El mismo paquete sin tocar sí vale (el servidor firma con la clave de la que se tiene la pública)
  servidorDice(canal, ACUSE_107_HASTA_60);
  s.atender(&canal, 300);
  assert(alm.acusado_ == 60 && s.rechazados() == 2);

  // Con otra clave pública el paquete del servidor ya no se acepta
  AlmacenFalso alm2;
  ConfigSyncV2 otra = config();
  uint8_t claveAjena[65];
  memcpy(claveAjena, CLAVE_PUBLICA_PRUEBA, 65);
  claveAjena[10] ^= 0x55;
  otra.clavePublicaServidor = claveAjena;
  SincronizadorV2 s2(alm2, otra);
  CanalFalso canal2;
  s2.atender(&canal2, 0);
  servidorDice(canal2, ACUSE_107_HASTA_60);
  s2.atender(&canal2, 100);
  assert(s2.rechazados() == 1 && alm2.acusado_ == 0);
}

void listasYTarifasAlAlmacen() {
  AlmacenFalso alm;
  CanalFalso canal;
  SincronizadorV2 s(alm, config());
  s.atender(&canal, 0);
  canal.vaciar();
  servidorDice(canal, LISTAS_107);
  servidorDice(canal, TARIFAS_107);
  s.atender(&canal, 100);  // un paquete por vuelta (#129): primero LISTAS
  assert(alm.listasRecibidas_ == 1 && alm.tarifasRecibidas_ == 0);
  s.atender(&canal, 101);  // y luego TARIFAS
  assert(alm.listasRecibidas_ == 1 && alm.listas_.versionDesde == 0x0A && alm.listas_.versionHasta == 0x0C &&
         alm.listas_.n == 2);
  assert(alm.listas_.entradas[0].tipo == sp::BLOQUEO && alm.listas_.entradas[0].a == 4242);
  assert(alm.listas_.entradas[1].tipo == sp::RECARGA && alm.listas_.entradas[1].a == 77 &&
         alm.listas_.entradas[1].b == 3 && alm.listas_.entradas[1].c == 500);
  assert(alm.tarifasRecibidas_ == 1 && alm.tarifas_.n == 2 && alm.tarifas_.tarifas[0].precio == 130 &&
         strcmp(alm.tarifas_.tarifas[1].nombre, "estudiante") == 0);
}

// --- Firmas del servidor comprobadas en otra tarea (#129) ----------------------------------

// Un TrabajoEnFondo que no hace nada hasta que el test lo dice
struct FondoFalso : TrabajoEnFondo {
  Trabajo trabajo = nullptr;
  void *arg = nullptr;
  bool rechazar = false;
  int lanzados = 0;
  bool lanzar(Trabajo t, void *a) override {
    if (rechazar || trabajo) return false;
    trabajo = t;
    arg = a;
    ++lanzados;
    return true;
  }
  bool hayTrabajo() const { return trabajo != nullptr; }
  void terminar() {
    Trabajo t = trabajo;
    trabajo = nullptr;
    t(arg);
  }
};

void firmasEnOtraTarea() {
  AlmacenFalso alm;
  alm.guardarViajes(100);
  CanalFalso canal;
  FondoFalso fondo;
  ConfigSyncV2 cfg = config();
  cfg.verificador = &fondo;
  {
    SincronizadorV2 s(alm, cfg);
    s.atender(&canal, 0);
    while (canal.enviados()) sacar(canal);

    // El ACUSE llega: la firma va a la otra tarea y atender() vuelve sin procesarlo, ni sacar el siguiente
    servidorDice(canal, ACUSE_107_HASTA_60);
    servidorDice(canal, ACUSE_107_HASTA_30);
    s.atender(&canal, 500);
    assert(fondo.lanzados == 1 && fondo.hayTrabajo() && s.verificando());
    assert(alm.acusado_ == 0 && canal.porRecibir() == 1);
    s.atender(&canal, 510);  // aún sin terminar: nada cambia
    assert(alm.acusado_ == 0 && canal.porRecibir() == 1 && fondo.lanzados == 1);
    // Terminó: en la vuelta siguiente se procesa (con la hora de esa vuelta) y se lanza el que esperaba
    fondo.terminar();
    s.atender(&canal, 520);
    assert(alm.acusado_ == 60 && s.pendientes() == 40 && s.verificadasEnFondo() == 1);
    assert(s.horaServidor() == HORA_SERVIDOR_PRUEBA && s.millisHora() == 520);
    assert(fondo.lanzados == 2 && canal.porRecibir() == 0);
    fondo.terminar();
    s.atender(&canal, 530);
    assert(alm.acusado_ == 60 && s.verificadasEnFondo() == 2);  // el viejo, comprobado, no hace retroceder nada

    // Una firma mala comprobada en la otra tarea: se rechaza igual que en el acto
    servidorDice(canal, ACUSE_107_FIRMA_ROTA);
    s.atender(&canal, 600);
    fondo.terminar();
    s.atender(&canal, 610);
    assert(s.rechazados() == 1 && s.ultimoRechazo() == sp::Motivo::FIRMA && alm.acusado_ == 60);

    // Si el canal deja de estar listo a mitad, el paquete se procesa igual al terminar
    servidorDice(canal, ACUSE_107_HASTA_60);
    s.atender(&canal, 700);
    assert(s.verificando());
    canal.ponerListo(false);
    fondo.terminar();
    s.atender(&canal, 710);
    assert(!s.verificando() && s.verificadasEnFondo() == 4);
    canal.ponerListo(true);

    // Si no se puede lanzar (la tarea ocupada o sin memoria), se comprueba en el acto
    fondo.rechazar = true;
    servidorDice(canal, ACUSE_107_HASTA_30);
    s.atender(&canal, 800);
    assert(!s.verificando() && s.verificadasEnFondo() == 4 && s.paquetesRecibidos() >= 5);
  }
  // Los LISTAS también: llegan al almacén solo tras comprobar la firma
  AlmacenFalso alm2;
  CanalFalso canal2;
  FondoFalso fondo2;
  ConfigSyncV2 cfg2 = config();
  cfg2.verificador = &fondo2;
  SincronizadorV2 s2(alm2, cfg2);
  s2.atender(&canal2, 0);
  canal2.vaciar();
  servidorDice(canal2, LISTAS_107);
  s2.atender(&canal2, 100);
  assert(alm2.listasRecibidas_ == 0);
  fondo2.terminar();
  s2.atender(&canal2, 110);
  assert(alm2.listasRecibidas_ == 1 && alm2.listas_.n == 2);

  // Lo que trae un vecino (entregar()) también va a la otra tarea, y se procesa como relevado: la hora del ACUSE se
  // compensa con RETRASO_RELE_S aunque el canal sea WiFi
  AlmacenFalso alm3;
  alm3.guardarViajes(100);
  CanalFalso canal3;
  FondoFalso fondo3;
  ConfigSyncV2 cfg3 = config();
  cfg3.verificador = &fondo3;
  SincronizadorV2 s3(alm3, cfg3);
  s3.atender(&canal3, 0);
  s3.entregar(ACUSE_107_HASTA_60, sizeof(ACUSE_107_HASTA_60), 50);
  assert(s3.verificando() && alm3.acusado_ == 0 && fondo3.lanzados == 1);
  // con el verificador ocupado, otro del vecino se comprueba en el acto
  s3.entregar(ACUSE_107_HASTA_30, sizeof(ACUSE_107_HASTA_30), 60);
  assert(fondo3.lanzados == 1 && alm3.acusado_ == 30);
  fondo3.terminar();
  s3.atender(&canal3, 70);
  assert(alm3.acusado_ == 60 && s3.horaServidor() == HORA_SERVIDOR_PRUEBA + SincronizadorV2::RETRASO_RELE_S);
  printf("  firmas del servidor en otra tarea: un paquete a la vez, el resultado se aplica después, firma mala, sin canal, sin tarea y por un vecino OK\n");
}

// --- Paquetes de otros buses ------------------------------------------------------------

void paquetesAjenosSeReenvianSinTocar() {
  AlmacenFalso alm;
  // Un paquete de otro bus: los bytes son opacos para el sincronizador (no lo vuelve a firmar)
  AlmacenFalso::Ajeno a1 = {105, 12, {}}, a2 = {105, 40, {}};
  for (int i = 0; i < 300; i++) a1.bytes.push_back(i * 3);
  for (int i = 0; i < 90; i++) a2.bytes.push_back(255 - i);
  alm.ajenos_ = {a1, a2};
  CanalFalso canal;
  SincronizadorV2 s(alm, config());

  s.atender(&canal, 0);  // HOLA y el primer ajeno
  s.atender(&canal, 10);  // el segundo
  Salida hola = sacar(canal);
  assert(hola.p.cabecera.tipo == sp::HOLA && hola.p.hola.ajenos == 2);
  uint8_t buf[sp::TAM_MAXIMO];
  size_t n = canal.sacarEnviado(buf, sizeof(buf));
  assert(n == a1.bytes.size() && memcmp(buf, a1.bytes.data(), n) == 0);
  n = canal.sacarEnviado(buf, sizeof(buf));
  assert(n == a2.bytes.size() && memcmp(buf, a2.bytes.data(), n) == 0);
  assert(s.paquetesAjenos() == 2);

  // Mientras sigan guardados se reenvían a los 30 s
  s.atender(&canal, 29000);
  assert(canal.enviados() == 0);
  s.atender(&canal, 30000);
  s.atender(&canal, 30010);
  assert(canal.enviados() == 2);
  canal.vaciar();

  // El acuse del dueño (105) hasta el 12 quita el primero y deja el segundo
  servidorDice(canal, ACUSE_105_HASTA_12);
  s.atender(&canal, 31000);
  assert(alm.ultimoAjenoAcusado_.first == 105 && alm.ultimoAjenoAcusado_.second == 12);
  assert(alm.ajenos_.size() == 1 && alm.ajenos_[0].ultimoId == 40);
  assert(alm.acusado_ == 0);  // y no cuenta como acuse de los eventos propios
}

void servidorSabeMasDeLoQueSeCreia() {
  // Tras perder el estado local, el servidor confirma más allá de lo enviado: no se reenvía lo ya confirmado
  AlmacenFalso alm;
  alm.guardarViajes(140);
  CanalFalso canal;
  SincronizadorV2 s(alm, config());
  s.atender(&canal, 0);
  while (canal.enviados()) sacar(canal);
  servidorDice(canal, ACUSE_107_HASTA_130);
  s.atender(&canal, 100);
  Salida e = sacar(canal);
  assert(e.p.eventos.eventos[0].idEvento == 131 && e.p.eventos.n == 10);
}

// --- Hora del servidor (M4-03) -----------------------------------------------------------

// Un sincronizador con el canal dado, que ya mandó su HOLA
struct Escenario {
  AlmacenFalso alm;
  CanalFalso canal;
  SincronizadorV2 s;
  Escenario(const char *nombre, uint8_t codigo) : canal(nombre, codigo), s(alm, config()) {
    s.atender(&canal, 0);
    canal.vaciar();
  }
};

template <size_t N>
void llega(Escenario &e, const uint8_t (&paquete)[N], uint32_t ahoraMs) {
  servidorDice(e.canal, paquete);
  e.s.atender(&e.canal, ahoraMs);
}

void horaDelAcuse() {
  Escenario e("mqtt-wifi", 1);
  assert(e.s.horaServidor() == 0 && e.s.horaActual(1000) == 0);  // sin referencia todavía
  llega(e, ACUSE_107_HASTA_30, 5000);
  assert(e.s.horaServidor() == HORA_SERVIDOR_PRUEBA && e.s.millisHora() == 5000);
  assert(e.s.horaActual(5000) == HORA_SERVIDOR_PRUEBA);
  assert(e.s.horaActual(5000 + 42999) == HORA_SERVIDOR_PRUEBA + 42);  // avanza con el reloj local
  assert(e.s.horaActual(5000 + 43000) == HORA_SERVIDOR_PRUEBA + 43);

  // Cada ACUSE válido renueva la referencia (también uno que no confirma nada nuevo)
  llega(e, ACUSE_107_HASTA_60_100S_DESPUES, 105000);
  assert(e.s.horaServidor() == HORA_SERVIDOR_PRUEBA + 100 && e.s.millisHora() == 105000);
}

void horaPorCanalesRetransmitidos() {
  // Reenviado por otro bus el paquete tarda más: el contrato estima 2 s
  Escenario rele("espnow", sp::REENVIADO);
  llega(rele, ACUSE_107_HASTA_30, 9000);
  assert(rele.s.horaServidor() == HORA_SERVIDOR_PRUEBA + 2 && rele.s.millisHora() == 9000);

  Escenario wifi("mqtt-wifi", sp::WIFI);
  llega(wifi, ACUSE_107_HASTA_30, 9000);
  assert(wifi.s.horaServidor() == HORA_SERVIDOR_PRUEBA);  // por WiFi no se compensa nada
}

void horaDentroDeTresSegundos() {
  // El servidor firma el ACUSE a la hora H y llega con `retrasoMs` de demora. A los 5 minutos
  // la hora del validador debe estar a ±3 s de la del servidor, sea cual sea el camino.
  struct Camino { const char *nombre; uint8_t enlace; uint32_t retrasoMs; };
  const Camino caminos[] = {{"mqtt-wifi", sp::WIFI, 300}, {"espnow", sp::REENVIADO, 2000}};
  for (const Camino &c : caminos) {
    Escenario e(c.nombre, c.enlace);
    const uint32_t llegada = 10000 + c.retrasoMs;  // el ACUSE se firmó en el ms 10000
    llega(e, ACUSE_107_HASTA_30, llegada);
    for (uint32_t despues : {0u, 1000u, 59999u, 300000u}) {
      const uint32_t ahora = llegada + despues;
      const int64_t real = (int64_t)HORA_SERVIDOR_PRUEBA + (ahora - 10000) / 1000;  // la hora del servidor ahora
      const int64_t dif = (int64_t)e.s.horaActual(ahora) - real;
      assert(dif >= -3 && dif <= 3);
    }
  }
}

void horaSoloConFirmaValida() {
  Escenario e("mqtt-wifi", 1);
  llega(e, ACUSE_107_FIRMA_ROTA, 1000);
  assert(e.s.rechazados() == 1 && e.s.horaServidor() == 0);  // una firma rota no pone la hora

  llega(e, ACUSE_107_HASTA_30, 2000);
  assert(e.s.horaServidor() == HORA_SERVIDOR_PRUEBA);
  // Un ACUSE de una hora antigua, aunque la firma sea buena (reenviado por quien lo transportaba),
  // no hace retroceder el reloj
  llega(e, ACUSE_107_HASTA_60_1H_ANTES, 3000);
  assert(e.s.horaServidor() == HORA_SERVIDOR_PRUEBA && e.s.millisHora() == 2000);
  assert(e.alm.acusado_ == 60);  // pero sí confirma sus eventos: lo único antiguo es la hora
}

}  // namespace

// --- Eventos con la hora estimada (correccion_hora.h) -----------------------------------

void correccionPorTramos() {
  CorreccionHora c;
  assert(!c.retenido(1) && c.anotados() == 0);
  // Arranque con la hora estimada: base 1000 s por detrás de la real, en millis() = 2000
  const uint32_t real = 1795000000;
  c.anotar(1, real - 1000, 2000);
  c.anotar(2, real - 1000, 2000);
  // cobrar() adelanta el reloj estimado 300 s (la tarjeta llevaba una fecha posterior): tramo nuevo
  c.anotar(3, real - 700, 2000);
  assert(c.anotados() == 3 && c.retenido(1) && c.retenido(3) && !c.retenido(4));
  sp::Evento e = AlmacenFalso::evento(1);
  assert(!c.corregir(e));  // sin hora real no se toca
  // Llega la hora real: `real` en millis() = 2000 + 400 ms después de la base
  c.horaReal(real + 0, 2400);
  assert(c.hayHoraReal() && !c.retenido(1) && !c.retenido(3));
  sp::Evento e1 = AlmacenFalso::evento(1), e3 = AlmacenFalso::evento(3), e4 = AlmacenFalso::evento(4);
  const uint32_t f1 = e1.fecha, f3 = e3.fecha, f4 = e4.fecha;
  assert(c.corregir(e1) && e1.fecha == f1 + 1000);   // 1000 s - 0,4 s, redondeado al segundo
  assert(c.corregir(e3) && e3.fecha == f3 + 700);
  assert(!c.corregir(e4) && e4.fecha == f4);          // no se cobró con hora estimada
  // Con la hora real ya conocida no se anota más (lo nuevo ya lleva la buena)
  c.anotar(5, real - 1000, 2000);
  assert(c.anotados() == 3);
  // Una hora real POR DETRÁS de la estimada (la estimada se adelantó de más) también corrige, hacia atrás
  CorreccionHora atras;
  atras.anotar(1, real + 60, 0);
  atras.horaReal(real, 0);
  sp::Evento a = AlmacenFalso::evento(1);
  const uint32_t fa = a.fecha;
  assert(atras.corregir(a) && a.fecha == fa - 60);
  puts("  hora estimada: tramos por salto del reloj, corrección redondeada, hacia atrás y nada antes de la hora real OK");
}

void retenidosHastaLaHoraReal() {
  AlmacenFalso alm;
  CanalFalso canal;
  CorreccionHora corr;
  ConfigSyncV2 cfg = config();
  cfg.correccion = &corr;
  SincronizadorV2 s(alm, cfg);
  alm.guardarViajes(3);
  for (uint32_t id = 1; id <= 3; id++) corr.anotar(id, 1795000000 - 500, 0);
  s.atender(&canal, 100);
  assert(sacar(canal).p.cabecera.tipo == sp::HOLA && canal.enviados() == 0);  // solo el HOLA: los 3 esperan
  assert(s.retenido(1) && s.pendientes() == 3);
  uint32_t ultimo = 0;
  uint8_t buf[sp::TAM_MAXIMO];
  assert(s.empaquetarEventos(0, buf, sizeof(buf), ultimo, 100) == 0);  // tampoco para un vecino
  s.atender(&canal, 5000);
  assert(canal.enviados() == 0);  // siguen esperando
  // Llega la hora real (en la placa, del ACUSE del HOLA: main.cpp llama a fijarHora): desde ahí salen corregidos
  corr.horaReal(1795000000, 0);
  s.atender(&canal, 5100);
  Salida e = sacar(canal);
  assert(e.p.cabecera.tipo == sp::EVENTOS && e.p.eventos.n == 3);
  for (uint8_t i = 0; i < 3; i++) {
    const sp::Evento orig = AlmacenFalso::evento(i + 1);
    assert(e.p.eventos.eventos[i].fecha == orig.fecha + (uint32_t)corr.delta());
  }
  assert(corr.delta() == 500);
  puts("  hora estimada: los eventos no salen hasta la hora real y salen con la fecha corregida OK");
}

void ultimoAcuseParaUnVecino() {
  AlmacenFalso alm;
  CanalFalso canal;
  SincronizadorV2 s(alm, config());
  uint8_t buf[128];
  uint32_t ms = 0;
  assert(s.ultimoAcuse(buf, sizeof(buf), ms) == 0);
  s.atender(&canal, 100);
  sacar(canal);
  servidorDice(canal, ACUSE_107_HASTA_30);
  s.atender(&canal, 700);
  assert(s.ultimoAcuse(buf, sizeof(buf), ms) == sizeof(ACUSE_107_HASTA_30) && ms == 700);
  assert(memcmp(buf, ACUSE_107_HASTA_30, sizeof(ACUSE_107_HASTA_30)) == 0);  // tal cual, con su firma
  // pedirHola: el HOLA sale en la próxima vuelta, sin esperar los 5 minutos
  s.pedirHola();
  s.atender(&canal, 800);
  assert(sacar(canal).p.cabecera.tipo == sp::HOLA);
  puts("  hora para un vecino: se guarda el último ACUSE propio tal cual y pedirHola() manda un HOLA enseguida OK");
}

int main() {
  correccionPorTramos();
  retenidosHastaLaHoraReal();
  ultimoAcuseParaUnVecino();
  cienEventosEnCuatroPaquetes();
  acuseHastaSesenta();
  reenvioALos30Segundos();
  viajeNuevoSaleEnLaMismaVuelta();
  holaAlConectarYCada5Minutos();
  sinCanalLosEventosSeConservan();
  paquetesInvalidosSeRechazan();
  listasYTarifasAlAlmacen();
  firmasEnOtraTarea();
  paquetesAjenosSeReenvianSinTocar();
  servidorSabeMasDeLoQueSeCreia();
  horaDelAcuse();
  horaPorCanalesRetransmitidos();
  horaDentroDeTresSegundos();
  horaSoloConFirmaValida();
  puts("M4-01/03 sincronizador v2: 100 eventos en 4 paquetes, acuse a 60 = 40 pendientes, reenvío a 30 s, HOLA, ajenos, firmas y hora (±3 s) OK");
}
