// M4-02: las cinco reglas de C1 5.5 (LISTAS) y las TARIFAS, con el SincronizadorV2 de verdad, un CanalFalso y
// el almacenamiento binario sobre FsFalso. Paquetes: los vectores del contrato (vectores_c1.json, validador 101)
// y otros firmados con la clave de prueba (vectores_listas.h, validador 107), más paquetes alterados a mano.
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include <memory>
#include <string>
#include <vector>

#include <AplicadorListas.h>
#include <CanalFalso.h>

#include "../test/test_protocolo/vectores.h"
#include "fs_falso.h"
#include "sincronizador_v2.h"
#include "vectores_listas.h"
#include "vectores_sync.h"

namespace {

using Res = AplicadorListas::Resultado;
using Bytes = std::vector<uint8_t>;

constexpr uint16_t MI_107 = 107;
const uint8_t *claveActual = nullptr;
uint16_t validadorActual = 0;
const uint8_t *claveDe(uint16_t v) { return v == validadorActual ? claveActual : nullptr; }
uint32_t horaFalsa() { return HORA_SERVIDOR_PRUEBA; }

// El almacenamiento binario completo, como lo usará el validador
struct Piezas {
  EstadoPersistente estado;
  ListaNegra lista;
  Recargas recargas;
  TarifasGuardadas tarifas;
  PaquetesFirmados firmados;
  AplicadorListas ap;
  explicit Piezas(SistemaArchivos &fs)
      : estado(fs), lista(fs), recargas(fs), tarifas(fs), firmados(fs), ap(estado, lista, recargas, tarifas, firmados) {
    estado.cargar();
    lista.iniciar();
    recargas.iniciar();
    tarifas.iniciar();
    firmados.iniciar();
  }
};

class AlmacenReal : public AlmacenSync {
 public:
  explicit AlmacenReal(Piezas &p) : p_(p) {}
  uint32_t ultimoEvento() const override { return 0; }
  uint32_t ultimoAcusado() const override { return 0; }
  size_t eventosDesde(uint32_t, sp::Evento *, size_t) override { return 0; }
  void acusarHasta(uint32_t) override {}
  uint32_t versionListas() const override { return p_.estado.estado().versionListas; }
  uint32_t versionTarifas() const override { return p_.estado.estado().versionTarifas; }
  uint32_t siguienteSecuencia() override { return p_.estado.siguienteSecuencia(); }
  size_t ajenos() const override { return 0; }
  size_t leerAjeno(size_t, uint8_t *, size_t) override { return 0; }
  void ajenosAcusados(uint16_t, uint32_t) override {}
  void aplicarListas(const sp::Listas &l, const uint8_t *paquete, size_t largo) override {
    ultimo = p_.ap.listas(l, paquete, largo);
    llamadas++;
  }
  void aplicarTarifas(const sp::Tarifas &t) override {
    ultimo = p_.ap.tarifas(t);
    llamadas++;
  }
  void incidencia(const char *detalle) override { incidencias.push_back(detalle); }

  Res ultimo = Res::FALLO;
  int llamadas = 0;
  std::vector<std::string> incidencias;

 private:
  Piezas &p_;
};

// Un validador con su sincronizador, su canal falso y su "microSD"
struct Banco {
  FsFalso fs;
  CanalFalso canal;
  std::unique_ptr<Piezas> p;
  std::unique_ptr<AlmacenReal> alm;
  std::unique_ptr<SincronizadorV2> sinc;
  uint32_t ms = 1000;
  uint16_t validador;
  const uint8_t *clavePublica, *claveHmac;
  uint32_t versionEnElHola = 0xFFFFFFFF;  // la que anunció el último HOLA

  Banco(uint16_t v, const uint8_t *publica, const uint8_t *hmac) : validador(v), clavePublica(publica), claveHmac(hmac) {
    arrancar();
  }

