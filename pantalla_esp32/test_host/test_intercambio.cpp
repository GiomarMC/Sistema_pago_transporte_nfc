// M8-03 / M8-04 en el ordenador: dos buses completos (SincronizadorV2 con firmas reales, almacenamiento binario sobre
// FsFalso) que se "cruzan" por una radio falsa. El bus A tiene canal con el servidor (un CanalFalso y un servidor
// falso que responde con ACUSE firmados); el bus B no. Se comprueba lo que dice el plan: los viajes de B llegan al
// servidor por A, el acuse vuelve a B por A, las listas pasan de A a B encadenadas, y un LISTAS o un ACUSE con la firma
// alterada se rechazan.
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include <deque>
#include <functional>
#include <memory>
#include <vector>

#include <CanalFalso.h>

#include "fs_falso.h"
#include "intercambio_vecinos.h"
#include "almacen_sync_binario.h"
#include "vectores_listas.h"
#include "vectores_sync.h"

namespace {

using Bytes = std::vector<uint8_t>;

uint32_t horaFalsa() { return HORA_SERVIDOR_PRUEBA; }
const uint8_t CLAVE_108[32] = {1, 0, 8, 7, 7, 7};
const uint8_t MAC_A[6] = {0, 0, 0, 0, 0, 1}, MAC_B[6] = {0, 0, 0, 0, 0, 2};

template <size_t N>
Bytes bytes(const uint8_t (&d)[N]) {
  return Bytes(d, d + N);
}

struct Piezas {
  EstadoPersistente estado;
  ColaEventos cola;
  PaquetesAjenos ajenos;
  ListaNegra lista;
  Recargas recargas;
  TarifasGuardadas tarifas;
  PaquetesFirmados firmados;
  AplicadorListas aplicador;
  explicit Piezas(SistemaArchivos &fs)
      : estado(fs), cola(fs, estado), ajenos(fs), lista(fs), recargas(fs), tarifas(fs), firmados(fs),
        aplicador(estado, lista, recargas, tarifas, firmados) {
    estado.cargar();
    cola.iniciar();
    ajenos.iniciar();
    lista.iniciar();
    recargas.iniciar();
    tarifas.iniciar();
    firmados.iniciar();
  }
};

// La radio de un bus: lo que le llega del otro y lo que ve de él
class RadioFalsa : public RadioVecinos {
 public:
  RadioFalsa *otra = nullptr;
  uint8_t mac[6];
  bool visible = true;                   // si el otro bus está a su alcance
  std::function<void(Bytes &)> alterar;  // qué le pasa a lo que se envía (para pruebas de firmas)
  std::vector<Vecino> vecinos;
  std::deque<std::pair<Bytes, Bytes>> mensajes;  // (mac origen, paquete)
  std::deque<std::pair<Bytes, sp::Pedido>> pedidos;
  sp::Baliza publicada;
  int enviadosMensajes = 0, enviadosPedidos = 0;
  int limiteMensajes = 1000;

