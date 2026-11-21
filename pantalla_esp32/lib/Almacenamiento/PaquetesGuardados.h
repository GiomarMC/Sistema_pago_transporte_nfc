// Paquetes que el validador guarda en la microSD para entregarlos después (M3-05).
//
//   PaquetesAjenos   /ajenos/<origen>-<secuencia>.bin   EVENTOS de OTROS buses (llegaron por ESP-NOW o
//                    BLE) que el servidor aún no ha confirmado. Hasta 500; al pasar el límite se
//                    borra el más antiguo. Un ACUSE del servidor para ese origen borra los que cubre.
//   PaquetesFirmados /firmados/<desde>-<hasta>.bin      LISTAS firmados por el servidor (ECDSA), para
//                    pasárselos a otros buses (M8). Los 50 más recientes.
//
// Cada archivo es el paquete C1 completo, tal como llegó (con su firma): aquí no se firma ni se
// comprueba ninguna firma, eso es cosa de quien lo recibe o lo envía. Al guardar solo se exige que
// tenga la forma de lo que se espera (tipo, origen, largo coherente).
//
// EL ÍNDICE: la microSD tarda ~180 ms por archivo en recorrer una carpeta (medido: 500 archivos son
// 90 s, el doble del watchdog), así que NO se lista nunca. El índice (24 B por paquete en RAM, unos
// 12 KB con 500) se reconstruye de un diario, /ajenos.log o /firmados.log, de registros de 28 B con
// CRC32: ALTA (con todo lo que se necesita saber del paquete: hora, secuencia, último evento, largo)
// y BAJA. Leerlo cuesta unas decenas de ms. Para que un corte de energía nunca deje un archivo que el
// índice no conozca:
//   guardar: 1. se anota la ALTA (y se vacía);  2. se escribe el archivo.
//   borrar:  1. se borra el archivo;            2. se anota la BAJA.
// Un corte entre los dos pasos deja, como mucho, una entrada cuyo archivo falta o está cortado: se
// descubre al leerlo (el tamaño debe ser el anotado) y se quita entonces. Un registro cortado al final
// del diario se descarta al arrancar. El diario se reescribe entero (por un .tmp con un registro FIN
// al final) cuando acumula el doble de registros que paquetes, o si hubo un fallo al escribirlo.
//
// "Más antiguo": en ajenos, el de menor `creado` (la hora de su emisor; a igual hora, menor origen y
// secuencia); en firmados, el de menor secuencia (la del servidor, que solo crece).
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <map>
#include <memory>
#include <vector>

#include "Archivos.h"

struct InfoPaquete {
  uint32_t a;          // ajenos: origen · firmados: versión desde (los del nombre del archivo)
  uint32_t b;          // ajenos: secuencia · firmados: versión hasta
  uint32_t secuencia;  // de la cabecera
  uint32_t creado;     // de la cabecera (unix, s)
  uint32_t ultimoEvento;  // ajenos: el id del último evento que lleva · firmados: 0
  size_t largo;        // bytes del paquete
};

class CarpetaPaquetes {
 public:
  static constexpr size_t TAM_REGISTRO = 28;
  static constexpr size_t REGISTROS_DE_MAS = 100;  // el diario se reescribe al pasar de 2 x paquetes + esto

  size_t cantidad() const { return indice_.size(); }
  size_t registrosDelDiario() const { return registros_; }

 protected:
  struct Entrada {
    uint32_t a = 0, b = 0, secuencia = 0, creado = 0, ultimoEvento = 0;
    uint16_t largo = 0;
  };

  CarpetaPaquetes(SistemaArchivos &fs, const char *carpeta, const char *diario, size_t maximo, bool ajenos);

  bool iniciarCarpeta();
  bool guardarPaquete(const uint8_t *datos, size_t largo, const Entrada &e);
  size_t leerPaquete(uint32_t a, uint32_t b, uint8_t *destino, size_t capacidad);
  bool masAntigua(const Entrada &x, const Entrada &y) const;
  // Los datos de la cabecera de un paquete entero; false si no es lo que esta carpeta guarda
  bool examinar(const uint8_t *datos, size_t largo, Entrada &e) const;
  long buscar(uint32_t a, uint32_t b) const;
  // vaciar = false en los lotes (borrar varios): quien llama vacía el diario una sola vez al terminar
  void borrarEntrada(size_t i, bool vaciar = true);
  void vaciarDiario();
  void ordenadas(std::vector<const Entrada *> &salida) const;
  bool yaConfirmado(const Entrada &e) const;

  std::map<uint16_t, uint32_t> acusado_;  // ajenos: hasta qué evento confirmó el servidor, por origen (solo en RAM)
  std::vector<Entrada> indice_;

