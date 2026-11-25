// Lista negra de tarjetas bloqueadas (M3-03): /lista_negra.bin y /urgentes.bin.
//
// /lista_negra.bin son los identificadores uint32 (little-endian) en orden creciente y sin
// repetir, seguidos de un pie de 16 B: magia, generación, cuenta y el CRC32 de los
// identificadores. Con 50 000 tarjetas ocupa 200 KB, y no se carga entera en RAM:
//   - consulta: un índice en RAM con el primer identificador de cada bloque de 128 (512 B, un
//     sector) lleva al bloque que podría contener el id, y se lee ese solo bloque del archivo
//     (con 50 000 tarjetas el índice son 391 entradas, 1,5 KB);
//   - cambios: se mezclan con el archivo viejo en un .tmp, por partes (cada avanzar() procesa
//     un número acotado de entradas, para no bloquear el cobro desde loop()), y al terminar
//     se cambia de forma segura ante cortes de energía, igual que EstadoPersistente:
//         1. /lista_negra.tmp ya está completo y vaciado;   un corte: .bin intacto, .tmp ignorable
//         2. se borra .bak;                                 un corte: .bin intacto
//         3. .bin pasa a .bak;                              un corte: .tmp completo y .bak
//         4. .tmp pasa a .bin.
//     Al arrancar se usa la copia válida (pie y CRC) de mayor generación de las tres, así que no
//     importa en qué paso se cortó. Una mezcla a medias se pierde: quien la pidió debe repetirla
//     (por eso no hay que avanzar la versión de listas hasta que termine).
//
// Aplicar dos veces los mismos cambios da el mismo resultado: la lista es un conjunto, así que
// bloquear un id ya bloqueado o desbloquear uno que no está no cambia nada. Si un mismo id
// aparece varias veces entre los cambios, vale el último.
//
// Los bloqueos urgentes (/urgentes.bin) son pocos y van además en RAM: se consultan junto a la
// lista, y cada uno se guarda y vacía al recibirlo, porque la tarjeta debe quedar bloqueada
// aunque se corte la energía. Un urgente solo se quita con limpiarUrgentes() (cuando una lista
// firmada ya lo recoge) o con reiniciarListas().
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <memory>
#include <vector>

#include "Archivos.h"

class ListaNegra {
 public:
  struct Cambio {
    uint32_t id;
    bool alta;  // true: bloquear; false: desbloquear
  };
  enum class Progreso : uint8_t {
    OCIOSA,     // no hay ninguna mezcla en curso
    EN_CURSO,   // queda trabajo: volver a llamar a avanzar()
    TERMINADA,  // la mezcla acabó y la lista nueva ya está en uso (se devuelve una sola vez)
    FALLO,      // no se pudo escribir: la lista sigue como estaba (se devuelve una sola vez)
  };

  static constexpr size_t BLOQUE = 128;              // identificadores por bloque del índice
  static constexpr size_t MAX_IDS = 262144;          // 1 MB; el índice llega a 2048 entradas (8 KB)
  static constexpr size_t MAX_URGENTES = 64;
  static constexpr size_t TAM_PIE = 16;
  static constexpr size_t POR_LECTURA = 128;         // identificadores por lectura y escritura al mezclar (un sector)
  static constexpr size_t POR_LLAMADA = 64;          // lo que procesa avanzar() por defecto
  static constexpr uint32_t VACIAR_CADA = 4096;      // identificadores escritos entre vaciados

  // base: "/lista_negra" -> .bin, .tmp, .bak. urgentes: "/urgentes" -> .bin y .tmp
  explicit ListaNegra(SistemaArchivos &fs, const char *base = "/lista_negra", const char *urgentes = "/urgentes");
  ~ListaNegra();

  // Recupera la lista de lo que haya en disco y carga los urgentes. Lee el archivo una vez
  // (comprueba el CRC y arma el índice): unos 0,2 s con 50 000 tarjetas. false si no había
  // lista válida (arranca vacía: validador nuevo o tarjeta borrada).
  bool iniciar();

  bool bloqueada(uint32_t id);
  size_t cantidad() const { return cuenta_; }
  uint32_t generacion() const { return generacion_; }

  // Empieza a mezclar los cambios con la lista. false si ya hay una mezcla en curso o la lista
  // resultante podría pasar de MAX_IDS. No hace nada más: el trabajo lo hace avanzar().
  bool aplicarCambios(const Cambio *entradas, size_t n);
  // Procesa como mucho `maxEntradas` identificadores (o un paso de cierre) y vuelve. La lista
  // vieja sigue respondiendo a bloqueada() mientras tanto.
  Progreso avanzar(size_t maxEntradas = POR_LLAMADA);
  bool mezclando() const { return mezcla_ != nullptr; }
  // true cuando el siguiente avanzar() es el cierre (vaciado y cambio de archivos), que en la
  // microSD cuesta decenas o cientos de ms (renombrar y borrar los fija la tarjeta, no el código).
  // Quien llama desde loop() conviene que lo haga con el lector libre, sin una tarjeta delante.
  bool proximoPasoBloquea() const;

  // Vacía la lista (para reconstruirla cuando version_desde = 0) y los urgentes. Síncrono.
  // Con conservarUrgentes los bloqueos urgentes siguen en pie: los de una foto completa que aún no
  // los recoge no deben desbloquearse mientras se reconstruye.
  bool reiniciarListas(bool conservarUrgentes = false);

  bool bloqueoUrgente(uint32_t id);  // false si no se pudo guardar (queda bloqueado igualmente en RAM)
  bool limpiarUrgentes();
  // Quita un urgente concreto: porque la lista ya lo recoge, o porque un cambio en orden lo desbloquea
  // (sin esto un urgente no se podría desbloquear nunca). false si no se pudo guardar. No hace nada si no está.
  bool quitarUrgente(uint32_t id);
  size_t cantidadUrgentes() const { return urgentes_.size(); }

 private:
  struct Pie {
    bool valido = false;
    uint32_t generacion = 0, cuenta = 0, crc = 0;
  };
  struct Mezcla;

  Pie leerPie(const char *ruta);
  bool verificar(const char *ruta, const Pie &pie, std::vector<uint32_t> &indice);  // CRC, orden e índice
  bool reparar();                      // elige la mejor copia y deja /lista_negra.bin con ella
  bool cargarUrgentes();
  bool reescribirUrgentes();
  bool abrirLista();
  bool iniciarMezcla(const Cambio *entradas, size_t n, bool desdeCero);
  void abortarMezcla();
  Progreso fallo();
  bool siguienteViejo(uint32_t &id);
  bool escribirSalida();

  SistemaArchivos &fs_;
  char bin_[48], tmp_[48], bak_[48], urg_[48], urgTmp_[48];

  std::unique_ptr<Archivo> archivo_;   // /lista_negra.bin abierto para consultas
  std::vector<uint32_t> indice_;       // el primer id de cada bloque
  std::vector<uint32_t> bloque_;       // el último bloque leído
  long bloqueLeido_ = -1;
  size_t bloqueN_ = 0;                 // cuántos ids tiene ese bloque
  size_t cuenta_ = 0;
  uint32_t generacion_ = 0;
  std::vector<uint32_t> urgentes_;
  std::unique_ptr<Mezcla> mezcla_;
};