  void arrancar() {
    validadorActual = validador;
    claveActual = claveHmac;
    sinc.reset();
    alm.reset();
    p.reset(new Piezas(fs));
    alm.reset(new AlmacenReal(*p));
    ConfigSyncV2 c;
    c.validador = validador;
    c.claveHmac = claveHmac;
    c.clavePublicaServidor = clavePublica;
    c.versionFirmware = 20261121;
    c.ahoraUnix = horaFalsa;
    sinc.reset(new SincronizadorV2(*alm, c));
    canal.vaciar();
    ms += 1000;
    sinc->atender(&canal, ms);  // el primer HOLA
    leerHola();
  }
  void leerHola() {
    static uint8_t buf[sp::TAM_MAXIMO];
    static sp::Paquete pq;
    size_t n;
    while ((n = canal.sacarEnviado(buf, sizeof(buf))) > 0) {
      if (sp::desempaquetar(buf, n, claveDe, clavePublica, pq) && pq.cabecera.tipo == sp::HOLA) versionEnElHola = pq.hola.versionListas;
    }
  }
  void reiniciar() { arrancar(); }

  // El servidor manda un paquete; devuelve lo que el almacén hizo con él (o REPETIDA... si no llegó a aplicarlo ver llamadas)
  void servidor(const uint8_t *datos, size_t largo) {
    canal.inyectar(datos, largo);
    ms += 10;
    sinc->atender(&canal, ms);
    canal.vaciar();
  }
  template <size_t N>
  Res manda(const uint8_t (&datos)[N]) {
    const int antes = alm->llamadas;
    alm->ultimo = Res::FALLO;
    servidor(datos, N);
    assert(alm->llamadas == antes + 1);
    return alm->ultimo;
  }
  Res manda(const Bytes &b) {
    const int antes = alm->llamadas;
    servidor(b.data(), b.size());
    assert(alm->llamadas == antes + 1);
    return alm->ultimo;
  }
  uint32_t version() const { return p->estado.estado().versionListas; }
  bool bloqueada(uint32_t id) { return p->lista.bloqueada(id); }
  int32_t recargasDe(uint32_t cuenta, uint32_t desde, uint32_t &hasta) {
    int32_t total;
    p->recargas.pendientes(cuenta, desde, total, hasta);
    return total;
  }
};

// Un banco "del contrato": validador 101 y los vectores de vectores_c1.json
Banco contrato() { return Banco(vectores::VALIDADOR, vectores::CLAVE_PUBLICA, vectores::CLAVE_HMAC); }
Banco miBanco() { return Banco(MI_107, CLAVE_PUBLICA_PRUEBA, CLAVE_HMAC_107); }

Bytes vec(const char *nombre) {
  for (size_t i = 0; i < vectores::N_VALIDOS; i++)
    if (strcmp(vectores::VALIDOS[i].nombre, nombre) == 0)
      return Bytes(vectores::VALIDOS[i].datos, vectores::VALIDOS[i].datos + vectores::VALIDOS[i].largo);
  assert(false);
  return {};
}

template <size_t N>
Bytes bytes(const uint8_t (&d)[N]) {
  return Bytes(d, d + N);
}

// Pone la lista negra y la versión en un estado de partida
void partir(Banco &b, uint32_t version, std::vector<ListaNegra::Cambio> bloqueos = {}) {
  if (!bloqueos.empty()) {
    assert(b.p->lista.aplicarCambios(bloqueos.data(), bloqueos.size()));
    while (b.p->lista.avanzar(1024) == ListaNegra::Progreso::EN_CURSO) {
    }
  }
  assert(b.p->estado.fijarVersiones(version, b.p->estado.estado().versionTarifas));
}

size_t firmadosGuardados(Banco &b) { return b.p->firmados.cantidad(); }

// --- Regla 1: versionado -----------------------------------------------------------------

void regla1_versionado() {
  Banco b = contrato();
  partir(b, 100, {{4, true}});
  assert(b.bloqueada(4) && !b.bloqueada(9));
  assert(b.manda(vec("listas_normal")) == Res::APLICADA);
  // la versión pasa a version_hasta (103) y los cambios están: bloqueo 9, desbloqueo 4, recarga 3 de la cuenta 12
  assert(b.version() == 103);
  assert(b.bloqueada(9) && !b.bloqueada(4));
  uint32_t hasta;
  assert(b.recargasDe(12, 2, hasta) == 2000 && hasta == 3);
  // y es lo que anuncia el HOLA, también tras un reinicio (se guardó, no solo está en RAM)
  b.reiniciar();
  assert(b.version() == 103 && b.versionEnElHola == 103 && b.bloqueada(9) && !b.bloqueada(4));
  assert(b.recargasDe(12, 2, hasta) == 2000);
  assert(b.alm->versionListas() == 103);
  printf("  regla 1: la versión pasa a version_hasta, se guarda y se anuncia en el HOLA OK\n");
}

// --- Regla 2: contigüidad y reinicio -----------------------------------------------------

void regla2_contiguidad() {
  Banco b = contrato();
  partir(b, 100, {{4, true}});
  // un paquete de más adelante (104 -> 104) con la versión en 100: falta uno antes, no se aplica
  assert(b.manda(vec("listas_vacia")) == Res::SALTO && b.version() == 100);
  // el contiguo (100 -> 103) sí
  assert(b.manda(vec("listas_normal")) == Res::APLICADA && b.version() == 103);
  // repetirlo (ya se tiene) no hace nada, y el de 104 sigue sin valer con la versión en 103
  const size_t guardados = firmadosGuardados(b);
  assert(b.manda(vec("listas_normal")) == Res::REPETIDA && b.version() == 103);
  assert(b.manda(vec("listas_vacia")) == Res::SALTO && b.version() == 103);
  assert(firmadosGuardados(b) == guardados);  // lo que no se aplica no se guarda

  // version_desde = 0: borra las listas y las reconstruye con la foto
  Banco f = contrato();
  partir(f, 90, {{77, true}, {9, true}});
  {
    std::vector<Recarga> viejas = {{99, 1, 5}};
    assert(f.p->recargas.reemplazar(viejas));
  }
  assert(f.manda(vec("listas_foto_completa")) == Res::APLICADA);
  assert(f.version() == 104);
  assert(f.bloqueada(9) && f.bloqueada(15) && !f.bloqueada(77));  // 77 ya no está
  uint32_t hasta;
  assert(f.recargasDe(99, 0, hasta) == 0 && f.recargasDe(12, 2, hasta) == 2000);  // la recarga vieja ya no
  // sin cambios, solo avanza la versión
  assert(f.manda(vec("listas_vacia")) == Res::APLICADA && f.version() == 104);
  // una versión normal que retrocede (210 -> 205) no es coherente: no se aplica (con la versión local en 210)
  Banco r = miBanco();
  partir(r, 210);
  assert(r.manda(LISTAS_N_HASTA_MENOR) == Res::INVALIDA && r.version() == 210);
  printf("  regla 2: solo se aplica el contiguo (o la foto desde 0), lo repetido y lo que salta se descartan OK\n");
}

// --- Regla 3: reconstrucción completa en paquetes encadenados --------------------------------

void regla3_foto_encadenada() {
  Banco b = miBanco();
  partir(b, 150, {{777, true}});
  {
    std::vector<Recarga> viejas = {{9, 1, 1}};
    assert(b.p->recargas.reemplazar(viejas));
  }
  // el segundo antes que el primero: no vale, y no se toca nada
  assert(b.manda(LISTAS_FOTO_2) == Res::SALTO);
  assert(b.version() == 150 && b.bloqueada(777));
  // el primero reconstruye desde cero; la versión es la intermedia (mayor que 2^31: no es una versión real)
  assert(b.manda(LISTAS_FOTO_1) == Res::APLICADA);
  assert(b.version() == FOTO_INTERMEDIA_1 && b.version() > 0x80000000u);
  assert(!b.bloqueada(777) && b.bloqueada(1000) && b.bloqueada(1002) && !b.bloqueada(1003));
  // el tercero se salta el segundo: no vale
  assert(b.manda(LISTAS_FOTO_3) == Res::SALTO && b.version() == FOTO_INTERMEDIA_1);
  assert(b.manda(LISTAS_FOTO_2) == Res::APLICADA && b.version() == FOTO_INTERMEDIA_2);
  assert(b.manda(LISTAS_FOTO_3) == Res::APLICADA && b.version() == 200);
  for (uint32_t id = 1000; id <= 1005; id++) assert(b.bloqueada(id));
  uint32_t hasta;
  assert(b.recargasDe(50, 0, hasta) == 700 && hasta == 2 && b.recargasDe(9, 0, hasta) == 0);
  assert(b.p->lista.cantidad() == 6);
  assert(firmadosGuardados(b) == 3);  // los tres, para pasárselos a otros buses

  // una cadena que se corta: la versión se queda en la intermedia, que el servidor ve como "del futuro" y manda otra foto
  Banco c = miBanco();
  partir(c, 150, {{777, true}});
  assert(c.manda(LISTAS_FOTO_1) == Res::APLICADA);
  c.reiniciar();
  assert(c.version() == FOTO_INTERMEDIA_1 && c.versionEnElHola == FOTO_INTERMEDIA_1);
  // la foto nueva empieza otra vez desde 0 (aunque la versión local sea la intermedia) y se completa
  assert(c.manda(LISTAS_FOTO_1) == Res::APLICADA);
  assert(c.manda(LISTAS_FOTO_2) == Res::APLICADA && c.manda(LISTAS_FOTO_3) == Res::APLICADA);
  assert(c.version() == 200 && c.p->lista.cantidad() == 6);
  printf("  regla 3: foto completa en 3 paquetes encadenados, orden obligatorio y cadena cortada que se repite desde 0 OK\n");
}

// --- Regla 4: bloqueos urgentes ----------------------------------------------------------

void regla4_urgentes() {
  // con los vectores del contrato: el urgente (103 -> 104, tarjeta 15) llega con la versión en 100
  Banco c = contrato();
  partir(c, 100, {{4, true}});
  assert(c.manda(vec("listas_urgente")) == Res::URGENTE);
  assert(c.bloqueada(15) && c.version() == 100);  // bloqueada, y la versión no cambia
  assert(firmadosGuardados(c) == 1);
  // cuando llegan los cambios en orden se aplican, y el urgente sigue bloqueado (la lista normal no lo trae)
  assert(c.manda(vec("listas_normal")) == Res::APLICADA && c.version() == 103);
  assert(c.bloqueada(15) && c.bloqueada(9) && !c.bloqueada(4));
  // el urgente sobrevive a un reinicio
  c.reiniciar();
  assert(c.bloqueada(15) && c.version() == 103);

  Banco b = miBanco();
  partir(b, 205);
  assert(b.manda(LISTAS_U_3000_212) == Res::URGENTE);
  assert(b.bloqueada(3000) && b.version() == 205 && b.p->lista.cantidadUrgentes() == 1);
  // un urgente cuya versión ya se tiene no se aplica: podría volver a bloquear lo que un cambio posterior desbloqueó
  assert(b.manda(LISTAS_U_4000_VIEJO) == Res::REPETIDA && !b.bloqueada(4000));
  // llega el cambio en orden con el mismo bloqueo: se aplica sin duplicar nada, y el urgente ya no hace falta
  assert(b.manda(LISTAS_N3_205_212) == Res::APLICADA && b.version() == 212);
  assert(b.bloqueada(3000) && b.bloqueada(3002) && b.p->lista.cantidadUrgentes() == 0);
  assert(b.p->lista.cantidad() == 2);  // 3000 una sola vez
  assert(b.manda(LISTAS_U_3000_212) == Res::REPETIDA && b.p->lista.cantidadUrgentes() == 0);
  // un urgente que luego se desbloquea en orden: el desbloqueo vale (el urgente no lo deja bloqueado para siempre)
  assert(b.manda(LISTAS_U_3001_213) == Res::URGENTE && b.bloqueada(3001) && b.version() == 212);
  assert(b.manda(LISTAS_N4_212_214) == Res::APLICADA && b.version() == 214);
  assert(!b.bloqueada(3001) && b.bloqueada(3003) && b.p->lista.cantidadUrgentes() == 0);
  // un desbloqueo urgente se ignora (desbloquear va siempre en orden)
  {
    std::vector<ListaNegra::Cambio> c5 = {{5000, true}};
    assert(b.p->lista.aplicarCambios(c5.data(), 1));
    while (b.p->lista.avanzar(1024) == ListaNegra::Progreso::EN_CURSO) {
    }
  }
  assert(b.manda(LISTAS_U_DESBLOQUEO_5000) == Res::URGENTE && b.bloqueada(5000));
  // una foto completa no desbloquea un urgente que aún no recoge
  Banco f = miBanco();
  partir(f, 205);
  assert(f.manda(LISTAS_U_3000_212) == Res::URGENTE);
  assert(f.manda(LISTAS_FOTO_1) == Res::APLICADA);
  assert(f.bloqueada(3000) && f.bloqueada(1000));
  printf("  regla 4: urgentes no contiguos sin tocar la versión, sin duplicar al llegar en orden, desbloqueables y viejos ignorados OK\n");
}

// --- Regla 5: firma ----------------------------------------------------------------------

void regla5_firma() {
  Banco b = miBanco();
  partir(b, 200);
  const Bytes bueno = bytes(LISTAS_N1_200_205);

  // un bit de la firma cambiado
  Bytes malo = bueno;
  malo.back() ^= 0x01;
  b.servidor(malo.data(), malo.size());
  assert(b.alm->llamadas == 0 && b.version() == 200 && !b.bloqueada(2000));
  assert(b.sinc->ultimoRechazo() == sp::Motivo::FIRMA && b.sinc->listasRechazadasPorFirma() == 1);
  assert(b.alm->incidencias.size() == 1 && b.alm->incidencias[0] == "lista firma invalida");
  assert(strlen(SincronizadorV2::DETALLE_LISTA_FALSA) <= 20);  // cabe en el detalle de un evento

  // un cambio en el cuerpo (otra tarjeta) con la firma original
  Bytes otro = bueno;
  otro[sp::TAM_CABECERA + 12 + 4] ^= 0x10;
  b.servidor(otro.data(), otro.size());
  assert(b.alm->llamadas == 0 && !b.bloqueada(2000) && b.sinc->listasRechazadasPorFirma() == 2);
  // como mucho una incidencia por minuto (si no, un paquete falso repetido inunda el registro)
  assert(b.alm->incidencias.size() == 1);
  b.ms += 61000;
  b.servidor(malo.data(), malo.size());
  assert(b.alm->incidencias.size() == 2 && b.sinc->listasRechazadasPorFirma() == 3);

  // un urgente con la firma rota no bloquea nada
  Bytes urg = bytes(LISTAS_U_3000_212);
  urg.back() ^= 0x80;
  b.servidor(urg.data(), urg.size());
  assert(b.alm->llamadas == 0 && !b.bloqueada(3000));

  // un LISTAS de otro servidor (otra clave) tampoco: se descarta igual
  Banco c = contrato();  // con la clave pública del contrato, los paquetes de prueba no valen
  partir(c, 200);
  c.servidor(bueno.data(), bueno.size());
  assert(c.alm->llamadas == 0 && c.alm->incidencias.size() == 1 && c.version() == 200);

  // con la firma buena y para otro validador (108): no es para este y no se aplica
  assert(b.alm->llamadas == 0);
  b.servidor(LISTAS_N_PARA_108, sizeof(LISTAS_N_PARA_108));
  assert(b.alm->llamadas == 0 && b.version() == 200);
  // y el bueno, justo después, se aplica
  assert(b.manda(bueno) == Res::APLICADA && b.version() == 205 && b.bloqueada(2000));

  // con los vectores del contrato: la firma alterada en el último byte
  Banco k = contrato();
  partir(k, 100);
  Bytes normal = vec("listas_normal");
  normal.back() ^= 0x01;
  k.servidor(normal.data(), normal.size());
  assert(k.alm->llamadas == 0 && k.version() == 100 && !k.bloqueada(9) && k.alm->incidencias.size() == 1);
  printf("  regla 5: firma inválida (firma, cuerpo, urgente), una incidencia por minuto y paquetes de otro validador descartados OK\n");
}

// --- Tarifas -----------------------------------------------------------------------------

void tarifas() {
  Banco b = miBanco();
  sp::Tarifa t;
  assert(b.manda(TARIFAS_A) == Res::APLICADA);
  assert(b.p->estado.estado().versionTarifas == TARIFAS_A_VERSION && b.alm->versionTarifas() == TARIFAS_A_VERSION);
  assert(b.p->tarifas.tarifa(1, t) && t.precio == 130 && strcmp(t.nombre, "general") == 0 && b.p->tarifas.tarifa(2, t) && t.precio == 80);
  assert(b.p->estado.estado().versionListas == 0);  // las tarifas no tocan la versión de las listas
  // una nueva (más reciente) sustituye a la anterior entera
  assert(b.manda(TARIFAS_B) == Res::APLICADA && b.p->estado.estado().versionTarifas == TARIFAS_B_VERSION);
  assert(b.p->tarifas.tarifa(1, t) && t.precio == 150 && b.p->tarifas.tarifa(3, t) && t.precio == 0 && b.p->tarifas.cantidad() == 3);
  // una más vieja que la que se tiene (un paquete viejo reenviado) se descarta
  assert(b.manda(TARIFAS_VIEJA) == Res::REPETIDA);
  assert(b.p->tarifas.tarifa(1, t) && t.precio == 150 && b.p->estado.estado().versionTarifas == TARIFAS_B_VERSION);
  // las mismas otra vez no cambian nada
  assert(b.manda(TARIFAS_B) == Res::APLICADA && b.p->tarifas.cantidad() == 3);
  // sobreviven a un reinicio y el HOLA anuncia su versión
  b.reiniciar();
  assert(b.p->tarifas.cantidad() == 3 && b.p->tarifas.tarifa(1, t) && t.precio == 150 && b.alm->versionTarifas() == TARIFAS_B_VERSION);
  // con la versión de las listas intacta si ya había una
  partir(b, 77);
  assert(b.manda(TARIFAS_A) == Res::REPETIDA && b.version() == 77);

  // el vector del contrato
  Banco c = contrato();
  assert(c.manda(vec("tarifas")) == Res::APLICADA && c.p->tarifas.cantidad() == 3);
  assert(c.p->tarifas.tarifa(3, t) && strcmp(t.nombre, "Libre") == 0);
  printf("  tarifas: se aplican y sustituyen, las viejas se descartan y sobreviven al reinicio OK\n");
}

// --- Un corte de energía en cada operación de aplicar --------------------------------------

void cortes() {
  for (int caso = 0; caso < 2; caso++) {
    // caso 0: cambios en orden (200 -> 205); caso 1: una foto completa (desde 0)
    Banco base = miBanco();
    partir(base, 200, {{1000, true}, {1001, true}, {7, true}});
    {
      std::vector<Recarga> r = {{60, 9, 1}};
      assert(base.p->recargas.reemplazar(r));
    }
    const Bytes paquete = caso == 0 ? bytes(LISTAS_N1_200_205) : bytes(LISTAS_FOTO_1);
    const uint32_t versionNueva = caso == 0 ? 205 : FOTO_INTERMEDIA_1;
    int cortes = 0, conVersionVieja = 0, conVersionNueva = 0;
    for (long k = 0;; k++) {
      Banco t = miBanco();
      t.fs = base.fs;
      t.reiniciar();
      t.fs.programarCorte(k);
      bool cortado = false;
      try {
        t.servidor(paquete.data(), paquete.size());
      } catch (CorteDeEnergia &) {
        cortado = true;
      }
      t.fs.sinCorte();
      if (!cortado) break;
      cortes++;
      t.fs.aplicarCorte();
      t.reiniciar();
      const uint32_t v = t.version();
      assert(v == 200 || v == versionNueva);  // la versión nunca queda a medias
      (v == 200 ? conVersionVieja : conVersionNueva)++;
      if (v == versionNueva) {  // con la versión nueva, los datos son los nuevos
        if (caso == 0) assert(t.bloqueada(2000) && !t.bloqueada(1001));
        else assert(t.bloqueada(1000) && !t.bloqueada(7));
      }
      // el servidor repite el paquete en el siguiente HOLA: se llega siempre al estado nuevo
      if (v == 200) {
        assert(t.manda(paquete) == Res::APLICADA);
      } else {
        t.servidor(paquete.data(), paquete.size());  // ya aplicado: se descarta (o, en la foto, se vuelve a aplicar)
      }
      if (caso == 0) {
        assert(t.bloqueada(2000) && !t.bloqueada(1001) && t.bloqueada(1000) && t.bloqueada(7) && t.version() == 205);
        uint32_t hasta;
        assert(t.recargasDe(60, 0, hasta) == 100 && hasta == 1);  // la 1 que trae el cambio; la 9 de antes sigue (no es foto)
      } else {
        assert(t.bloqueada(1000) && t.bloqueada(1002) && !t.bloqueada(7) && t.version() == FOTO_INTERMEDIA_1);
      }
    }
    assert(cortes >= 8 && conVersionVieja > 0 && conVersionNueva > 0);
    printf("  %s: %d cortes de energía; la versión nunca queda por delante de los datos (%d con la vieja, %d con la nueva) OK\n",
           caso == 0 ? "cambios en orden" : "foto completa", cortes, conVersionVieja, conVersionNueva);
  }
}

}  // namespace

int main() {
  regla1_versionado();
  regla2_contiguidad();
  regla3_foto_encadenada();
  regla4_urgentes();
  regla5_firma();
  tarifas();
  cortes();
  printf("M4-02 LISTAS y TARIFAS: las cinco reglas de C1 5.5, tarifas y cortes de energía OK\n");
  return 0;
}
