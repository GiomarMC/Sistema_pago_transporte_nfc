// Búsqueda del canal de los vecinos de ESP-NOW para un bus sin WiFi.
//
// ESP-NOW solo oye a quien está en su mismo canal de radio, y cada bus con WiFi está en el canal de su AP (#67). Un
// bus sin WiFi se quedaba siempre en espnow_canal: si sus vecinos estaban en otro, no se veían nunca. Con esto, un bus
// sin WiFi y sin vecinos espera primero EN_BASE_MS en su canal (espnow_canal) y luego recorre los demás: 1, 6 y 11
// (los que más usan los routers) y después el resto, POR_CANAL_MS en cada uno, en un orden al azar dentro de cada
// grupo que cambia en cada vuelta. POR_CANAL_MS es más que el máximo entre dos balizas (2,5 s), así que un vecino que
// esté en ese canal se oye. Al oír a uno se queda en ese canal mientras lo oiga; si lo pierde, vuelve a buscar
// empezando por donde estaba.
//
// Si una baliza dice que su emisor está en otro canal (los canales de 2,4 GHz se solapan y de cerca una baliza del 6
// se oye también en el 5 o el 7), pista() salta directo a ese canal.
//
// Lógica pura, sin radio ni Arduino, para probarla en el ordenador. VecinosEspNow le pregunta en cada atender() y
// mueve la radio cuando le devuelve un canal.
#pragma once

#include <stddef.h>
#include <stdint.h>

class BuscadorCanal {
 public:
  static constexpr uint8_t ULTIMO_CANAL = 13;
  static constexpr uint32_t EN_BASE_MS = 8000;    // en espnow_canal antes de buscar en otros (y en cada vuelta)
  static constexpr uint32_t POR_CANAL_MS = 3000;  // en cada uno de los demás
  static constexpr uint32_t VARIACION_MS = 400;   // ± al azar: dos buses que buscan a la vez no van al mismo paso

  explicit BuscadorCanal(uint32_t semilla = 1) : azar_(semilla ? semilla : 1) {}

  void base(uint8_t canal) { base_ = valido(canal) ? canal : 1; }
  uint8_t base() const { return base_; }

  // Una vuelta del bucle. Devuelve el canal al que hay que pasar la radio, o 0 si se queda donde está.
  //   actual     canal en que está ahora la radio
  //   vecinos    cuántos vecinos hay en la tabla
  //   permitido  false con el WiFi conectado, conectándose o buscando redes: no se salta (la radio la lleva el WiFi) y
  //              el recorrido se congela; al volver sigue donde iba, con la espera de ese canal entera. Así los
  //              escaneos del WiFi (cada 15 s sin conexión) no lo devuelven al principio una y otra vez
  uint8_t atender(uint32_t ahoraMs, uint8_t actual, size_t vecinos, bool permitido);
  // El WiFi movió la radio (se conectó a un AP o lo perdió): la próxima búsqueda empieza desde cero
  void reiniciar() {
    buscando_ = false;
    enPausa_ = false;
  }

  // Llegó una baliza cuyo emisor dice estar en `canal`, estando la radio en `actual`. Devuelve el canal al que saltar,
  // o 0 si no hay que moverse: mismo canal, no lo dice, ya hay vecinos en este o no está permitido buscar.
  uint8_t pista(uint32_t ahoraMs, uint8_t actual, uint8_t canal, size_t vecinos, bool permitido);

  bool buscando() const { return buscando_; }
  uint32_t saltos() const { return saltos_; }
  uint32_t vueltas() const { return vueltas_; }  // recorridos completos sin oír a nadie

 private:
  static bool valido(uint8_t c) { return c >= 1 && c <= ULTIMO_CANAL; }
  void empezar(uint32_t ahoraMs, uint8_t actual);
  void armar(uint8_t primero);  // el orden de una vuelta: primero, espnow_canal, 1/6/11 y el resto (barajados)
  uint32_t espera(bool enBase);  // cuánto quedarse: EN_BASE_MS o POR_CANAL_MS, ± VARIACION_MS
  uint32_t aleatorio();

  uint8_t base_ = 1;
  uint8_t orden_[ULTIMO_CANAL] = {};
  uint8_t cuantos_ = 0, indice_ = 0;
  bool buscando_ = false, enPausa_ = false;
  uint32_t desdeMs_ = 0, esperaMs_ = 0;
  uint32_t saltos_ = 0, vueltas_ = 0;
  uint32_t azar_;
};