 private:
  enum Tipo : uint8_t { ALTA = 1, BAJA = 2, FIN = 3 };

  void ruta(uint32_t a, uint32_t b, char *salida, size_t n) const;
  static void registro(Tipo t, const Entrada &e, uint8_t r[TAM_REGISTRO]);
  bool cargarDiario(bool &hayQueReescribir);
  // vaciar = false para las BAJA: no hace falta que lleguen a la tarjeta de inmediato (si se pierde una, la
  // entrada se quita al descubrir que su archivo no está), y cada vaciado cuesta decenas de ms
  bool anotar(Tipo t, const Entrada &e, bool vaciar = true);
  bool reescribirDiario();
  void recortar(bool protegerUno, uint32_t a, uint32_t b);

  SistemaArchivos &fs_;
  const char *carpeta_;
  char log_[24], tmp_[24];
  size_t maximo_;
  bool ajenos_;
  std::unique_ptr<Archivo> diario_;  // abierto para agregar; null si hay que reescribirlo
  size_t registros_ = 0;
};

class PaquetesAjenos : public CarpetaPaquetes {
 public:
  static constexpr size_t MAXIMO = 500;
  static constexpr const char *CARPETA = "/ajenos";

  explicit PaquetesAjenos(SistemaArchivos &fs) : CarpetaPaquetes(fs, CARPETA, "/ajenos", MAXIMO, true) {}

  // Lee el diario (no recorre la carpeta). false si no se pudo preparar el almacenamiento.
  bool iniciar() { return iniciarCarpeta(); }
  // Guarda un paquete EVENTOS de otro validador. false si no es un EVENTOS entero de un validador (origen
  // distinto de 0) o no se pudo escribir. Si ya estaba (mismo origen y secuencia), o el servidor ya
  // confirmó sus eventos, no lo vuelve a escribir y da true. Con MAXIMO guardados borra el más antiguo.
  bool guardar(const uint8_t *paquete, size_t largo);
  // El servidor confirmó los eventos del `origen` hasta `hastaEvento`: recuerda el acuse (para no guardar
  // un paquete que llegue otra vez) y borra los paquetes que cubre (los que terminan en ese evento o
  // antes), pero como mucho `maxBorrar`: borrar un archivo cuesta ~100-300 ms en la microSD (medido: 124
  // paquetes = 37 s, casi el watchdog). Los que falten se borran con borrarConfirmados(), por ejemplo una
  // vez por vuelta de loop(). Devuelve cuántos borró.
  size_t acusar(uint16_t origen, uint32_t hastaEvento, size_t maxBorrar = 2);
  size_t borrarConfirmados(size_t max);        // borra hasta `max` de los cubiertos por un acuse
  size_t confirmadosPorBorrar() const;         // cuántos quedan por borrar (el acuse se recuerda solo hasta reiniciar)
  // Hasta `max` paquetes, del más antiguo al más nuevo. Devuelve cuántos.
  size_t listar(InfoPaquete *salida, size_t max);
  // El paquete guardado, copiado en `destino`. 0 si no existe o no cabe (si su archivo falta o está
  // cortado, lo quita del índice).
  size_t leer(uint16_t origen, uint32_t secuencia, uint8_t *destino, size_t capacidad);
  // El paquete que está en la posición `indice` (0 a cantidad() - 1), en el orden en que se guardaron, para
  // recorrerlos de uno en uno sin ordenar. 0 si no hay tal posición, si el servidor ya lo confirmó (solo falta
  // borrarlo) o si no cabe / su archivo no está.
  size_t leerPosicion(size_t indice, uint8_t *destino, size_t capacidad);
};

class PaquetesFirmados : public CarpetaPaquetes {
 public:
  static constexpr size_t MAXIMO = 50;
  static constexpr const char *CARPETA = "/firmados";

  explicit PaquetesFirmados(SistemaArchivos &fs) : CarpetaPaquetes(fs, CARPETA, "/firmados", MAXIMO, false) {}

  bool iniciar() { return iniciarCarpeta(); }
  // Guarda un paquete LISTAS del servidor. false si no lo es (tipo, origen 0, largo coherente) o no se
  // pudo escribir. Si ya había uno con las mismas versiones, lo sustituye. Con MAXIMO borra el más antiguo.
  bool guardar(const uint8_t *paquete, size_t largo);
  // Hasta `max` paquetes, del más antiguo al más nuevo. Devuelve cuántos.
  size_t listar(InfoPaquete *salida, size_t max);
  size_t leer(uint32_t desde, uint32_t hasta, uint8_t *destino, size_t capacidad);
};
