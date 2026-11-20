// Envío fiable de un mensaje C1 por fragmentos (M8-02). Lógica pura, sin radio.
//
// Parte un mensaje de hasta 2048 B en tramas FRAGMENTO de 220 B como mucho, y las va
// entregando de una en una: no se pasa a la siguiente hasta saber si la anterior llegó (en
// ESP-NOW lo dice el aviso de entrega). Cada fragmento admite hasta REINTENTOS reintentos;
// si se agotan, se aborta el mensaje entero (el receptor descarta lo que llevaba).
//
// Uso (lo hace VecinosEspNow):
//   emisor.iniciar(mensaje, largo, validador, id);
//   en cada vuelta:  n = emisor.siguienteTrama(trama, ahora);  if (n) transmitir(trama, n);
//                    cuando la radio avisa:  emisor.resultado(llegó, ahora);
//                    emisor.revisar(ahora);  switch (emisor.tomarFin()) {...}
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <Fragmentos.h>

class EmisorFragmentos {
 public:
  static constexpr uint8_t REINTENTOS = 3;                  // por fragmento, tras el primer intento
  static constexpr uint32_t ESPERA_CONFIRMACION_MS = 100;   // sin aviso de entrega = fallo
  static constexpr size_t MAX_MENSAJE = 2048;               // paquete C1 más grande
  enum class Fin : uint8_t { NINGUNO, ENTREGADO, ABORTADO };

  // Copia el mensaje y lo deja listo. false si ya hay uno en curso, si está vacío o si
  // es mayor que MAX_MENSAJE.
  bool iniciar(const uint8_t *mensaje, size_t largo, uint16_t emisor, uint32_t idMensaje);
  void cancelar();  // olvida el mensaje en curso sin avisar de fin
  bool ocupado() const { return estado_ != Estado::LIBRE; }

  // La trama que toca transmitir ahora (devuelve su largo), o 0 si no hay nada que
  // enviar: no hay mensaje, o se espera todavía el aviso de la anterior.
  size_t siguienteTrama(uint8_t trama[sp::TAM_TRAMA_MAXIMO], uint32_t ahoraMs);
  // Dice si la última trama llegó. Se ignora si no se esperaba ninguno.
  void resultado(bool llego, uint32_t ahoraMs);
  // Si el aviso no llega en ESPERA_CONFIRMACION_MS, cuenta como un fallo
  void revisar(uint32_t ahoraMs);

  // Cómo terminó el último mensaje; se consulta una sola vez (después vuelve a NINGUNO)
  Fin tomarFin() {
    const Fin f = fin_;
    fin_ = Fin::NINGUNO;
    return f;
  }
  uint32_t reintentos() const { return reintentos_; }  // acumulados desde el arranque
  uint8_t indice() const { return indice_; }           // fragmento en curso
  uint8_t total() const { return total_; }

 private:
  enum class Estado : uint8_t { LIBRE, LISTO, ESPERANDO };

  uint8_t mensaje_[MAX_MENSAJE];
  size_t largo_ = 0;
  uint16_t emisor_ = 0;
  uint32_t id_ = 0;
  uint8_t total_ = 0, indice_ = 0, intentos_ = 0;
  Estado estado_ = Estado::LIBRE;
  uint32_t envioMs_ = 0;
  Fin fin_ = Fin::NINGUNO;
  uint32_t reintentos_ = 0;
};
