// Sistema de archivos falso con cortes de energía, para probar el almacenamiento (M3).
//
// Es más duro que la microSD real a propósito: si el almacenamiento aguanta esto, aguanta la tarjeta.
//   - Lo escrito vive en una "caché" y solo pasa a "durable" con vaciar(); cerrar un archivo NO vacía.
//   - Abrir con Modo::NUEVO vacía el archivo al instante (su contenido anterior se pierde ya).
//   - borrar() y renombrar() son inmediatos y duraderos, pero un corte puede caer entre dos operaciones.
//   - Un corte a mitad de agregar() deja una parte de los bytes escritos.
//   - Al cortar, de lo escrito y no vaciado puede sobrevivir un trozo al azar (o nada).
// programarCorte(n): la operación n-ésima (que cambia algo) lanza CorteDeEnergia sin llegar a hacerse.
#pragma once

#include <stdint.h>

#include <map>
#include <memory>
#include <string>
#include <vector>

#include <Archivos.h>

struct CorteDeEnergia {};

class FsFalso : public SistemaArchivos {
 public:
  struct Entrada {
    std::vector<uint8_t> durable, cache;
  };

  std::map<std::string, Entrada> archivos;
  long operaciones = 0;
  long cortarEn = -1;   // -1: sin corte programado
  bool agregarFalla = false;  // agregar() escribe la mitad y devuelve false (como una tarjeta con errores)
  uint32_t azar = 12345;

  uint32_t siguienteAzar() {
    azar = azar * 1664525u + 1013904223u;
    return azar >> 8;
  }

  void programarCorte(long enOperaciones) {
    operaciones = 0;
    cortarEn = enOperaciones;
  }
  void sinCorte() { cortarEn = -1; }

  // El corte de energía ya ocurrió: queda lo durable y, a veces, parte de lo que no se había vaciado
  void aplicarCorte() {
    cortarEn = -1;
    for (auto &par : archivos) {
      Entrada &e = par.second;
      std::vector<uint8_t> queda = e.durable;
      const bool cacheEsContinuacion = e.cache.size() > e.durable.size() &&
                                       std::equal(e.durable.begin(), e.durable.end(), e.cache.begin());
      if (cacheEsContinuacion) {
        const size_t extra = e.cache.size() - e.durable.size();
        const size_t sobrevive = siguienteAzar() % (extra + 1);  // de 0 a todo
        queda.insert(queda.end(), e.cache.begin() + e.durable.size(), e.cache.begin() + e.durable.size() + sobrevive);
      }
      e.durable = queda;
      e.cache = queda;
    }
  }

  // --- SistemaArchivos ----------------------------------------------------------------

  std::unique_ptr<Archivo> abrir(const char *ruta, Modo modo) override;
  bool existe(const char *ruta) override { return archivos.count(ruta) > 0; }
  bool borrar(const char *ruta) override {
    tic();
    return archivos.erase(ruta) > 0;
  }
  bool renombrar(const char *de, const char *a) override {
    tic();
    if (!archivos.count(de) || archivos.count(a)) return false;
    archivos[a] = archivos[de];
    archivos.erase(de);
    return true;
  }

  // Cuenta una operación que cambia algo; lanza el corte si le toca
  void tic() {
    if (cortarEn >= 0 && operaciones++ >= cortarEn) throw CorteDeEnergia{};
  }
  bool tocaCortar() const { return cortarEn >= 0 && operaciones >= cortarEn; }
};

class ArchivoFalso : public Archivo {
 public:
  ArchivoFalso(FsFalso &fs, std::string ruta, bool escribible) : fs_(fs), ruta_(std::move(ruta)), escribible_(escribible) {}
  size_t tamano() override { return e().cache.size(); }
  bool leerEn(size_t pos, void *destino, size_t n) override {
    if (pos + n > e().cache.size()) return false;
    memcpy(destino, e().cache.data() + pos, n);
    return true;
  }
  bool agregar(const void *origen, size_t n) override {
    if (!escribible_) return false;
    const uint8_t *b = static_cast<const uint8_t *>(origen);
    if (fs_.tocaCortar()) {  // el corte llega a mitad de la escritura: queda un trozo
      const size_t parte = fs_.siguienteAzar() % n;
      e().cache.insert(e().cache.end(), b, b + parte);
      fs_.tic();  // lanza
    }
    fs_.tic();
    if (fs_.agregarFalla) {
      e().cache.insert(e().cache.end(), b, b + n / 2);
      return false;
    }
    e().cache.insert(e().cache.end(), b, b + n);
    return true;
  }
  bool vaciar() override {
    if (!escribible_) return false;
    fs_.tic();
    e().durable = e().cache;
    return true;
  }

 private:
  FsFalso::Entrada &e() { return fs_.archivos[ruta_]; }
  FsFalso &fs_;
  std::string ruta_;
  bool escribible_;
};

inline std::unique_ptr<Archivo> FsFalso::abrir(const char *ruta, Modo modo) {
  const bool hay = archivos.count(ruta) > 0;
  switch (modo) {
    case Modo::LECTURA:
      if (!hay) return nullptr;
      return std::make_unique<ArchivoFalso>(*this, ruta, false);
    case Modo::NUEVO:
      tic();
      archivos[ruta] = Entrada();  // vacío y duradero desde ya
      return std::make_unique<ArchivoFalso>(*this, ruta, true);
    case Modo::ABRIR:
      if (!hay) {
        tic();
        archivos[ruta] = Entrada();
      }
      return std::make_unique<ArchivoFalso>(*this, ruta, true);
  }
  return nullptr;
}
