// M3-03: lista negra binaria ordenada, con cortes de energía simulados.
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include <algorithm>
#include <set>
#include <vector>

#include <Crc32.h>
#include <ListaNegra.h>

#include "fs_falso.h"

namespace {

using Cambio = ListaNegra::Cambio;
using Progreso = ListaNegra::Progreso;
const char *BIN = "/lista_negra.bin";

// Lo que de verdad hay en el archivo: los ids que se leen del .bin (sin el pie de 16 B)
std::set<uint32_t> enDisco(FsFalso &fs, const char *ruta = BIN) {
  std::set<uint32_t> s;
  if (!fs.archivos.count(ruta)) return s;
  const std::vector<uint8_t> &b = fs.archivos[ruta].cache;
  assert(b.size() >= ListaNegra::TAM_PIE && (b.size() - ListaNegra::TAM_PIE) % 4 == 0);
  uint32_t anterior = 0;
  for (size_t i = 0; i < (b.size() - ListaNegra::TAM_PIE) / 4; i++) {
    uint32_t v;
    memcpy(&v, &b[i * 4], 4);
    assert(i == 0 || v > anterior);  // estrictamente creciente
    anterior = v;
    s.insert(v);
  }
  return s;
}

// Termina una mezcla; devuelve cuántas llamadas a avanzar() hicieron falta
int terminar(ListaNegra &l, size_t porLlamada = 256) {
  int llamadas = 0;
  for (;;) {
    llamadas++;
    const Progreso p = l.avanzar(porLlamada);
    if (p == Progreso::TERMINADA) return llamadas;
    assert(p == Progreso::EN_CURSO);
  }
}

void aplicar(ListaNegra &l, const std::vector<Cambio> &c) {
  assert(l.aplicarCambios(c.data(), c.size()));
  terminar(l);
}

void aplicarAlModelo(std::set<uint32_t> &m, const std::vector<Cambio> &c) {
  // de los repetidos vale el último, como en la lista
  for (const Cambio &x : c) {
    if (x.alta)
      m.insert(x.id);
    else
      m.erase(x.id);
  }
}

void crcEncadenado() {
  const char *a = "1234", *b = "56789";
  assert(crc32(b, 5, crc32(a, 4)) == crc32("123456789", 9));
  assert(crc32(b, 5, crc32(a, 4)) == 0xCBF43926u);
}

void basico() {
  FsFalso fs;
  ListaNegra l(fs);
  assert(!l.iniciar());  // validador nuevo: sin lista
  assert(l.cantidad() == 0 && !l.bloqueada(5));

  // 7 se bloquea y se desbloquea: vale el último; 10 está repetido
  aplicar(l, {{5, true}, {10, true}, {3, true}, {10, true}, {7, true}, {7, false}, {99, false}});
  assert(l.cantidad() == 3 && l.generacion() == 1);
  for (uint32_t id : {3u, 5u, 10u}) assert(l.bloqueada(id));
  for (uint32_t id : {0u, 4u, 7u, 99u, 11u, 0xFFFFFFFFu}) assert(!l.bloqueada(id));
  assert((enDisco(fs) == std::set<uint32_t>{3, 5, 10}));

  // los mismos cambios otra vez: el mismo resultado
  aplicar(l, {{5, true}, {10, true}, {3, true}, {7, false}});
  assert((enDisco(fs) == std::set<uint32_t>{3, 5, 10}) && l.cantidad() == 3 && l.generacion() == 2);

  // altas y bajas sobre lo que ya hay y sobre lo que no
  aplicar(l, {{5, false}, {10, true}, {11, true}, {1000, false}, {2, true}});
  assert((enDisco(fs) == std::set<uint32_t>{2, 3, 10, 11}));
  assert(l.bloqueada(2) && l.bloqueada(11) && !l.bloqueada(5) && !l.bloqueada(1000));

  // reabrir: lo mismo
  ListaNegra r(fs);
  assert(r.iniciar() && r.cantidad() == 4 && r.generacion() == 3 && r.bloqueada(11) && !r.bloqueada(5));

  // no queda ningún .tmp
  assert(!fs.existe("/lista_negra.tmp"));
}

void consultaDuranteLaMezcla() {
  FsFalso fs;
  ListaNegra l(fs);
  aplicar(l, {{10, true}, {20, true}, {30, true}});
  std::vector<Cambio> c = {{20, false}, {25, true}};
  assert(l.aplicarCambios(c.data(), c.size()));
  assert(l.mezclando());
  assert(!l.aplicarCambios(c.data(), c.size()));  // una sola a la vez
  assert(l.avanzar(1) == Progreso::EN_CURSO);
  // a medias, la lista vieja sigue mandando
  assert(l.bloqueada(20) && !l.bloqueada(25) && l.cantidad() == 3);
  terminar(l, 1);
  assert(!l.mezclando() && !l.bloqueada(20) && l.bloqueada(25) && l.cantidad() == 3);
  assert(l.avanzar() == Progreso::OCIOSA);
}

void cincuentaMilIdentificadores() {
  FsFalso fs;
  ListaNegra l(fs);
  std::set<uint32_t> modelo;
  std::vector<Cambio> carga;
  for (uint32_t i = 0; i < 50000; i++) carga.push_back({i * 7 + 3, true});
  aplicarAlModelo(modelo, carga);
  assert(l.aplicarCambios(carga.data(), carga.size()));
  terminar(l);
  assert(l.cantidad() == 50000);

  // 120 cambios (el máximo de una LISTAS): 60 bajas de ids que están y 60 altas nuevas
  std::vector<Cambio> cambios;
  for (uint32_t i = 0; i < 60; i++) cambios.push_back({(i * 811) * 7 + 3, false});
  for (uint32_t i = 0; i < 60; i++) cambios.push_back({1000000 + i * 13, true});
  aplicarAlModelo(modelo, cambios);
  assert(l.aplicarCambios(cambios.data(), cambios.size()));
  // por partes: ninguna llamada pasa de 256 entradas, y al menos 50 000 / 256 llamadas
  const int llamadas = terminar(l, 256);
  assert(llamadas >= 50000 / 256 && llamadas < 50000 / 256 + 8);

  assert(l.cantidad() == modelo.size() && enDisco(fs) == modelo);
  uint32_t semilla = 99;
  for (int i = 0; i < 30000; i++) {
    semilla = semilla * 1664525u + 1013904223u;
    const uint32_t id = (semilla >> 4) % 1500000;
    assert(l.bloqueada(id) == (modelo.count(id) > 0));
  }
  for (const Cambio &c : cambios) assert(l.bloqueada(c.id) == c.alta);
  // y tras reiniciar: lo mismo
  ListaNegra r(fs);
  assert(r.iniciar() && r.cantidad() == modelo.size());
  for (const Cambio &c : cambios) assert(r.bloqueada(c.id) == c.alta);
}

void limiteDeTamano() {
  FsFalso fs;
  ListaNegra l(fs);
  std::vector<Cambio> carga;
  for (uint32_t i = 0; i < ListaNegra::MAX_IDS; i++) carga.push_back({i + 1, true});
  assert(l.aplicarCambios(carga.data(), carga.size()));
  terminar(l, 100000);
  assert(l.cantidad() == ListaNegra::MAX_IDS);
  const Cambio una = {0xFFFFFF00u, true};
  assert(!l.aplicarCambios(&una, 1));  // se pasaría del máximo
  const Cambio baja = {1, false};
  assert(l.aplicarCambios(&baja, 1));  // quitar sí cabe
  terminar(l);
  assert(l.cantidad() == ListaNegra::MAX_IDS - 1 && !l.bloqueada(1) && l.bloqueada(2));
}

// Un corte de energía en cada operación posible de la mezcla: tras recuperar, la lista es la de
// antes o la de después, nunca una mezcla ni una lista rota; y repitiendo los cambios se llega a la nueva.
void corteEnCadaOperacionDeLaMezcla(uint32_t ids) {
  FsFalso base;
  std::set<uint32_t> antes;
  {
    ListaNegra l(base);
    std::vector<Cambio> carga;
    for (uint32_t i = 0; i < ids; i++) carga.push_back({i * 3 + 1, true});
    aplicarAlModelo(antes, carga);
    aplicar(l, carga);
    // una segunda generación, para que haya .bak
    std::vector<Cambio> otros = {{1, false}, {5000, true}};
    aplicarAlModelo(antes, otros);
    aplicar(l, otros);
  }
  const std::vector<Cambio> cambios = {{4, false}, {7, false}, {10, true}, {9999, true}, {2, true}, {5000, false}};
  std::set<uint32_t> despues = antes;
  aplicarAlModelo(despues, cambios);
  assert(antes != despues);

  int cortes = 0, vieja = 0, nueva = 0;
  for (long k = 0;; k++) {
    FsFalso fs = base;
    fs.programarCorte(k);
    bool cortado = false;
    try {
      ListaNegra l(fs);
      l.iniciar();
      assert(l.aplicarCambios(cambios.data(), cambios.size()));
      terminar(l, 64);
    } catch (CorteDeEnergia &) {
      cortado = true;
    }
    fs.sinCorte();
    if (!cortado) {
      assert(enDisco(fs) == despues);
      break;
    }
    cortes++;
    fs.aplicarCorte();
    ListaNegra r(fs);
    assert(r.iniciar());
    const std::set<uint32_t> hay = enDisco(fs);
    assert(hay == antes || hay == despues);
    (hay == antes ? vieja : nueva)++;
    assert(r.cantidad() == hay.size());
    for (const Cambio &c : cambios) assert(r.bloqueada(c.id) == (hay.count(c.id) > 0));
    // quien pidió los cambios los repite (no avanzó la versión de listas) y llega a lo mismo
    aplicar(r, cambios);
    assert(enDisco(fs) == despues);
    ListaNegra otra(fs);
    assert(otra.iniciar() && otra.cantidad() == despues.size());
    assert(k < 5000);
  }
  assert(cortes >= 8 && vieja > 0 && nueva > 0);  // hubo cortes en todas las fases
  printf("  mezcla de %u ids: %d cortes (%d dejaron la lista vieja, %d la nueva)\n", (unsigned)ids, cortes, vieja, nueva);
}

void copiaCorruptaCaeEnLaAnterior() {
  FsFalso fs;
  ListaNegra l(fs);
  aplicar(l, {{1, true}, {2, true}, {3, true}});
  const std::set<uint32_t> primera = enDisco(fs);
  aplicar(l, {{4, true}});
  assert(enDisco(fs).size() == 4 && fs.existe("/lista_negra.bak"));

  // se corrompe el último id del .bin sin romper el orden: solo el CRC lo nota
  fs.archivos[BIN].durable[11] ^= 0x40;
  fs.archivos[BIN].cache = fs.archivos[BIN].durable;
  ListaNegra r(fs);
  assert(r.iniciar() && r.cantidad() == 3 && enDisco(fs) == primera && r.generacion() == 1);
  assert(r.bloqueada(3) && !r.bloqueada(4));

  // si las dos están rotas, arranca vacía
  fs.archivos[BIN].durable[11] ^= 0x40;
  fs.archivos[BIN].cache = fs.archivos[BIN].durable;
  fs.archivos["/lista_negra.bak"] = FsFalso::Entrada();
  ListaNegra v(fs);
  assert(!v.iniciar() && v.cantidad() == 0 && !v.bloqueada(3));
  aplicar(v, {{9, true}});  // y se puede seguir
  assert(v.bloqueada(9) && v.cantidad() == 1);
}

void desordenadoSeRechaza() {
  // un archivo con el pie y el CRC bien pero los ids desordenados no se acepta
  FsFalso fs;
  uint32_t ids[3] = {5, 3, 9};
  std::vector<uint8_t> b(3 * 4 + ListaNegra::TAM_PIE);
  memcpy(b.data(), ids, 12);
  const uint32_t pie[4] = {0x314E4C53, 1, 3, crc32(ids, 12)};
  memcpy(b.data() + 12, pie, 16);
  fs.archivos[BIN].durable = fs.archivos[BIN].cache = b;
  ListaNegra l(fs);
  assert(!l.iniciar() && l.cantidad() == 0);
}

void escrituraFallidaDejaLaListaIntacta() {
  FsFalso fs;
  ListaNegra l(fs);
  aplicar(l, {{10, true}, {20, true}});
  std::vector<Cambio> c = {{15, true}};
  assert(l.aplicarCambios(c.data(), c.size()));
  fs.agregarFalla = true;
  Progreso p;
  do {
    p = l.avanzar(16);
  } while (p == Progreso::EN_CURSO);
  fs.agregarFalla = false;
  assert(p == Progreso::FALLO && !l.mezclando());
  assert(!fs.existe("/lista_negra.tmp"));
  assert(l.bloqueada(10) && l.bloqueada(20) && !l.bloqueada(15) && l.cantidad() == 2);
  aplicar(l, c);  // y después sí
  assert(l.bloqueada(15) && l.cantidad() == 3);
}

void urgentes() {
  FsFalso fs;
  ListaNegra l(fs);
  aplicar(l, {{100, true}});
  assert(!l.bloqueada(555));
  assert(l.bloqueoUrgente(555) && l.bloqueoUrgente(555) && l.bloqueoUrgente(777));  // el repetido no se duplica
  assert(l.cantidadUrgentes() == 2 && l.bloqueada(555) && l.bloqueada(777) && l.bloqueada(100));

  ListaNegra r(fs);  // sobreviven a un reinicio
  assert(r.iniciar() && r.cantidadUrgentes() == 2 && r.bloqueada(555) && r.bloqueada(777) && !r.bloqueada(556));

  // un registro a medio escribir al final se ignora
  fs.archivos["/urgentes.bin"].cache.insert(fs.archivos["/urgentes.bin"].cache.end(), {1, 2, 3});
  fs.archivos["/urgentes.bin"].durable = fs.archivos["/urgentes.bin"].cache;
  ListaNegra t(fs);
  t.iniciar();
  assert(t.cantidadUrgentes() == 2);

  // solo caben MAX_URGENTES: el más viejo cede su sitio
  FsFalso fs2;
  ListaNegra m(fs2);
  for (uint32_t i = 1; i <= ListaNegra::MAX_URGENTES + 6; i++) assert(m.bloqueoUrgente(i));
  assert(m.cantidadUrgentes() == ListaNegra::MAX_URGENTES);
  assert(!m.bloqueada(1) && !m.bloqueada(6) && m.bloqueada(7) && m.bloqueada(ListaNegra::MAX_URGENTES + 6));
  ListaNegra m2(fs2);
  m2.iniciar();
  assert(m2.cantidadUrgentes() == ListaNegra::MAX_URGENTES && !m2.bloqueada(6) && m2.bloqueada(7));

  // limpiar y reiniciar
  assert(m2.limpiarUrgentes() && m2.cantidadUrgentes() == 0 && !m2.bloqueada(7));
  ListaNegra m3(fs2);
  m3.iniciar();
  assert(m3.cantidadUrgentes() == 0);
}

// Un corte en cada operación de guardar un urgente: o la llamada no llegó a volver (y puede no estar),
// o volvió y entonces el bloqueo sobrevive. Y los que ya estaban nunca se pierden.
void urgenteCorteEnCadaOperacion() {
  for (const bool llenos : {false, true}) {
    FsFalso base;
    {
      ListaNegra l(base);
      const size_t previos = llenos ? ListaNegra::MAX_URGENTES : 5;
      for (uint32_t i = 1; i <= previos; i++) assert(l.bloqueoUrgente(i));
    }
    const uint32_t nuevo = 9000;
    const size_t previos = llenos ? ListaNegra::MAX_URGENTES : 5;
    int cortes = 0;
    for (long k = 0;; k++) {
      FsFalso fs = base;
      fs.programarCorte(k);
      bool cortado = false, volvio = false;
      try {
        ListaNegra l(fs);
        l.iniciar();
        volvio = l.bloqueoUrgente(nuevo);
      } catch (CorteDeEnergia &) {
        cortado = true;
      }
      fs.sinCorte();
      if (!cortado) {
        assert(volvio);
        break;
      }
      cortes++;
      fs.aplicarCorte();
      ListaNegra r(fs);
      r.iniciar();
      // los que había (salvo el más viejo, que cede su sitio si estaba lleno) siguen
      for (uint32_t i = llenos ? 2 : 1; i <= previos; i++) assert(r.bloqueada(i));
      assert(k < 100);
    }
    assert(cortes > 0);
  }
}

void reiniciarListas() {
  FsFalso fs;
  ListaNegra l(fs);
  aplicar(l, {{1, true}, {2, true}, {3, true}});
  assert(l.bloqueoUrgente(50));
  assert(l.reiniciarListas());
  assert(l.cantidad() == 0 && l.cantidadUrgentes() == 0 && !l.bloqueada(1) && !l.bloqueada(50));
  ListaNegra r(fs);
  assert(r.iniciar() && r.cantidad() == 0 && r.cantidadUrgentes() == 0);  // la lista vacía es una lista válida
  aplicar(r, {{8, true}});  // y se reconstruye
  assert(r.cantidad() == 1 && r.bloqueada(8));
}

}  // namespace

int main() {
  crcEncadenado();
  basico();
  consultaDuranteLaMezcla();
  cincuentaMilIdentificadores();
  limiteDeTamano();
  corteEnCadaOperacionDeLaMezcla(700);
  corteEnCadaOperacionDeLaMezcla(9000);  // con vaciados a mitad de la mezcla
  copiaCorruptaCaeEnLaAnterior();
  desordenadoSeRechaza();
  escrituraFallidaDejaLaListaIntacta();
  urgentes();
  urgenteCorteEnCadaOperacion();
  reiniciarListas();
  puts("M3-03 lista negra: 50 000 ids, mezcla por partes, cortes en cada operación, urgentes y reinicio OK");
  return 0;
}
