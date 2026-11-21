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
  void aplicarListas(const sp::Listas &l) override { listas_ = l; listasRecibidas_++; }
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
  s.atender(&canal, 100);
  assert(alm.listasRecibidas_ == 1 && alm.listas_.versionDesde == 0x0A && alm.listas_.versionHasta == 0x0C &&
         alm.listas_.n == 2);
  assert(alm.listas_.entradas[0].tipo == sp::BLOQUEO && alm.listas_.entradas[0].a == 4242);
  assert(alm.listas_.entradas[1].tipo == sp::RECARGA && alm.listas_.entradas[1].a == 77 &&
         alm.listas_.entradas[1].b == 3 && alm.listas_.entradas[1].c == 500);
  assert(alm.tarifasRecibidas_ == 1 && alm.tarifas_.n == 2 && alm.tarifas_.tarifas[0].precio == 130 &&
         strcmp(alm.tarifas_.tarifas[1].nombre, "estudiante") == 0);
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
  // Por BLE y reenviado por otro bus el paquete tarda más: el contrato estima 2 s
  Escenario ble("ble", sp::BLE);
  llega(ble, ACUSE_107_HASTA_30, 7000);
  assert(ble.s.horaServidor() == HORA_SERVIDOR_PRUEBA + 2 && ble.s.millisHora() == 7000);

  Escenario rele("espnow", sp::REENVIADO);
  llega(rele, ACUSE_107_HASTA_30, 9000);
  assert(rele.s.horaServidor() == HORA_SERVIDOR_PRUEBA + 2);

  Escenario wifi("mqtt-wifi", sp::WIFI);
  llega(wifi, ACUSE_107_HASTA_30, 9000);
  assert(wifi.s.horaServidor() == HORA_SERVIDOR_PRUEBA);  // por WiFi no se compensa nada
}

void horaDentroDeTresSegundos() {
  // El servidor firma el ACUSE a la hora H y llega con `retrasoMs` de demora. A los 5 minutos
  // la hora del validador debe estar a ±3 s de la del servidor, sea cual sea el camino.
  struct Camino { const char *nombre; uint8_t enlace; uint32_t retrasoMs; };
  const Camino caminos[] = {{"mqtt-wifi", sp::WIFI, 300}, {"ble", sp::BLE, 2000}, {"espnow", sp::REENVIADO, 2000}};
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

int main() {
  cienEventosEnCuatroPaquetes();
  acuseHastaSesenta();
  reenvioALos30Segundos();
  viajeNuevoSaleEnLaMismaVuelta();
  holaAlConectarYCada5Minutos();
  sinCanalLosEventosSeConservan();
  paquetesInvalidosSeRechazan();
  listasYTarifasAlAlmacen();
  paquetesAjenosSeReenvianSinTocar();
  servidorSabeMasDeLoQueSeCreia();
  horaDelAcuse();
  horaPorCanalesRetransmitidos();
  horaDentroDeTresSegundos();
  horaSoloConFirmaValida();
  puts("M4-01/03 sincronizador v2: 100 eventos en 4 paquetes, acuse a 60 = 40 pendientes, reenvío a 30 s, HOLA, ajenos, firmas y hora (±3 s) OK");
}
