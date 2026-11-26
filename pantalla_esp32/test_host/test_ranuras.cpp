// #129: ArchivoRanuras (dos ranuras que se sobrescriben en su sitio) y la migración de ArchivoAtomico desde el
// formato .bin/.tmp/.bak, con cortes de energía simulados.
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include <vector>

#include <ArchivoAtomico.h>
#include <ArchivoRanuras.h>
#include <Crc32.h>

#include "fs_falso.h"

namespace {

const char MAGIA[4] = {'P', 'R', 'U', '1'};

std::vector<uint8_t> datos(size_t n, uint8_t semilla) {
  std::vector<uint8_t> v(n);
  for (size_t i = 0; i < n; i++) v[i] = static_cast<uint8_t>(semilla + i * 13);
  return v;
}

std::vector<uint8_t> leer(FsFalso &fs) {
  FsFalso copia = fs;
  ArchivoRanuras a(copia, "/p.ab", MAGIA);
  std::vector<uint8_t> c;
  assert(a.cargar(c, 1 << 20));
  return c;
}

void basico() {
  FsFalso fs;
  ArchivoRanuras a(fs, "/p.ab", MAGIA);
  std::vector<uint8_t> c;
  assert(!a.cargar(c, 4096) && c.empty() && !a.hayArchivo());
  const auto uno = datos(100, 1), dos = datos(100, 2), tres = datos(300, 3);
  assert(a.guardar(uno.data(), uno.size()) && a.tamRanura() == 512 && fs.archivos["/p.ab"].durable.size() == 1024);
  assert(a.guardar(dos.data(), dos.size()) && a.guardar(tres.data(), tres.size()));
  assert(a.generacion() == 3 && leer(fs) == tres);
  // los guardados que caben no vuelven a abrir el archivo ni crean otros
  assert(fs.aperturasEditar == 1 && fs.archivos.size() == 1);
  // vacío también vale
  assert(a.guardar(nullptr, 0) && leer(fs).empty());
  ArchivoRanuras b(fs, "/p.ab", MAGIA);
  assert(b.cargar(c, 4096) && c.empty() && b.generacion() == 4);
  // otra magia no lee estas copias
  FsFalso copia = fs;
  ArchivoRanuras otra(copia, "/p.ab", "XXX1");
  assert(!otra.cargar(c, 4096));
  // una copia más larga que el máximo no vale: se usa la otra (aquí, la vacía de antes)
  assert(a.guardar(tres.data(), tres.size()));
  copia = fs;
  ArchivoRanuras corta(copia, "/p.ab", MAGIA);
  assert(corta.cargar(c, 200) && c.empty() && corta.generacion() == 4);
  printf("  dos ranuras de 512 B, sin reabrir ni crear archivos, vacío, magia y máximo OK\n");
}

void creceYSeRehace() {
  FsFalso fs;
  ArchivoRanuras a(fs, "/p.ab", MAGIA);
  const auto chica = datos(400, 1), grande = datos(1500, 2), mayor = datos(5000, 3);
  assert(a.guardar(chica.data(), chica.size()) && a.tamRanura() == 512);
  assert(a.guardar(grande.data(), grande.size()) && a.tamRanura() == 2048 && leer(fs) == grande);
  assert(a.guardar(chica.data(), chica.size()) && a.tamRanura() == 2048);  // no encoge
  assert(a.guardar(mayor.data(), mayor.size()) && a.tamRanura() == 8192 && leer(fs) == mayor);
  assert(!fs.existe("/p.abn") && fs.archivos["/p.ab"].durable.size() == 2 * 8192);
  printf("  la ranura crece por potencias de dos y el archivo se rehace sin dejar temporales OK\n");
}

// Un corte en cada operación de un guardado (en su sitio o rehaciendo el archivo), con distintos azares para lo que
// sobrevive de lo no vaciado: queda siempre lo de antes o lo nuevo, y se puede seguir guardando
void cortes(const std::vector<uint8_t> &antes, const std::vector<uint8_t> &nueva, const char *nombre) {
  FsFalso base;
  {
    ArchivoRanuras a(base, "/p.ab", MAGIA);
    const auto previa = datos(antes.size(), 99);
    assert(a.guardar(previa.data(), previa.size()) && a.guardar(antes.data(), antes.size()));
  }
  int n = 0, vieja = 0, nuevas = 0;
  bool terminado = false;
  for (long k = 0; !terminado; k++) {
    for (uint32_t semilla = 1; semilla <= 16; semilla++) {
      FsFalso fs = base;
      fs.azar = semilla * 104729;
      fs.programarCorte(k);
      bool cortado = false;
      try {
        ArchivoRanuras a(fs, "/p.ab", MAGIA);
        std::vector<uint8_t> c;
        assert(a.cargar(c, 1 << 20) && c == antes);
        assert(a.guardar(nueva.data(), nueva.size()));
      } catch (CorteDeEnergia &) {
        cortado = true;
      }
      fs.sinCorte();
      if (!cortado) {
        assert(leer(fs) == nueva);
        terminado = true;
        break;
      }
      n++;
      fs.aplicarCorte();
      ArchivoRanuras r(fs, "/p.ab", MAGIA);
      std::vector<uint8_t> c;
      assert(r.cargar(c, 1 << 20));
      assert(c == antes || c == nueva);  // nunca una mezcla
      (c == antes ? vieja : nuevas)++;
      assert(!fs.existe("/p.abn"));  // cargar() deja ordenado lo que dejó a medias un rehacer
      const auto otra = datos(nueva.size(), 7);
      assert(r.guardar(otra.data(), otra.size()) && leer(fs) == otra);
    }
  }
  assert(n >= 16 && vieja > 0 && nuevas > 0);
  printf("  %s: %d cortes (%d dejaron lo de antes, %d lo nuevo) OK\n", nombre, n, vieja, nuevas);
}

// El formato anterior de ArchivoAtomico: carga | magia | generación | crc32(carga + 8 B del pie)
std::vector<uint8_t> viejo(const std::vector<uint8_t> &carga, uint32_t generacion, const char magia[4]) {
  std::vector<uint8_t> v = carga;
  uint8_t pie[12];
  memcpy(pie, magia, 4);
  for (int b = 0; b < 4; b++) pie[4 + b] = generacion >> (8 * b);
  const uint32_t c = crc32(pie, 8, crc32(carga.data(), carga.size()));
  for (int b = 0; b < 4; b++) pie[8 + b] = c >> (8 * b);
  v.insert(v.end(), pie, pie + 12);
  return v;
}

void poner(FsFalso &fs, const char *ruta, const std::vector<uint8_t> &d) {
  fs.archivos[ruta].durable = fs.archivos[ruta].cache = d;
}

void migracionAtomico() {
  const auto actual = datos(200, 5), anterior = datos(150, 6);
  {
    FsFalso fs;
    poner(fs, "/m.bin", viejo(actual, 9, MAGIA));
    poner(fs, "/m.bak", viejo(anterior, 8, MAGIA));
    ArchivoAtomico a(fs, "/m", MAGIA);
    std::vector<uint8_t> c;
    assert(a.cargar(c, 4096) && c == actual);
    assert(fs.existe("/m.ab") && !fs.existe("/m.bin") && !fs.existe("/m.bak") && !fs.existe("/m.tmp"));
    const auto otra = datos(220, 1);
    assert(a.guardar(otra.data(), otra.size()));
    ArchivoAtomico b(fs, "/m", MAGIA);
    assert(b.cargar(c, 4096) && c == otra);
  }
  // un .bin roto: vale el .bak, como antes
  {
    FsFalso fs;
    auto roto = viejo(actual, 9, MAGIA);
    roto[3] ^= 1;
    poner(fs, "/m.bin", roto);
    poner(fs, "/m.bak", viejo(anterior, 8, MAGIA));
    ArchivoAtomico a(fs, "/m", MAGIA);
    std::vector<uint8_t> c;
    assert(a.cargar(c, 4096) && c == anterior);
  }
  // un corte en cada operación de la migración: siempre arranca con lo que había, y la siguiente vez la termina
  int n = 0;
  for (long k = 0; k < 20; k++) {
    FsFalso fs;
    poner(fs, "/m.bin", viejo(actual, 9, MAGIA));
    poner(fs, "/m.bak", viejo(anterior, 8, MAGIA));
    fs.programarCorte(k);
    try {
      ArchivoAtomico a(fs, "/m", MAGIA);
      std::vector<uint8_t> c;
      a.cargar(c, 4096);
    } catch (CorteDeEnergia &) {
      n++;
      fs.sinCorte();
      fs.aplicarCorte();
    }
    fs.sinCorte();
    ArchivoAtomico a(fs, "/m", MAGIA);
    std::vector<uint8_t> c;
    assert(a.cargar(c, 4096) && c == actual);
    assert(fs.existe("/m.ab") && !fs.existe("/m.bin") && !fs.existe("/m.bak"));
  }
  assert(n >= 4);
  printf("  migración desde .bin/.bak (también con .bin roto y %d cortes en medio) OK\n", n);
}

void soltarYReabrir() {
  FsFalso fs;
  ArchivoRanuras a(fs, "/p.ab", MAGIA);
  const auto uno = datos(50, 1), dos = datos(60, 2), tres = datos(50, 3);
  assert(a.guardar(uno.data(), uno.size()));
  a.soltar();
  assert(a.guardar(dos.data(), dos.size()) && leer(fs) == dos && fs.aperturasEditar == 2);
  // una escritura que falla (graba la mitad) suelta el archivo; la vigente sigue valiendo y el siguiente guardado
  // lo reabre. (Con datos distintos de los que había en esa ranura: con los mismos, la mitad sin escribir coincide y
  // la copia sale válida, y además correcta.)
  fs.agregarFalla = true;
  assert(!a.guardar(tres.data(), tres.size()));
  fs.agregarFalla = false;
  assert(leer(fs) == dos);
  assert(a.guardar(tres.data(), tres.size()) && leer(fs) == tres && fs.aperturasEditar == 3);
  printf("  soltar, escritura fallida y reapertura OK\n");
}

// Sin mantenerAbierto (recargas y tarifas): cada guardado abre y cierra, y todo lo demás sigue igual
void sinMantenerAbierto() {
  FsFalso fs;
  ArchivoRanuras a(fs, "/p.ab", MAGIA, false);
  std::vector<uint8_t> c;
  assert(!a.cargar(c, 4096));
  const auto uno = datos(80, 1), dos = datos(90, 2), grande = datos(2000, 3);
  assert(a.guardar(uno.data(), uno.size()) && fs.aperturasEditar == 0);  // creado, sin abrirlo después
  assert(a.guardar(dos.data(), dos.size()) && fs.aperturasEditar == 1 && leer(fs) == dos);
  assert(a.guardar(uno.data(), uno.size()) && fs.aperturasEditar == 2 && leer(fs) == uno);
  assert(a.guardar(grande.data(), grande.size()) && leer(fs) == grande && a.tamRanura() == 2048);
  ArchivoRanuras b(fs, "/p.ab", MAGIA, false);
  assert(b.cargar(c, 4096) && c == grande);
  assert(b.guardar(dos.data(), dos.size()) && leer(fs) == dos);
  printf("  sin mantener abierto: abre y cierra en cada guardado OK\n");
}

}  // namespace

int main() {
  basico();
  creceYSeRehace();
  cortes(datos(100, 1), datos(120, 2), "guardar en su sitio");
  cortes(datos(100, 1), datos(3000, 2), "rehacer el archivo al crecer");
  migracionAtomico();
  soltarYReabrir();
  sinMantenerAbierto();
  printf("#129 archivo de dos ranuras: en su sitio, crecimiento, cortes de energía y migración OK\n");
  return 0;
}