  size_t cantidad() const override { return vecinos.size(); }
  const Vecino &vecino(size_t i) const override { return vecinos[i]; }
  bool enviando() const override { return false; }
  Envio enviarMensaje(const uint8_t m[6], const uint8_t *msg, size_t largo) override {
    if (!otra || !visible || memcmp(m, otra->mac, 6) != 0 || enviadosMensajes >= limiteMensajes) return Envio::RECHAZADO;
    Bytes b(msg, msg + largo);
    if (alterar) alterar(b);
    otra->mensajes.emplace_back(Bytes(mac, mac + 6), b);
    enviadosMensajes++;
    return Envio::ACEPTADO;
  }
  Envio enviarPedido(const uint8_t m[6], const sp::Pedido &p) override {
    if (!otra || !visible || memcmp(m, otra->mac, 6) != 0) return Envio::RECHAZADO;
    otra->pedidos.emplace_back(Bytes(mac, mac + 6), p);
    enviadosPedidos++;
    return Envio::ACEPTADO;
  }
  size_t recibirMensaje(uint8_t *buf, size_t max, uint8_t macOrigen[6]) override {
    if (mensajes.empty()) return 0;
    auto m = mensajes.front();
    mensajes.pop_front();
    if (m.second.size() > max) return 0;
    memcpy(buf, m.second.data(), m.second.size());
    memcpy(macOrigen, m.first.data(), 6);
    return m.second.size();
  }
  bool recibirPedido(uint8_t macOrigen[6], sp::Pedido &p) override {
    if (pedidos.empty()) return false;
    memcpy(macOrigen, pedidos.front().first.data(), 6);
    p = pedidos.front().second;
    pedidos.pop_front();
    return true;
  }
  void publicar(const sp::Baliza &b) override { publicada = b; }
  // Lo que se oye por la radio cada ciclo: la baliza que el otro tiene publicada
  void oirBaliza() {
    vecinos.clear();
    if (!otra || !visible || otra->publicada.validador == 0) return;
    Vecino v;
    memcpy(v.mac, otra->mac, 6);
    v.baliza = otra->publicada;
    vecinos.push_back(v);
  }
};

struct Bus {
  FsFalso fs;
  std::unique_ptr<Piezas> p;
  std::unique_ptr<AlmacenSyncBinario> alm;
  std::unique_ptr<SincronizadorV2> sinc;
  std::unique_ptr<IntercambioVecinos> iv;
  RadioFalsa radio;
  CanalFalso canal;
  uint16_t numero;
  const uint8_t *hmac;
  bool conCanal;

