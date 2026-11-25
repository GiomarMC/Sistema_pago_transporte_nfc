// Búsqueda del canal de los vecinos de ESP-NOW (BuscadorCanal), sin radio: el recorrido, la pausa mientras el WiFi
// busca redes, la pista de una baliza oída desde otro canal y una simulación de buses que se buscan.
#include <assert.h>
#include <stdint.h>
#include <stdio.h>

#include <BuscadorCanal.h>

#include <initializer_list>

namespace {

using B = BuscadorCanal;
constexpr uint32_t MAX_BASE = B::EN_BASE_MS + B::VARIACION_MS, MIN_BASE = B::EN_BASE_MS - B::VARIACION_MS;
constexpr uint32_t MAX_OTRO = B::POR_CANAL_MS + B::VARIACION_MS, MIN_OTRO = B::POR_CANAL_MS - B::VARIACION_MS;

// Avanza de 10 en 10 ms hasta que el buscador pida un salto; devuelve el canal y deja en `t` el momento
uint8_t hastaElSalto(B &b, uint32_t &t, uint8_t actual, uint32_t limiteMs = 60000) {
  const uint32_t fin = t + limiteMs;
  for (; t != fin; t += 10) {
    const uint8_t c = b.atender(t, actual, 0, true);
    if (c) return c;
  }
  return 0;
}

bool en(uint8_t c, std::initializer_list<uint8_t> grupo) {
  for (uint8_t g : grupo)
    if (g == c) return true;
  return false;
}

void recorridoCompleto() {
  B b(12345);
  b.base(1);
  uint32_t t = 0;
  assert(b.atender(t, 1, 0, true) == 0 && b.buscando());  // empieza esperando en su canal

  // Tres vueltas. En cada una: ~8 s en espnow_canal, luego 6 y 11 (en cualquier orden) y después los otros 10, ~3 s en
  // cada uno; cada canal una sola vez por vuelta, y el orden cambia de una vuelta a otra
  uint8_t actual = 1;
  uint32_t desde = t;
  uint8_t ordenes[3][B::ULTIMO_CANAL];
  for (int vuelta = 0; vuelta < 3; vuelta++) {
    bool visto[B::ULTIMO_CANAL + 1] = {};
    ordenes[vuelta][0] = 1;
    visto[1] = true;
    for (int i = 1; i <= B::ULTIMO_CANAL; i++) {
      const uint8_t salto = hastaElSalto(b, t, actual);
      assert(salto >= 1 && salto <= B::ULTIMO_CANAL);
      const uint32_t estuvo = t - desde;
      if (actual == 1) assert(estuvo >= MIN_BASE && estuvo <= MAX_BASE + 10);
      else assert(estuvo >= MIN_OTRO && estuvo <= MAX_OTRO + 10);
      if (i == B::ULTIMO_CANAL) {
        assert(salto == 1);  // vuelve a su canal: vuelta siguiente
      } else {
        assert(!visto[salto]);
        visto[salto] = true;
        ordenes[vuelta][i] = salto;
        if (i <= 2) assert(en(salto, {6, 11}));
      }
      actual = salto;
      desde = t;
    }
  }
  assert(b.vueltas() == 3 && b.saltos() == 3 * B::ULTIMO_CANAL);
  bool distintos = false;
  for (int i = 0; i < B::ULTIMO_CANAL; i++) distintos |= ordenes[0][i] != ordenes[1][i] || ordenes[1][i] != ordenes[2][i];
  assert(distintos);
}

void conVecinosNoSalta() {
  B b(7);
  b.base(1);
  for (uint32_t t = 0; t < 120000; t += 10) assert(b.atender(t, 1, 1, true) == 0);
  assert(!b.buscando() && b.saltos() == 0);

  // Lo pierde en el 11 (la tabla lo olvida a los 10 s): busca desde ahí, ~3 s, luego su canal (~8 s) y sigue
  uint32_t t = 200000;
  assert(b.atender(t, 11, 0, true) == 0 && b.buscando());
  uint32_t desde = t;
  assert(hastaElSalto(b, t, 11) == 1 && t - desde <= MAX_OTRO + 10);
  desde = t;
  assert(hastaElSalto(b, t, 1) == 6 && t - desde >= MIN_BASE);

  // Si oye a uno a media espera, deja de buscar ahí mismo
  t += 1000;
  assert(b.atender(t, 6, 1, true) == 0 && !b.buscando());
}

void otroCanalPropio() {
  B b(99);
  b.base(6);
  uint32_t t = 0;
  b.atender(t, 6, 0, true);
  // espnow_canal=6: tras él, 1 y 11 en cualquier orden, y luego uno de los demás
  const uint8_t a = hastaElSalto(b, t, 6), c = hastaElSalto(b, t, a), d = hastaElSalto(b, t, c);
  assert(en(a, {1, 11}) && en(c, {1, 11}) && a != c && !en(d, {1, 6, 11}));
  b.base(0);  // fuera de 1-13: queda el 1
  assert(b.base() == 1);
  b.base(14);
  assert(b.base() == 1);
}

void elWifiPausaLaBusqueda() {
  B b(3);
  b.base(1);
  uint32_t t = 0;
  b.atender(t, 1, 0, true);
  const uint8_t primero = hastaElSalto(b, t, 1), segundo = primero == 6 ? 11 : 6;
  assert(en(primero, {6, 11}));
  // A 2 s de llegar el WiFi busca redes ~2 s: no se salta durante el escaneo...
  const uint32_t llegada = t;
  for (t = llegada; t < llegada + 2000; t += 10) assert(b.atender(t, primero, 0, true) == 0);
  for (; t < llegada + 4000; t += 10) assert(b.atender(t, primero, 0, false) == 0);
  assert(b.buscando());
  // ...y al terminar sigue donde iba (no vuelve al principio), con la espera de ese canal entera
  const uint32_t fin = t;
  assert(hastaElSalto(b, t, primero) == segundo);
  assert(t - fin >= MIN_OTRO);

  // Si el WiFi movió la radio (se conectó y se perdió), se empieza de cero desde donde quede
  b.reiniciar();
  assert(!b.buscando());
  b.atender(t, 1, 0, true);
  const uint32_t desde = t;
  assert(en(hastaElSalto(b, t, 1), {6, 11}) && t - desde >= MIN_BASE);
}

void pistaDeOtroCanal() {
  B b(5);
  b.base(1);
  uint32_t t = 1000;
  assert(b.pista(t, 5, 6, 0, true) == 6);  // oída en el 5, dice venir del 6
  assert(b.buscando() && b.saltos() == 1);
  // Si ahí no aparece en ~3 s, sigue buscando
  const uint32_t desde = t;
  const uint8_t siguiente = hastaElSalto(b, t, 6);
  assert(siguiente != 0 && siguiente != 6 && t - desde >= MIN_OTRO && t - desde <= MAX_OTRO + 10);

  assert(b.pista(t, 6, 6, 0, true) == 0);    // del mismo canal
  assert(b.pista(t, 6, 0, 0, true) == 0);    // firmware anterior: no lo dice
  assert(b.pista(t, 6, 14, 0, true) == 0);   // canal imposible
  assert(b.pista(t, 6, 7, 1, true) == 0);    // ya hay vecinos aquí: no se los abandona
  assert(b.pista(t, 6, 7, 0, false) == 0);   // con el WiFi ocupando la radio
}

void relojQueDaLaVuelta() {
  B b(11);
  b.base(1);
  uint32_t t = 0xFFFFFFFFu - 3000;  // millis() vuelve a 0 a media espera
  b.atender(t, 1, 0, true);
  const uint32_t desde = t;
  assert(en(hastaElSalto(b, t, 1), {6, 11}));
  const uint32_t estuvo = t - desde;
  assert(estuvo >= MIN_BASE && estuvo <= MAX_BASE + 10);
}

// --- Simulación: buses que se buscan --------------------------------------------------------------
// Cada bus manda una baliza cada 2 s ± 500 ms, y una enseguida (0-500 ms) al cambiar de canal; el otro la oye si está
// en el mismo canal. La tabla olvida a un vecino a los 10 s sin balizas.
struct Bus {
  B buscador;
  uint8_t canal;
  bool fijo;  // con WiFi: no se mueve
  uint32_t proxima = 0, oidoMs = 0;
  bool oye = false;
  uint32_t azar;
  Bus(uint32_t semilla, uint8_t c, bool f) : buscador(semilla), canal(c), fijo(f), azar(semilla * 2654435761u + 1) {
    buscador.base(c);
    proxima = aleatorio() % 501;
  }
  uint32_t aleatorio() {
    azar ^= azar << 13;
    azar ^= azar >> 17;
    azar ^= azar << 5;
    return azar;
  }
};

void baliza(Bus &de, Bus &a, uint32_t t) {
  if ((int32_t)(t - de.proxima) < 0) return;
  de.proxima = t + 1500 + de.aleatorio() % 1001;
  if (de.canal == a.canal) {
    a.oye = true;
    a.oidoMs = t;
  }
}

void paso(Bus &bus, uint32_t t) {
  if (bus.oye && t - bus.oidoMs >= 10000) bus.oye = false;  // caduca
  if (bus.fijo) return;
  const uint8_t c = bus.buscador.atender(t, bus.canal, bus.oye ? 1 : 0, true);
  if (c) {
    bus.canal = c;
    bus.proxima = t + bus.aleatorio() % 501;
  }
}

// ms hasta que los dos se oyen a la vez (0 si no pasa en `limite`)
uint32_t encuentro(Bus &a, Bus &b, uint32_t limite) {
  for (uint32_t t = 0; t < limite; t += 10) {
    paso(a, t);
    paso(b, t);
    baliza(a, b, t);
    baliza(b, a, t);
    if (a.oye && b.oye) return t ? t : 1;
  }
  return 0;
}

void unoFijoYUnoQueBusca() {
  // El que busca está en espnow_canal=1; el otro, conectado a un AP en cualquier canal
  uint32_t peor = 0;
  for (uint8_t canalWifi = 1; canalWifi <= B::ULTIMO_CANAL; canalWifi++) {
    for (uint32_t semilla = 1; semilla <= 50; semilla++) {
      Bus fijo(semilla, canalWifi, true), busca(semilla + 1000, 1, false);
      const uint32_t ms = encuentro(fijo, busca, 120000);
      assert(ms > 0);
      if (ms > peor) peor = ms;
    }
  }
  // Una vuelta entera: ~8 s en el 1, 12 canales a ~3 s y la baliza que falte
  assert(peor <= MAX_BASE + 12 * MAX_OTRO + 2600);
  printf("  uno fijo en cualquier canal (1-13) y otro que busca desde el 1: se encuentran en %.1f s como mucho\n",
         peor / 1000.0);
}

void dosQueBuscan() {
  // Dos buses sin WiFi con espnow_canal distinto: los dos se mueven
  uint32_t peor = 0, suma = 0, n = 0;
  for (uint8_t ca = 1; ca <= B::ULTIMO_CANAL; ca++) {
    for (uint8_t cb = 1; cb <= B::ULTIMO_CANAL; cb++) {
      if (ca == cb) continue;
      for (uint32_t semilla = 1; semilla <= 40; semilla++) {
        Bus a(semilla * 7 + ca, ca, false), b(semilla * 13 + cb + 500, cb, false);
        const uint32_t ms = encuentro(a, b, 1800000);
        assert(ms > 0);  // en media hora siempre
        if (ms > peor) peor = ms;
        suma += ms;
        n++;
      }
    }
  }
  printf("  dos que buscan, con espnow_canal distinto: se encuentran en %.0f s de media y %.0f s como mucho\n",
         suma / 1000.0 / n, peor / 1000.0);
}

}  // namespace

int main() {
  recorridoCompleto();
  conVecinosNoSalta();
  otroCanalPropio();
  elWifiPausaLaBusqueda();
  pistaDeOtroCanal();
  relojQueDaLaVuelta();
  unoFijoYUnoQueBusca();
  dosQueBuscan();
  puts("ESP-NOW: búsqueda del canal de los vecinos (recorrido, pausa del WiFi, pista, reloj y simulación) OK");
}
