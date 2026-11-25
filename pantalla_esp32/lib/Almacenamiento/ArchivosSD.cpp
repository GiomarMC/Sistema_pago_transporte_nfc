#include "ArchivosSD.h"

#ifdef ARDUINO

#include <SD.h>

namespace {

class ArchivoSD : public Archivo {
 public:
  ArchivoSD(File f, Modo modo) : f_(f), modo_(modo) {}
  ~ArchivoSD() override { f_.close(); }  // cerrar vacía a la tarjeta, pero el almacenamiento no cuenta con ello

  // OJO: File::size() lee la entrada de directorio, que en un archivo recién creado (o con escrituras sin cerrar) trae
  // datos viejos: medido, 211 y 1073450320 bytes en uno nuevo y vacío. El tamaño de verdad es hasta dónde llega el
  // archivo abierto: se va al final, se lee la posición y se vuelve donde estaba (en los de escritura, que van en
  // modo "agregar", el final es donde siguen escribiendo).
  size_t tamano() override {
    const size_t antes = f_.position();
    if (!f_.seek(0, SeekEnd)) return f_.size();
    const size_t n = f_.position();
    f_.seek(antes);
    return n;
  }
  bool leerEn(size_t posicion, void *destino, size_t n) override {
    // los archivos NUEVO y ABRIR van en modo "agregar": solo escriben
    if ((modo_ != Modo::LECTURA && modo_ != Modo::EDITAR) || !f_.seek(posicion)) return false;
    return f_.read(static_cast<uint8_t *>(destino), n) == n;
  }
  bool agregar(const void *origen, size_t n) override {
    return (modo_ == Modo::NUEVO || modo_ == Modo::ABRIR) && f_.write(static_cast<const uint8_t *>(origen), n) == n;
  }
  bool escribirEn(size_t posicion, const void *origen, size_t n) override {
    return modo_ == Modo::EDITAR && f_.seek(posicion) && f_.write(static_cast<const uint8_t *>(origen), n) == n;
  }
  // flush() del framework hace fflush y fsync: lo escrito queda en la tarjeta
  bool vaciar() override {
    if (modo_ == Modo::LECTURA) return false;
    f_.flush();
    return true;
  }

 private:
  File f_;
  Modo modo_;
};

}  // namespace

bool ArchivosSD::completa(const char *ruta, char *salida, size_t n) const {
  const int largo = snprintf(salida, n, "%s%s", carpeta_.c_str(), ruta);
  return largo > 0 && (size_t)largo < n;
}

bool ArchivosSD::iniciar() {
  if (carpeta_.length() == 0 || carpeta_ == "/") return true;
  return SD.exists(carpeta_.c_str()) || SD.mkdir(carpeta_.c_str());
}

std::unique_ptr<Archivo> ArchivosSD::abrir(const char *ruta, Modo modo) {
  char c[96];
  if (!completa(ruta, c, sizeof(c))) return nullptr;
  // En el ESP32: FILE_WRITE crea o vacía el archivo; FILE_APPEND lo crea si no existe y escribe al final; "r+" lee y
  // escribe donde se le diga, sin vaciarlo (y sin crearlo: en EDITAR debe existir)
  const char *m = modo == Modo::LECTURA ? FILE_READ : modo == Modo::NUEVO ? FILE_WRITE
                : modo == Modo::ABRIR   ? FILE_APPEND : "r+";
  File f = SD.open(c, m);
  if (!f || f.isDirectory()) return nullptr;
  return std::unique_ptr<Archivo>(new ArchivoSD(f, modo));
}

bool ArchivosSD::existe(const char *ruta) {
  char c[96];
  return completa(ruta, c, sizeof(c)) && SD.exists(c);
}

bool ArchivosSD::borrar(const char *ruta) {
  char c[96];
  return completa(ruta, c, sizeof(c)) && SD.remove(c);
}

bool ArchivosSD::renombrar(const char *de, const char *a) {
  char x[96], y[96];
  if (!completa(de, x, sizeof(x)) || !completa(a, y, sizeof(y)) || SD.exists(y)) return false;
  return SD.rename(x, y);
}

bool ArchivosSD::crearCarpeta(const char *carpeta) {
  char c[96];
  return completa(carpeta, c, sizeof(c)) && (SD.exists(c) || SD.mkdir(c));
}

bool ArchivosSD::listar(const char *carpeta, std::vector<std::string> &nombres) {
  nombres.clear();
  char c[96];
  if (!completa(carpeta, c, sizeof(c))) return false;
  File dir = SD.open(c);
  if (!dir || !dir.isDirectory()) return false;
  for (File f = dir.openNextFile(); f; f = dir.openNextFile()) {
    if (f.isDirectory()) continue;
    const char *n = f.name();  // según la versión del framework trae o no la ruta
    const char *barra = strrchr(n, '/');
    nombres.push_back(barra ? barra + 1 : n);
  }
  return true;
}

#endif  // ARDUINO