  Bus(uint16_t n, const uint8_t *clave, const uint8_t *mac_, bool canalListo) : numero(n), hmac(clave), conCanal(canalListo) {
    memcpy(radio.mac, mac_, 6);
    p.reset(new Piezas(fs));
    alm.reset(new AlmacenSyncBinario(p->cola, p->estado, p->ajenos, p->aplicador, horaFalsa, &p->firmados));
    ConfigSyncV2 c;
    c.validador = n;
    c.claveHmac = clave;
    c.clavePublicaServidor = CLAVE_PUBLICA_PRUEBA;
    c.versionFirmware = 20261121;
    c.ahoraUnix = horaFalsa;
    sinc.reset(new SincronizadorV2(*alm, c));
    iv.reset(new IntercambioVecinos(radio, *sinc, *alm, n));
    canal.ponerListo(canalListo);
  }
  void viajes(int n) {
    for (int i = 0; i < n; i++) {
      sp::Evento e;
      memset(&e, 0, sizeof(e));
      e.tipo = sp::VIAJE;
      e.largoUid = 4;
      e.fecha = HORA_SERVIDOR_PRUEBA;
      e.monto = -130;
      e.contador = sp::SIN_CONTADOR;
      assert(p->cola.agregar(e));
    }
  }
  uint32_t version() { return p->estado.estado().versionListas; }
};

// Un servidor falso a la espera de lo que sube el bus A. Guarda lo que llegó y responde con el ACUSE para B
struct ServidorFalso {
  std::vector<Bytes> recibidos;
  int eventosDe108 = 0;
  bool respondeAcuse = true;
  const uint8_t *claveDe(uint16_t v) const { return v == 108 ? CLAVE_108 : nullptr; }
};
ServidorFalso *servidorActual = nullptr;
const uint8_t *claveDeValidador(uint16_t v) { return servidorActual ? servidorActual->claveDe(v) : nullptr; }

struct Escena {
  Bus A, B;
  ServidorFalso srv;
  uint32_t ms = 1000;
  Escena(bool aConCanal = true) : A(107, CLAVE_HMAC_107, MAC_A, aConCanal), B(108, CLAVE_108, MAC_B, false) {
    A.radio.otra = &B.radio;
    B.radio.otra = &A.radio;
    servidorActual = &srv;
  }
  void servidor(Bus &bus) {
    static uint8_t buf[sp::TAM_MAXIMO];
    static sp::Paquete pq;
    size_t n;
    while ((n = bus.canal.sacarEnviado(buf, sizeof(buf))) > 0) {
      srv.recibidos.emplace_back(buf, buf + n);
      sp::Motivo m;
      if (sp::desempaquetar(buf, n, claveDeValidador, CLAVE_PUBLICA_PRUEBA, pq, &m) && pq.cabecera.tipo == sp::EVENTOS &&
          pq.cabecera.origen == 108) {  // la firma HMAC de B se comprueba aquí, como lo haría el servidor
        srv.eventosDe108++;
        if (srv.respondeAcuse) {
          const Bytes a = bytes(ACUSE_108_HASTA_30);
          bus.canal.inyectar(a.data(), a.size());
        }
      }
    }
  }
  // Un ciclo de unos 100 ms de los dos buses
  void ciclo() {
    ms += 100;
    A.radio.oirBaliza();
    B.radio.oirBaliza();
    A.sinc->atender(A.conCanal ? &A.canal : nullptr, ms);
    B.sinc->atender(nullptr, ms);
    A.iv->atender(ms, A.conCanal);
    B.iv->atender(ms, false);
    A.alm->paso(true);
    B.alm->paso(true);
    servidor(A);
  }
  void ciclos(int n) {
    for (int i = 0; i < n; i++) ciclo();
  }
  // El servidor le manda algo a A por su canal
  void servidorAA(const Bytes &b) { A.canal.inyectar(b.data(), b.size()); }
};

void vaciar(Escena &e) {
  for (int i = 0; i < 400 && (e.A.alm->trabajoPendiente() || e.B.alm->trabajoPendiente()); i++) e.ciclo();
}

// --- Los viajes de B llegan al servidor por A, y el acuse vuelve a B por A -------------------------------

void eventos_y_acuse() {
  Escena e;
  e.B.viajes(30);
  e.A.sinc->atender(&e.A.canal, e.ms);  // el HOLA de A
  e.ciclos(60);
  // el servidor recibió el EVENTOS de B tal como B lo firmó (la firma HMAC se comprobó en el servidor falso)
  assert(e.srv.eventosDe108 >= 1);
  assert(e.A.sinc->ajenosRecibidos() >= 1);
  // el acuse del servidor para B llegó a A, que lo guardó y lo entregó a B cuando lo pidió
  assert(e.B.alm->ultimoAcusado() == 30 && e.B.sinc->pendientes() == 0);
  assert(e.B.iv->cuenta().pedidosAcuses >= 1 && e.A.iv->cuenta().acusesEntregados >= 1);
  // A ya no guarda los paquetes de B (el acuse los cubre) y B compacta su cola
  vaciar(e);
  assert(e.A.p->ajenos.cantidad() == 0);
  assert(e.B.p->cola.registros() == 0);
  // la hora de un ACUSE que llega por un vecino se compensa con el retraso del relevo
  assert(e.B.sinc->horaServidor() == HORA_SERVIDOR_PRUEBA + SincronizadorV2::RETRASO_RELE_S);
  printf("  eventos: B (sin red) entrega 30 viajes a A, A los sube, el ACUSE vuelve a B y B queda sin pendientes OK\n");
}

void condiciones_de_entrega() {
  // Dos buses sin canal: no se entregan eventos hasta que B lleva más de 10 minutos sin canal
  Escena e(false);
  e.B.viajes(5);
  e.ciclos(30);
  assert(e.B.iv->cuenta().eventosEntregados == 0 && e.A.p->ajenos.cantidad() == 0);
  e.ms += IntercambioVecinos::SIN_CANAL_MS;
  e.ciclos(60);
  assert(e.B.iv->cuenta().eventosEntregados >= 1);
  vaciar(e);
  assert(e.A.p->ajenos.cantidad() >= 1);  // A lo guarda para subirlo cuando tenga canal
  // si el vecino no acepta ajenos (lleno), no se le entrega aunque tenga canal
  Escena lleno;
  lleno.B.viajes(5);
  lleno.A.radio.publicada.flags = 0;
  lleno.ciclos(1);
  lleno.A.radio.publicada.flags = sp::BALIZA_CON_SERVIDOR;  // sin el bit de aceptar ajenos
  for (int i = 0; i < 20; i++) {
    lleno.ms += 100;
    lleno.B.radio.oirBaliza();
    lleno.B.radio.vecinos[0].baliza.flags = sp::BALIZA_CON_SERVIDOR;
    lleno.B.iv->atender(lleno.ms, false);
  }
  assert(lleno.B.iv->cuenta().eventosEntregados == 0);
  printf("  entrega: solo a quien tiene canal y acepta ajenos, o con más de 10 min sin canal, y no a quien no acepta OK\n");
}

void maximo_por_encuentro() {
  // 400 viajes = 14 paquetes de 30: en un encuentro solo se entregan 10
  Escena e;
  e.srv.respondeAcuse = false;
  e.B.viajes(400);
  e.ciclos(150);
  assert(e.B.iv->cuenta().eventosEntregados == IntercambioVecinos::PAQUETES_EVENTOS);
  printf("  entrega: como mucho %d paquetes EVENTOS por encuentro OK\n", (int)IntercambioVecinos::PAQUETES_EVENTOS);
}

// --- Listas: A tiene una versión mayor y B se la pide ------------------------------------------------

void listas_encadenadas() {
  Escena e;
  e.servidorAA(bytes(LISTAS_N1_200_205));
  e.servidorAA(bytes(LISTAS_N2_205_210));
  // A parte de 200 (como B)
  for (Bus *b : {&e.A, &e.B}) assert(b->p->estado.fijarVersiones(200, 0));
  // primero A los recibe del servidor sin ver a B; luego se cruzan
  e.A.radio.visible = e.B.radio.visible = false;
  e.ciclos(60);
  vaciar(e);
  assert(e.A.version() == 210 && e.A.p->firmados.cantidad() == 2 && e.B.version() == 200);
  e.A.radio.visible = e.B.radio.visible = true;
  e.ciclos(60);
  vaciar(e);
  // con UN solo pedido (PEDIDO 1 desde 200) A responde con la cadena entera
  assert(e.A.iv->cuenta().pedidosAtendidos == 1 && e.B.iv->cuenta().pedidosListas == 1);
  // B se enteró por la baliza, pidió y aplicó los dos, encadenados, aunque iban dirigidos a A (107)
  assert(e.B.version() == 210);
  assert(e.B.p->lista.bloqueada(2000) && e.B.p->lista.bloqueada(2001));
  assert(e.B.iv->cuenta().pedidosListas >= 1 && e.A.iv->cuenta().listasEntregadas == 2);
  // B guarda lo recibido para pasárselo a un tercero
  assert(e.B.p->firmados.cantidad() == 2);
  printf("  listas: A tiene 210 y B 200: B pide, recibe los 2 LISTAS encadenados, los aplica y los guarda OK\n");
}

void lista_alterada_se_rechaza() {
  Escena e;
  e.servidorAA(bytes(LISTAS_N1_200_205));
  for (Bus *b : {&e.A, &e.B}) assert(b->p->estado.fijarVersiones(200, 0));
  // la radio de A altera un byte de lo que envía (un bus intermedio malicioso o un fallo)
  e.A.radio.alterar = [](Bytes &b) { b[b.size() - 5] ^= 0x40; };
  e.ciclos(60);
  vaciar(e);
  assert(e.A.version() == 205);  // A lo recibió del servidor, bien
  assert(e.B.version() == 200 && !e.B.p->lista.bloqueada(2000));  // lo alterado no se aplica
  assert(e.B.sinc->rechazados() >= 1 && e.B.sinc->ultimoRechazo() == sp::Motivo::FIRMA);
  assert(e.B.sinc->listasRechazadasPorFirma() >= 1);
  // y cuando la radio deja de alterar, el siguiente pedido (10 s después) funciona
  e.A.radio.alterar = nullptr;
  e.ms += IntercambioVecinos::REPETIR_PEDIDO_MS;
  e.ciclos(60);
  vaciar(e);
  assert(e.B.version() == 205 && e.B.p->lista.bloqueada(2000));
  printf("  listas: un LISTAS con la firma alterada por la radio se rechaza (y queda incidencia); sin alterar, se aplica OK\n");
}

void acuse_alterado_se_rechaza() {
  Escena e;
  e.B.viajes(30);
  e.A.sinc->atender(&e.A.canal, e.ms);
  e.A.radio.alterar = [](Bytes &b) {
    if (b.size() == 92) b[10] ^= 0x01;  // un ACUSE (92 B): se cambia la secuencia
  };
  e.ciclos(80);
  vaciar(e);
  assert(e.srv.eventosDe108 >= 1);
  assert(e.B.alm->ultimoAcusado() == 0 && e.B.sinc->pendientes() == 30);  // un acuse falso no confirma nada
  assert(e.B.sinc->rechazados() >= 1);
  printf("  acuses: un ACUSE alterado en el camino no confirma los eventos de B OK\n");
}

void no_se_pide_a_quien_sabe_menos() {
  Escena e;
  assert(e.A.p->estado.fijarVersiones(200, 0) && e.B.p->estado.fijarVersiones(205, 0));
  e.ciclos(40);
  assert(e.B.iv->cuenta().pedidosListas == 0 && e.B.version() == 205);
  // ni una versión intermedia de una foto completa (no es una versión real)
  Escena f;
  assert(f.A.p->estado.fijarVersiones(AplicadorListas::VERSION_INTERMEDIA + 5, 0) && f.B.p->estado.fijarVersiones(100, 0));
  f.ciclos(40);
  assert(f.B.iv->cuenta().pedidosListas == 0);
  printf("  listas: no se pide a quien sabe menos, ni a quien está a medias de una foto completa OK\n");
}

void con_canal_propio_no_se_entrega() {
  // Si yo tengo canal con el servidor, subo mis eventos yo: no se los doy a un vecino
  Escena e;
  e.B.viajes(5);
  for (int i = 0; i < 40; i++) {
    e.ms += 100;
    e.B.radio.oirBaliza();
    e.A.radio.oirBaliza();
    e.A.iv->atender(e.ms, true);
    e.B.iv->atender(e.ms, true);  // B dice que tiene canal
  }
  assert(e.B.iv->cuenta().eventosEntregados == 0);
  printf("  entrega: con canal propio no se le entregan los eventos a un vecino OK\n");
}

void sin_firmados_no_se_responde() {
  Escena e;
  // B pide con 200 pero A no tiene nada guardado que parta de ahí
  assert(e.A.p->estado.fijarVersiones(300, 0) && e.B.p->estado.fijarVersiones(200, 0));
  e.ciclos(40);
  assert(e.A.iv->cuenta().listasEntregadas == 0 && e.B.version() == 200 && e.A.iv->trabajosPendientes() == 0);
  printf("  listas: sin LISTAS guardados desde esa versión, no se responde nada y no se queda trabajo atascado OK\n");
}

void fuera_de_alcance() {
  Escena e;
  e.B.viajes(30);
  e.A.radio.visible = e.B.radio.visible = false;
  e.ciclos(60);
  assert(e.B.iv->cuenta().eventosEntregados == 0 && e.srv.eventosDe108 == 0);
  e.A.radio.visible = e.B.radio.visible = true;  // se cruzan
  e.ciclos(60);
  vaciar(e);
  assert(e.srv.eventosDe108 >= 1 && e.B.alm->ultimoAcusado() == 30);
  printf("  encuentro: sin verse no pasa nada; al cruzarse, los viajes llegan y se confirman OK\n");
}

}  // namespace

int main() {
  eventos_y_acuse();
  condiciones_de_entrega();
  maximo_por_encuentro();
  listas_encadenadas();
  lista_alterada_se_rechaza();
  acuse_alterado_se_rechaza();
  no_se_pide_a_quien_sabe_menos();
  con_canal_propio_no_se_entrega();
  sin_firmados_no_se_responde();
  fuera_de_alcance();
  printf("M8-03/04 intercambio entre buses: eventos, acuses y listas con firmas reales, sin verse y alteradas OK\n");
}
