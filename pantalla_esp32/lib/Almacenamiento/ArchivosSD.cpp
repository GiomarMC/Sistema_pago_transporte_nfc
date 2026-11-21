#include "ArchivosSD.h"

#ifdef ARDUINO

#include <SD.h>

namespace {

class ArchivoSD : public Archivo {
 public:
  ArchivoSD(File f, bool escribible) : f_(f), escribible_(escribible) {}
  ~ArchivoSD() override { f_.close(); }  // cerrar vacía a la tarjeta, pero el almacenamiento no cuenta con ello

  size_t tamano() override { return f_.size(); }
  bool leerEn(size_t posicion, void *destino, size_t n) override {
    if (escribible_ || !f_.seek(posicion)) return false;  // los archivos para escribir van en modo "agregar": solo escriben
    return f_.read(static_cast<uint8_t *>(destino), n) == n;
  }
  bool agregar(const void *origen, size_t n) override {
    return escribible_ && f_.write(static_cast<const uint8_t *>(origen), n) == n;
  }
  // flush() del framework hace fflush y fsync: lo escrito queda en la tarjeta
  bool vaciar() override {
    if (!escribible_) return false;
    f_.flush();
    return true;
  }

 private:
  File f_;
  bool escribible_;
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
  // En el ESP32: FILE_WRITE crea o vacía el archivo; FILE_APPEND lo crea si no existe y escribe al final
  File f = SD.open(c, modo == Modo::LECTURA ? FILE_READ : modo == Modo::NUEVO ? FILE_WRITE : FILE_APPEND);
  if (!f || f.isDirectory()) return nullptr;
  return std::unique_ptr<Archivo>(new ArchivoSD(f, modo != Modo::LECTURA));
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

#endif  // ARDUINO
