// M4/M15: AlmacenSyncBinario (SincronizadorV2 -> almacenamiento binario) con el sincronizador de verdad, firmas
// reales, CanalFalso y FsFalso. Lo que se comprueba, sobre todo: lo que corre desde atender() NO escribe en la
// microSD (los cambios se hacen en paso(), que solo avanza con el lector libre y una operación por llamada).
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include <memory>
#include <vector>

#include <CanalFalso.h>

#include "almacen_sync_binario.h"
#include "fs_falso.h"
#include "vectores_listas.h"
#include "vectores_sync.h"

namespace {

using Bytes = std::vector<uint8_t>;
using Res = AplicadorListas::Resultado;

constexpr uint16_t MI_107 = 107;
uint32_t horaFalsa() { return HORA_SERVIDOR_PRUEBA; }

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

struct Banco {
  FsFalso fs;
  CanalFalso canal;
  std::unique_ptr<Piezas> p;
  std::unique_ptr<AlmacenSyncBinario> alm;
  std::unique_ptr<SincronizadorV2> sinc;
  uint32_t ms = 1000;

  Banco() { arrancar(); }

  void arrancar() {
    sinc.reset();
    alm.reset();
    p.reset(new Piezas(fs));
    alm.reset(new AlmacenSyncBinario(p->cola, p->estado, p->ajenos, p->aplicador, horaFalsa));
    ConfigSyncV2 c;
    c.validador = MI_107;
    c.claveHmac = CLAVE_HMAC_107;
    c.clavePublicaServidor = CLAVE_PUBLICA_PRUEBA;
    c.versionFirmware = 20261121;
    c.ahoraUnix = horaFalsa;
    sinc.reset(new SincronizadorV2(*alm, c));
    canal.vaciar();
    ms += 1000;
    sinc->atender(&canal, ms);  // el primer HOLA
    canal.vaciar();
  }

  // Cuántas operaciones de escritura hizo `f` (el FsFalso solo las cuenta con un corte programado: uno que nunca llega)
  template <typename F>
  long escrituras(F f) {
    fs.programarCorte(1000000000L);
    f();
    const long n = fs.operaciones;
    fs.sinCorte();
    return n;
  }

