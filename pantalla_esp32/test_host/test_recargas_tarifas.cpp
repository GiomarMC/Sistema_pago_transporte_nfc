// M3-04: /recargas.ab y /tarifas.ab (dos ranuras, #129), con cortes de energía simulados.
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include <algorithm>
#include <vector>

#include <ProtocoloSP.h>
#include <Recargas.h>
#include <TarifasGuardadas.h>

#include "../test/test_protocolo/vectores.h"
#include "fs_falso.h"

namespace {

using sp::EntradaLista;

EntradaLista pendiente(uint32_t cuenta, uint32_t seq, int32_t monto) {
  return {sp::RECARGA, sp::PENDIENTE, cuenta, seq, static_cast<uint32_t>(monto)};
}
EntradaLista aplicada(uint32_t cuenta, uint32_t seq) { return {sp::RECARGA, sp::APLICADA, cuenta, seq, 0}; }
EntradaLista bloqueo(uint32_t id, uint8_t op = sp::AGREGAR) { return {sp::BLOQUEO, op, id, 0, 0}; }

// Cada paquete se arma en el montón: sp::Listas pesa ~2 KB
std::unique_ptr<sp::Listas> listas(uint32_t desde, std::vector<EntradaLista> e) {
  auto l = std::make_unique<sp::Listas>();
  memset(l.get(), 0, sizeof(*l));
  l->versionDesde = desde;
  l->versionHasta = desde + 1;
  assert(e.size() <= sp::MAX_ENTRADAS);
  l->n = e.size();
  std::copy(e.begin(), e.end(), l->entradas);
  return l;
}

bool aplicar(Recargas &r, uint32_t desde, std::vector<EntradaLista> e) { return r.aplicar(*listas(desde, std::move(e))); }

// La carga vigente de un archivo de ranuras (la válida más nueva), leída sobre una copia para no tocar nada
std::vector<uint8_t> cargaVigente(const FsFalso &fs, const char *ruta, const char magia[4]) {
  FsFalso copia = fs;
  ArchivoRanuras a(copia, ruta, magia);
  std::vector<uint8_t> carga;
  a.cargar(carga, 1 << 20);
  return carga;
}

// Lo que dice el archivo: los registros de 12 B, que deben estar ordenados y sin repetir
std::vector<Recarga> enDisco(FsFalso &fs, const char *ruta = "/recargas.ab") {
  std::vector<Recarga> v;
  if (!fs.archivos.count(ruta)) return v;
  const std::vector<uint8_t> b = cargaVigente(fs, ruta, "REC1");
  assert(b.size() % 12 == 0);
  for (size_t i = 0; i < b.size() / 12; i++) {
    Recarga r;
    memcpy(&r.cuenta, &b[i * 12], 4);
    memcpy(&r.seq, &b[i * 12 + 4], 4);
    memcpy(&r.monto, &b[i * 12 + 8], 4);
    assert(v.empty() || v.back().cuenta < r.cuenta || (v.back().cuenta == r.cuenta && v.back().seq < r.seq));
    v.push_back(r);
  }
  return v;
}

// La búsqueda que hacía Almacen con el texto: recorrer todo hasta no avanzar
void modelo(const std::vector<Recarga> &todas, uint32_t cuenta, uint32_t ultima, int32_t &total, uint32_t &hasta) {
  total = 0;
  hasta = ultima;
  bool avanzo = true;
  while (avanzo) {
    avanzo = false;
    for (const Recarga &r : todas) {
      if (r.cuenta == cuenta && r.seq == hasta + 1) {
        total += r.monto;
        hasta = r.seq;
        avanzo = true;
      }
    }
  }
}

void pend(const Recargas &r, uint32_t cuenta, uint32_t ultima, int32_t esperadoTotal, uint32_t esperadoHasta) {
  int32_t total;
  uint32_t hasta;
  r.pendientes(cuenta, ultima, total, hasta);
  assert(total == esperadoTotal && hasta == esperadoHasta);
}

void consultasYFoto() {
  FsFalso fs;
  Recargas r(fs);
  assert(!r.iniciar() && r.cantidad() == 0);
  pend(r, 7, 0, 0, 0);  // sin recargas: no cambia nada

  // fuera de orden a propósito; la cuenta 7 tiene un hueco (seq 4) y la 9 empieza en 3
  assert(aplicar(r, 0, {pendiente(9, 3, 300), pendiente(7, 2, 200), pendiente(7, 1, 100), pendiente(7, 3, 50), pendiente(7, 5, 999)}));
  assert(r.cantidad() == 5);
  const std::vector<Recarga> disco = enDisco(fs);
  assert(disco.size() == 5 && disco[0].cuenta == 7 && disco[0].seq == 1 && disco[4].cuenta == 9);

  pend(r, 7, 0, 350, 3);  // 1, 2 y 3; la 5 no porque falta la 4
  pend(r, 7, 1, 250, 3);  // desde la 2
  pend(r, 7, 3, 0, 3);    // la 4 no existe
  pend(r, 7, 4, 999, 5);  // la 5 sí
  pend(r, 7, 5, 0, 5);
  pend(r, 9, 0, 0, 0);    // la 9 no tiene la 1
  pend(r, 9, 2, 300, 3);
  pend(r, 8, 0, 0, 0);    // cuenta sin recargas
  pend(r, 7, UINT32_MAX, 0, UINT32_MAX);

  // tras reiniciar, lo mismo
  Recargas otra(fs);
  assert(otra.iniciar() && otra.cantidad() == 5);
  pend(otra, 7, 0, 350, 3);
  pend(otra, 9, 2, 300, 3);

  // una foto nueva reemplaza todo, aunque traiga menos
  assert(aplicar(r, 0, {pendiente(7, 1, 10)}));
  assert(r.cantidad() == 1);
  pend(r, 9, 2, 0, 2);
  pend(r, 7, 0, 10, 1);
  // y una foto vacía deja todo vacío
  assert(aplicar(r, 0, {}));
  assert(r.cantidad() == 0 && enDisco(fs).empty());
  printf("  consultas, orden y foto completa OK\n");
}

void cambiosEIdempotencia() {
  FsFalso fs;
  Recargas r(fs);
  assert(aplicar(r, 0, {pendiente(7, 1, 100), pendiente(7, 2, 200), pendiente(8, 1, 5)}));

  // alta, cambio de monto, baja y un BLOQUEO que no es de aquí
  assert(aplicar(r, 5, {pendiente(7, 3, 30), pendiente(7, 1, 111), aplicada(7, 2), bloqueo(42), bloqueo(43, sp::QUITAR)}));
  assert(r.cantidad() == 3);
  pend(r, 7, 0, 111, 1);  // la 2 ya no está: se corta ahí
  pend(r, 7, 2, 30, 3);
  pend(r, 8, 0, 5, 1);
  assert(enDisco(fs) == r.todas());

  // aplicar lo mismo otra vez no cambia nada ni escribe
  fs.programarCorte(0);  // si intentara escribir, se cortaría
  assert(aplicar(r, 5, {pendiente(7, 3, 30), pendiente(7, 1, 111), aplicada(7, 2), bloqueo(42)}));
  fs.sinCorte();
  assert(r.cantidad() == 3);
  // quitar algo que no está tampoco escribe
  fs.programarCorte(0);
  assert(aplicar(r, 6, {aplicada(99, 99)}));
  fs.sinCorte();

  // de las repetidas vale la última
  assert(aplicar(r, 7, {pendiente(7, 4, 1), pendiente(7, 4, 2), pendiente(7, 5, 9), aplicada(7, 5)}));
  pend(r, 7, 2, 32, 4);
  // un paquete solo de bloqueos no toca las recargas
  assert(aplicar(r, 8, {bloqueo(1), bloqueo(2)}));
  assert(r.cantidad() == 4);
  // una foto con solo bloqueos (versionDesde = 0) sí vacía las recargas
  assert(aplicar(r, 0, {bloqueo(1)}));
  assert(r.cantidad() == 0);
  printf("  altas, bajas, cambios, repetidas e idempotencia OK\n");
}

void reemplazarTodo() {
  FsFalso fs;
  Recargas r(fs);
  assert(aplicar(r, 0, {pendiente(7, 1, 100), pendiente(8, 1, 5)}));
  // desordenadas y con una repetida: vale la última
  assert(r.reemplazar({{9, 2, 20}, {7, 2, 7}, {9, 1, 10}, {9, 2, 21}}));
  assert(r.cantidad() == 3);
  pend(r, 9, 0, 31, 2);
  pend(r, 7, 0, 0, 0);
  assert(enDisco(fs) == r.todas());
  // lo mismo otra vez no escribe
  fs.programarCorte(0);
  assert(r.reemplazar({{9, 1, 10}, {9, 2, 21}, {7, 2, 7}}));
  fs.sinCorte();
  assert(r.reemplazar({}) && r.cantidad() == 0);
  std::vector<Recarga> muchas;
  for (uint32_t i = 0; i <= Recargas::MAX_RECARGAS; i++) muchas.push_back({1, i + 1, 1});
  assert(!r.reemplazar(muchas) && r.cantidad() == 0);  // una de más: no cabe, queda como estaba
  printf("  reemplazar la lista entera OK\n");
}

void limiteDeTamano() {
  FsFalso fs;
  Recargas r(fs);
  for (uint32_t i = 0; i < Recargas::MAX_RECARGAS; i += 100) {
    std::vector<EntradaLista> e;
    for (uint32_t j = i; j < i + 100 && j < Recargas::MAX_RECARGAS; j++) e.push_back(pendiente(1 + j / 50, 1 + j % 50, j));
    assert(aplicar(r, i == 0 ? 0 : 5, e));
  }
  assert(r.cantidad() == Recargas::MAX_RECARGAS);
  assert(enDisco(fs).size() == Recargas::MAX_RECARGAS);
  assert(!aplicar(r, 5, {pendiente(900000, 1, 1)}));  // no cabe
  assert(r.cantidad() == Recargas::MAX_RECARGAS && enDisco(fs).size() == Recargas::MAX_RECARGAS);
  assert(aplicar(r, 5, {aplicada(1, 1), pendiente(900000, 1, 1)}));  // quitar y poner sí cabe
  assert(r.cantidad() == Recargas::MAX_RECARGAS);
  Recargas otra(fs);
  assert(otra.iniciar() && otra.cantidad() == Recargas::MAX_RECARGAS);
  printf("  límite de %zu recargas OK\n", Recargas::MAX_RECARGAS);
}

void comoElModeloViejo() {
  FsFalso fs;
  Recargas r(fs);
  uint32_t azar = 7;
  auto sig = [&]() { return azar = azar * 1664525u + 1013904223u, azar >> 8; };
  std::vector<EntradaLista> e;
  for (int i = 0; i < 100; i++) e.push_back(pendiente(1 + sig() % 6, 1 + sig() % 12, int32_t(sig() % 1000) - 200));
  assert(aplicar(r, 0, e));
  for (uint32_t cuenta = 0; cuenta <= 7; cuenta++) {
    for (uint32_t ultima = 0; ultima <= 13; ultima++) {
      int32_t t1, t2;
      uint32_t h1, h2;
      r.pendientes(cuenta, ultima, t1, h1);
      modelo(r.todas(), cuenta, ultima, t2, h2);
      assert(t1 == t2 && h1 == h2);
    }
  }
  printf("  mismo resultado que la búsqueda de antes (%zu recargas, 112 consultas) OK\n", r.cantidad());
}

void cortesDeEnergia() {
  FsFalso base;
  {
    Recargas r(base);
    assert(aplicar(r, 0, {pendiente(7, 1, 100), pendiente(7, 2, 200), pendiente(8, 1, 5)}));
    assert(aplicar(r, 5, {pendiente(8, 2, 6)}));  // una segunda generación: las dos ranuras con algo
  }
  const std::vector<Recarga> antes = enDisco(base);
  FsFalso copia = base;
  Recargas m(copia);
  m.iniciar();
  assert(aplicar(m, 6, {aplicada(7, 1), pendiente(7, 3, 30), pendiente(9, 1, 1)}));
  const std::vector<Recarga> despues = m.todas();
  assert(antes != despues);

  int cortes = 0, vieja = 0, nueva = 0;
  bool terminado = false;
  for (long k = 0; !terminado; k++) {
    for (uint32_t semilla = 1; semilla <= 16; semilla++) {  // lo que sobrevive de lo no vaciado cambia con el azar
      FsFalso fs = base;
      fs.azar = semilla * 7919;
      fs.programarCorte(k);
      bool cortado = false;
      try {
        Recargas r(fs);
        r.iniciar();
        assert(aplicar(r, 6, {aplicada(7, 1), pendiente(7, 3, 30), pendiente(9, 1, 1)}));
      } catch (CorteDeEnergia &) {
        cortado = true;
      }
      fs.sinCorte();
      if (!cortado) {
        assert(enDisco(fs) == despues);
        terminado = true;
        break;
      }
      cortes++;
      fs.aplicarCorte();
      Recargas r(fs);
      assert(r.iniciar());
      assert(r.todas() == antes || r.todas() == despues);  // nunca una mezcla
      (r.todas() == antes ? vieja : nueva)++;
      assert(enDisco(fs) == r.todas());
      // quien lo pidió lo repite (no avanzó la versión de listas) y llega a lo mismo
      assert(aplicar(r, 6, {aplicada(7, 1), pendiente(7, 3, 30), pendiente(9, 1, 1)}));
      assert(r.todas() == despues);
    }
  }
  assert(cortes >= 16 && vieja > 0 && nueva > 0);
  printf("  %d cortes (%d dejaron la lista vieja, %d la nueva) OK\n", cortes, vieja, nueva);
}

void copiaCorruptaYEscrituraFallida() {
  FsFalso fs;
  Recargas r(fs);
  assert(aplicar(r, 0, {pendiente(7, 1, 100)}));
  assert(aplicar(r, 5, {pendiente(7, 2, 200)}));  // ranura 0 con 1 (generación 1), ranura 1 con 2 (generación 2)
  const std::vector<Recarga> dos = r.todas();
  assert(fs.archivos.count("/recargas.ab") && dos.size() == 2);
  const size_t R = 512, CAB = ArchivoRanuras::TAM_CABECERA;

  // un bit cambiado en la ranura vigente: el CRC la rechaza y se usa la otra
  FsFalso mala = fs;
  mala.archivos["/recargas.ab"].durable[R + CAB + 3] ^= 0x40;
  mala.archivos["/recargas.ab"].cache = mala.archivos["/recargas.ab"].durable;
  Recargas c(mala);
  assert(c.iniciar() && c.cantidad() == 1 && c.todas()[0].seq == 1);
  assert(enDisco(mala).size() == 1);
  // con las ranuras intercambiadas (ambas válidas) manda la generación más nueva, no la posición
  FsFalso cruzada = fs;
  auto &x = cruzada.archivos["/recargas.ab"].durable;
  std::swap_ranges(x.begin(), x.begin() + R, x.begin() + R);
  cruzada.archivos["/recargas.ab"].cache = x;
  Recargas g(cruzada);
  assert(g.iniciar() && g.todas() == dos);
  assert(enDisco(cruzada) == dos);
  // y si están corruptas las dos, arranca vacía
  mala.archivos["/recargas.ab"].durable[0] ^= 0xFF;
  mala.archivos["/recargas.ab"].cache = mala.archivos["/recargas.ab"].durable;
  Recargas d(mala);
  assert(!d.iniciar() && d.cantidad() == 0);
  // un archivo de otro tamaño no es de este formato (las escrituras en su sitio no cambian el tamaño): no vale
  FsFalso corto = fs;
  corto.archivos["/recargas.ab"].durable.resize(700);
  corto.archivos["/recargas.ab"].cache = corto.archivos["/recargas.ab"].durable;
  Recargas e(corto);
  assert(!e.iniciar() && e.cantidad() == 0);

  // la tarjeta falla al escribir: el cambio no vale y todo sigue como estaba
  fs.agregarFalla = true;
  assert(!aplicar(r, 6, {pendiente(7, 3, 1)}));
  fs.agregarFalla = false;
  assert(r.todas() == dos);
  Recargas f(fs);
  assert(f.iniciar() && f.todas() == dos);
  assert(aplicar(r, 6, {pendiente(7, 3, 1)}) && r.cantidad() == 3);
  printf("  copia corrupta, archivo cortado y escritura fallida OK\n");
}

// --- tarifas ---------------------------------------------------------------------------

sp::Paquete *vectorTarifas() {
  static sp::Paquete p;
  for (size_t i = 0; i < vectores::N_VALIDOS; i++) {
    const auto &v = vectores::VALIDOS[i];
    if (v.tipo != sp::TARIFAS) continue;
    sp::Motivo m;
    const bool ok = sp::desempaquetar(v.datos, v.largo, [](uint16_t) { return vectores::CLAVE_HMAC; },
                                      vectores::CLAVE_PUBLICA, p, &m);
    assert(ok);
    return &p;
  }
  assert(false);
  return nullptr;
}

const uint8_t *cuerpoDelVector(size_t &largo) {
  for (size_t i = 0; i < vectores::N_VALIDOS; i++) {
    const auto &v = vectores::VALIDOS[i];
    if (v.tipo != sp::TARIFAS) continue;
    largo = v.largo - sp::TAM_CABECERA - sp::FIRMA_ECDSA;
    return v.datos + sp::TAM_CABECERA;
  }
  assert(false);
  return nullptr;
}

void tarifasComoEnC1() {
  FsFalso fs;
  TarifasGuardadas t(fs);
  assert(!t.iniciar() && t.cantidad() == 0);
  sp::Tarifa x;
  assert(!t.tarifa(1, x));

  const sp::Paquete *p = vectorTarifas();
  assert(t.guardar(p->tarifas));
  assert(t.version() == p->tarifas.version && t.cantidad() == 3);

  // lo guardado es el cuerpo del paquete TARIFAS, byte a byte
  size_t largo;
  const uint8_t *cuerpo = cuerpoDelVector(largo);
  const std::vector<uint8_t> b = cargaVigente(fs, "/tarifas.ab", "TAR1");
  assert(b.size() == largo && memcmp(b.data(), cuerpo, largo) == 0);

  // la misma lista otra vez no escribe (se cortaría de intentarlo); una distinta sí
  fs.programarCorte(0);
  assert(t.guardar(p->tarifas));
  fs.sinCorte();
  sp::Tarifas distinta = p->tarifas;
  distinta.tarifas[0].precio++;
  fs.programarCorte(100);
  assert(t.guardar(distinta) && t.tarifa(1, x) && x.precio == 101);
  fs.sinCorte();
  assert(t.guardar(p->tarifas));

  assert(t.tarifa(1, x) && x.precio == 100 && strcmp(x.nombre, "General") == 0);
  assert(t.tarifa(2, x) && x.precio == 50 && strcmp(x.nombre, "Estudiante") == 0);
  assert(t.tarifa(3, x) && x.precio == 0 && strcmp(x.nombre, "Libre") == 0);
  assert(!t.tarifa(4, x));

  TarifasGuardadas otra(fs);
  assert(otra.iniciar() && otra.cantidad() == 3 && otra.version() == p->tarifas.version);
  assert(otra.tarifa(2, x) && x.precio == 50 && strcmp(x.nombre, "Estudiante") == 0);

  // la lista nueva sustituye a la vieja entera, y también puede estar vacía
  sp::Tarifas nueva;
  memset(&nueva, 0, sizeof(nueva));
  nueva.version = 77;
  nueva.n = 1;
  nueva.tarifas[0] = {9, 130, "Nocturna"};
  assert(t.guardar(nueva));
  assert(!t.tarifa(1, x) && t.tarifa(9, x) && x.precio == 130);
  nueva.n = 0;
  assert(t.guardar(nueva) && t.cantidad() == 0);
  TarifasGuardadas vacia(fs);
  assert(vacia.iniciar() && vacia.cantidad() == 0 && vacia.version() == 77);

  // el máximo cabe, uno más no
  nueva.n = sp::MAX_TARIFAS;
  for (size_t i = 0; i < sp::MAX_TARIFAS; i++) nueva.tarifas[i] = {static_cast<uint8_t>(i + 1), uint32_t(i), "t"};
  assert(t.guardar(nueva));
  TarifasGuardadas llena(fs);
  assert(llena.iniciar() && llena.cantidad() == sp::MAX_TARIFAS && llena.tarifa(sp::MAX_TARIFAS, x));
  nueva.n = sp::MAX_TARIFAS + 1;
  assert(!t.guardar(nueva) && t.cantidad() == sp::MAX_TARIFAS);
  printf("  cuerpo igual al de C1 (vector del servidor), consultas y sustitución OK\n");
}

void tarifasConCortes() {
  FsFalso base;
  const sp::Paquete *p = vectorTarifas();
  sp::Tarifas v2 = p->tarifas;
  {
    TarifasGuardadas t(base);
    assert(t.guardar(p->tarifas));
    v2.version++;
    v2.tarifas[1].precio = 60;
    assert(t.guardar(v2));  // las dos ranuras con algo
  }
  sp::Tarifas v3 = v2;
  v3.version++;
  v3.tarifas[1].precio = 70;
  int cortes = 0, vieja = 0, nueva = 0;
  bool terminado = false;
  for (long k = 0; !terminado; k++) {
    for (uint32_t semilla = 1; semilla <= 16; semilla++) {
      FsFalso fs = base;
      fs.azar = semilla * 7919;
      fs.programarCorte(k);
      bool cortado = false;
      try {
        TarifasGuardadas t(fs);
        t.iniciar();
        assert(t.guardar(v3));
      } catch (CorteDeEnergia &) {
        cortado = true;
      }
      fs.sinCorte();
      if (!cortado) {
        terminado = true;
        break;
      }
      cortes++;
      fs.aplicarCorte();
      TarifasGuardadas t(fs);
      assert(t.iniciar());
      sp::Tarifa x;
      assert(t.tarifa(2, x));
      assert((t.version() == v2.version && x.precio == 60) || (t.version() == v3.version && x.precio == 70));
      (x.precio == 60 ? vieja : nueva)++;
      assert(t.cantidad() == 3);
    }
  }
  assert(cortes >= 16 && vieja > 0 && nueva > 0);
  printf("  tarifas: %d cortes (%d dejaron las viejas, %d las nuevas) OK\n", cortes, vieja, nueva);
}

}  // namespace

int main() {
  consultasYFoto();
  cambiosEIdempotencia();
  reemplazarTodo();
  limiteDeTamano();
  comoElModeloViejo();
  cortesDeEnergia();
  copiaCorruptaYEscrituraFallida();
  tarifasComoEnC1();
  tarifasConCortes();
  printf("M3-04 recargas y tarifas: estado atómico, orden, idempotencia, cuerpo C1 y cortes OK\n");
  return 0;
}