  void evento(uint8_t n = 1) {
    for (uint8_t i = 0; i < n; i++) {
      sp::Evento e;
      memset(&e, 0, sizeof(e));
      e.tipo = sp::VIAJE;
      e.largoUid = 4;
      e.fecha = HORA_SERVIDOR_PRUEBA;
      e.monto = -150;
      e.contador = sp::SIN_CONTADOR;
      assert(p->cola.agregar(e));
    }
  }
  // El servidor manda estos paquetes y el sincronizador los procesa (una vuelta de atender())
  void servidor(std::initializer_list<Bytes> paquetes) {
    for (const Bytes &b : paquetes) canal.inyectar(b.data(), b.size());
    ms += 10;
    sinc->atender(&canal, ms);
  }
  // Lo que el validador mandó desde la última vez: cuántos paquetes por tipo y origen
  struct Salida {
    int hola = 0, eventosPropios = 0, ajenos = 0;
    std::vector<Bytes> paquetesAjenos;
  };
  Salida drenar() {
    Salida s;
    static uint8_t buf[sp::TAM_MAXIMO];
    size_t n;
    while ((n = canal.sacarEnviado(buf, sizeof(buf))) > 0) {
      const uint8_t tipo = buf[3];
      const uint16_t origen = buf[4] | (buf[5] << 8);
      if (tipo == sp::HOLA) s.hola++;
      else if (tipo == sp::EVENTOS && origen == MI_107) s.eventosPropios++;
      else if (tipo == sp::EVENTOS) {
        s.ajenos++;
        s.paquetesAjenos.emplace_back(buf, buf + n);
      }
    }
    return s;
  }
  // Pasos hasta que no quede nada; cuenta los pasos y devuelve el máximo de escrituras en uno solo
  struct Medida {
    int pasos = 0;
    long maxEscrituras = 0;
  };
  Medida vaciarPendientes() {
    Medida m;
    for (int i = 0; i < 2000 && alm->trabajoPendiente(); i++) {
      bool hizo = false;
      const long e = escrituras([&] { hizo = alm->paso(true); });
      if (!hizo) break;
      m.pasos++;
      if (e > m.maxEscrituras) m.maxEscrituras = e;
    }
    assert(!alm->trabajoPendiente());
    return m;
  }
  void reiniciar() { arrancar(); }
};

template <size_t N>
Bytes bytes(const uint8_t (&d)[N]) {
  return Bytes(d, d + N);
}

// Un EVENTOS firmado con HMAC de otro validador (105), con eventos desde `primero`
Bytes eventosAjenos(uint32_t secuencia, uint32_t primero, uint8_t n = 3) {
  static const uint8_t CLAVE_105[32] = {1, 0, 5};
  static sp::Paquete p;
  memset(&p, 0, sizeof(p));
  p.cabecera = {sp::EVENTOS, 105, sp::SERVIDOR, secuencia, HORA_SERVIDOR_PRUEBA};
  p.eventos.n = n;
  for (uint8_t i = 0; i < n; i++) {
    p.eventos.eventos[i].idEvento = primero + i;
    p.eventos.eventos[i].tipo = sp::VIAJE;
  }
  Bytes b(sp::TAM_MAXIMO);
  const size_t largo = sp::empaquetar(p, CLAVE_105, b.data(), b.size());
  assert(largo > 0);
  b.resize(largo);
  return b;
}

void partir(Banco &b, uint32_t version) {
  assert(b.p->estado.fijarVersiones(version, b.p->estado.estado().versionTarifas));
}

// --- Eventos y acuse ---------------------------------------------------------------------

void eventos_y_acuse() {
  Banco b;
  b.evento(30);
  assert(b.alm->ultimoEvento() == 30 && b.alm->ultimoAcusado() == 0);
  b.ms += 10;
  b.sinc->atender(&b.canal, b.ms);
  const Banco::Salida s = b.drenar();
  assert(s.eventosPropios == 1 && b.sinc->pendientes() == 30);

  // El acuse llega por atender(): se recuerda, pero NO se escribe nada en la microSD
  const long durante = b.escrituras([&] { b.servidor({bytes(ACUSE_107_HASTA_30)}); });
  assert(durante == 0);
  assert(b.alm->ultimoAcusado() == 30 && b.sinc->pendientes() == 0);   // el sincronizador ya lo sabe
  assert(b.p->cola.ultimoAcusado() == 0 && b.alm->trabajoPendiente());  // la microSD aún no
  // con el lector ocupado no se hace nada
  assert(b.escrituras([&] { assert(!b.alm->paso(false)); }) == 0 && b.p->cola.ultimoAcusado() == 0);
  // con el lector libre se guarda, una operación a la vez: primero el acuse, luego la compactación en dos pasos (todo
  // quedó confirmado: guardar siguiente_id y reescribir el archivo)
  assert(b.alm->paso(true));
  assert(b.p->cola.ultimoAcusado() == 30 && b.p->cola.registros() == 30 && b.p->cola.compactacionPendiente());
  assert(b.alm->paso(true) && b.p->cola.registros() == 30);  // guardar siguiente_id
  assert(b.alm->paso(true) && b.p->cola.registros() == 0);   // reescribir el archivo
  assert(!b.alm->trabajoPendiente() && !b.alm->paso(true));
  // y sobrevive a un reinicio, y los ids siguen sin saltos
  b.reiniciar();
  assert(b.alm->ultimoAcusado() == 30 && b.alm->ultimoEvento() == 30);
  b.evento();
  assert(b.alm->ultimoEvento() == 31);
  // los eventos salen por eventosDesde() y la secuencia de los paquetes crece
  sp::Evento e[4];
  assert(b.alm->eventosDesde(30, e, 4) == 1 && e[0].idEvento == 31);
  const uint32_t s1 = b.alm->siguienteSecuencia(), s2 = b.alm->siguienteSecuencia();
  assert(s2 > s1);
  printf("  acuse: atender() no escribe, paso() lo guarda con el lector libre y sobrevive al reinicio OK\n");
}

// --- Paquetes de otros buses -----------------------------------------------------------------

void ajenos_y_acuse() {
  Banco b;
  const Bytes a[5] = {eventosAjenos(1, 1), eventosAjenos(2, 4), eventosAjenos(3, 7), eventosAjenos(4, 10), eventosAjenos(5, 13)};
  for (const Bytes &x : a) assert(b.p->ajenos.guardar(x.data(), x.size()));
  assert(b.alm->ajenos() == 5);
  // el sincronizador los reenvía tal cual, de uno en uno
  for (int i = 0; i < 6; i++) {
    b.ms += 10;
    b.sinc->atender(&b.canal, b.ms);
  }
  const Banco::Salida s = b.drenar();
  assert(s.ajenos == 5);
  for (size_t i = 0; i < 5; i++) assert(s.paquetesAjenos[i] == a[i]);

  // el servidor confirma los del 105 hasta el evento 12: 4 paquetes sobran. En atender() no se borra ninguno
  const long durante = b.escrituras([&] { b.servidor({bytes(ACUSE_105_HASTA_12)}); });
  assert(durante == 0 && b.alm->ajenos() == 5 && b.p->ajenos.confirmadosPorBorrar() == 4);
  // y el sincronizador ya no reenvía los confirmados (solo falta borrarlos)
  b.drenar();
  b.ms += SincronizadorV2::REENVIO_MS;  // la siguiente ronda de ajenos empieza a los 30 s
  for (int i = 0; i < 6; i++) {
    b.ms += 10;
    b.sinc->atender(&b.canal, b.ms);
  }
  const Banco::Salida s2 = b.drenar();
  assert(s2.ajenos == 1 && s2.paquetesAjenos[0] == a[4]);
  // paso() los borra de uno en uno
  int pasos = 0;
  while (b.alm->paso(true)) pasos++;
  assert(pasos == 4 && b.alm->ajenos() == 1 && b.alm->cuenta().ajenosBorrados == 4);
  // tras un reinicio el acuse en RAM se perdió, pero el que no estaba cubierto sigue guardado
  b.reiniciar();
  assert(b.alm->ajenos() == 1);
  printf("  ajenos: se reenvían tal cual, el acuse no borra en atender() y paso() borra uno por llamada OK\n");
}

// --- LISTAS y TARIFAS por pasos -----------------------------------------------------------

void listas_por_pasos() {
  Banco b;
  partir(b, 200);
  // tres paquetes encadenados de golpe: 200 -> 205 -> 210, y uno que no es contiguo
  const long durante = b.escrituras([&] { b.servidor({bytes(LISTAS_N1_200_205), bytes(LISTAS_N2_205_210)}); });
  assert(durante == 0);  // nada de lo que hace atender() toca la microSD
  assert(b.alm->versionListas() == 200 && b.alm->trabajosEnCola() == 2 && !b.p->lista.bloqueada(2000));
  assert(b.alm->paso(false) == false && b.alm->versionListas() == 200);  // lector ocupado

  const Banco::Medida m = b.vaciarPendientes();
  assert(m.pasos >= 4);  // por pasos de verdad: no de un solo golpe
  assert(b.alm->versionListas() == 210);
  assert(b.p->lista.bloqueada(2000) && b.p->lista.bloqueada(2001));
  assert(b.alm->cuenta().listasAplicadas == 2 && b.p->firmados.cantidad() == 2);
  // el HOLA siguiente anuncia la nueva versión
  b.reiniciar();
  assert(b.alm->versionListas() == 210 && b.p->lista.bloqueada(2000));
  printf("  LISTAS: encolados sin tocar la microSD, aplicados en %d pasos (máx. %ld escrituras por paso) OK\n", m.pasos, m.maxEscrituras);
  // una cota para detectar que alguien vuelva a juntar todo en un paso (la lista es de 4 ids; cada paso hace una cosa)
  assert(m.maxEscrituras <= 12);

  // la cola tiene COLA_TRABAJOS sitios: el siguiente se descarta y el servidor lo repite
  Banco c;
  partir(c, 200);
  c.servidor({bytes(LISTAS_N1_200_205), bytes(LISTAS_N2_205_210), bytes(LISTAS_N3_205_212), bytes(LISTAS_N4_212_214)});
  assert(c.alm->trabajosEnCola() == AlmacenSyncBinario::COLA_TRABAJOS && c.alm->cuenta().descartados == 1);
  c.vaciarPendientes();
  // 200 -> 205 -> 210; el N3 (205 -> 212) ya es viejo y el N4 se descartó
  assert(c.alm->versionListas() == 210);
  c.servidor({bytes(LISTAS_N4_212_214)});  // ahora cabe, pero salta (falta el 210 -> 212): no se aplica
  c.vaciarPendientes();
  assert(c.alm->versionListas() == 210);
  printf("  LISTAS: cola de %zu, el exceso se descarta y no rompe la contigüidad OK\n", AlmacenSyncBinario::COLA_TRABAJOS);
}

// Una lista grande y una foto completa: cada paso hace poco aunque el trabajo total sea mucho
void listas_grandes_y_foto() {
  Banco b;
  {
    std::vector<ListaNegra::Cambio> muchos;
    for (uint32_t i = 0; i < 3000; i++) muchos.push_back({10000 + i, true});
    assert(b.p->lista.aplicarCambios(muchos.data(), muchos.size()));
    while (b.p->lista.avanzar(100000) == ListaNegra::Progreso::EN_CURSO) {
    }
  }
  partir(b, 200);
  b.servidor({bytes(LISTAS_N1_200_205)});
  const Banco::Medida m = b.vaciarPendientes();
  assert(b.alm->versionListas() == 205 && b.p->lista.bloqueada(2000) && b.p->lista.bloqueada(12999) && b.p->lista.cantidad() == 3001);
  // 3000 identificadores son 24 sectores: la mezcla se parte en ese número de pasos, no se hace de una vez
  assert(m.pasos >= 24 + 4);
  assert(m.maxEscrituras <= 8);
  printf("  lista de 3000 tarjetas: %d pasos, como mucho %ld escrituras en uno OK\n", m.pasos, m.maxEscrituras);

  // una foto completa en 3 paquetes: lo anterior desaparece y queda lo de la foto
  Banco f;
  partir(f, 150);
  f.servidor({bytes(LISTAS_FOTO_1), bytes(LISTAS_FOTO_2), bytes(LISTAS_FOTO_3)});
  assert(f.p->lista.cantidad() == 0);
  f.vaciarPendientes();
  assert(f.alm->versionListas() == 200 && f.p->lista.cantidad() == 6 && f.p->lista.bloqueada(1005));
  uint32_t hasta;
  int32_t total;
  f.p->recargas.pendientes(50, 0, total, hasta);
  assert(total == 700 && hasta == 2);
  printf("  foto completa en 3 paquetes por pasos OK\n");
}

void urgente_pasa_delante() {
  Banco b;
  partir(b, 205);
  b.servidor({bytes(LISTAS_N3_205_212), bytes(LISTAS_U_3000_212)});
  assert(b.alm->trabajosEnCola() == 2);
  // el urgente es lo primero que se aplica, aunque llegó después
  int pasos = 0;
  while (!b.p->lista.bloqueada(3000) && pasos < 20) {
    assert(b.alm->paso(true));
    pasos++;
  }
  assert(b.p->lista.bloqueada(3000) && b.alm->versionListas() == 205 && !b.p->lista.bloqueada(3002));
  assert(pasos <= 4);
  b.vaciarPendientes();
  assert(b.alm->versionListas() == 212 && b.p->lista.bloqueada(3002) && b.p->lista.cantidadUrgentes() == 0);
  printf("  urgente: pasa por delante de la cola (%d pasos), la versión no cambia hasta el cambio en orden OK\n", pasos);
}

void tarifas_por_pasos() {
  Banco b;
  // dos TARIFAS seguidas: la nueva sustituye a la que aún espera
  b.servidor({bytes(TARIFAS_A), bytes(TARIFAS_B)});
  assert(b.alm->trabajosEnCola() == 1 && b.alm->versionTarifas() == 0);
  b.vaciarPendientes();
  assert(b.alm->versionTarifas() == TARIFAS_B_VERSION && b.alm->cuenta().tarifasAplicadas == 1);
  // una más vieja que la guardada se descarta
  b.servidor({bytes(TARIFAS_VIEJA)});
  b.vaciarPendientes();
  assert(b.alm->versionTarifas() == TARIFAS_B_VERSION && b.alm->cuenta().tarifasAplicadas == 1);
  b.reiniciar();
  assert(b.alm->versionTarifas() == TARIFAS_B_VERSION);
  printf("  TARIFAS: la nueva sustituye a la que espera, la vieja se descarta y sobreviven al reinicio OK\n");
}

void incidencia_de_lista_falsa() {
  Banco b;
  partir(b, 200);
  Bytes mala = bytes(LISTAS_N1_200_205);
  mala[mala.size() - 3] ^= 0x55;  // firma rota
  const long durante = b.escrituras([&] { b.servidor({mala}); });
  assert(durante == 0 && b.alm->versionListas() == 200 && b.alm->trabajosEnCola() == 0);
  assert(b.alm->trabajoPendiente());
  const uint32_t antes = b.alm->ultimoEvento();
  assert(b.alm->paso(true));
  assert(b.alm->ultimoEvento() == antes + 1 && b.alm->cuenta().incidencias == 1);
  sp::Evento e;
  assert(b.alm->eventosDesde(antes, &e, 1) == 1);
  assert(e.tipo == sp::INCIDENCIA && strcmp(e.detalle, SincronizadorV2::DETALLE_LISTA_FALSA) == 0 && e.fecha == HORA_SERVIDOR_PRUEBA);
  // no se aplicó, y con el paquete bueno después todo sigue normal
  assert(b.alm->versionListas() == 200);
  b.servidor({bytes(LISTAS_N1_200_205)});
  b.vaciarPendientes();
  assert(b.alm->versionListas() == 205);
  printf("  firma inválida: se guarda como evento INCIDENCIA en paso() y no se aplica OK\n");
}

// --- Un corte de energía en cada operación de paso() ------------------------------------------

void cortes() {
  // Se prepara de todo a la vez: acuse, un ajeno confirmado, una incidencia y LISTAS + TARIFAS
  Banco base;
  base.evento(30);
  partir(base, 200);
  const Bytes ajeno = eventosAjenos(1, 1);
  assert(base.p->ajenos.guardar(ajeno.data(), ajeno.size()));
  int cortes = 0;
  for (long k = 0;; k++) {
    Banco t;
    t.fs = base.fs;
    t.reiniciar();
    t.servidor({bytes(ACUSE_107_HASTA_30), bytes(ACUSE_105_HASTA_12), bytes(LISTAS_N1_200_205), bytes(TARIFAS_A)});
    t.alm->incidencia("prueba");
    t.fs.programarCorte(k);
    bool cortado = false;
    try {
      for (int i = 0; i < 200 && t.alm->trabajoPendiente(); i++) t.alm->paso(true);
    } catch (CorteDeEnergia &) {
      cortado = true;
    }
    t.fs.sinCorte();
    if (!cortado) break;
    cortes++;
    t.fs.aplicarCorte();
    t.reiniciar();
    // invariantes tras cualquier corte
    const uint32_t v = t.alm->versionListas();
    assert(v == 200 || v == 205);
    if (v == 205) assert(t.p->lista.bloqueada(2000) && !t.p->lista.bloqueada(1001));
    const uint32_t vt = t.alm->versionTarifas();
    assert(vt == 0 || vt == TARIFAS_A_VERSION);
    assert(t.alm->ultimoAcusado() == 0 || t.alm->ultimoAcusado() == 30);
    assert(t.alm->ultimoEvento() >= 30);  // los 30 eventos siguen (y como mucho la incidencia)
    sp::Evento e[31];
    // sin acuse guardado los 30 eventos siguen en la cola; con él se pudieron compactar (ya no hacen falta)
    if (t.alm->ultimoAcusado() == 0) assert(t.alm->eventosDesde(0, e, 31) >= 30);
    // el servidor repite lo que falte y se llega al mismo estado final
    t.servidor({bytes(ACUSE_107_HASTA_30), bytes(ACUSE_105_HASTA_12), bytes(LISTAS_N1_200_205), bytes(TARIFAS_A)});
    t.vaciarPendientes();
    assert(t.alm->versionListas() == 205 && t.alm->versionTarifas() == TARIFAS_A_VERSION && t.alm->ultimoAcusado() == 30);
    assert(t.p->lista.bloqueada(2000) && !t.p->lista.bloqueada(1001));
  }
  assert(cortes >= 20);
  printf("  %d cortes de energía en paso() (acuse, ajenos, incidencia, LISTAS y TARIFAS): nada queda a medias y el servidor repite lo que falte OK\n", cortes);
}

}  // namespace

int main() {
  eventos_y_acuse();
  ajenos_y_acuse();
  listas_por_pasos();
  listas_grandes_y_foto();
  urgente_pasa_delante();
  tarifas_por_pasos();
  incidencia_de_lista_falsa();
  cortes();
  printf("M4/M15 AlmacenSyncBinario: SincronizadorV2 sobre el almacenamiento binario, sin escribir en atender() OK\n");
}
