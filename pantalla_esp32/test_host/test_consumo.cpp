// M3-06: consumo de datos por canal y por día (/consumo.ab, dos ranuras desde #129).
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include <ConsumoDatos.h>

#include "fs_falso.h"

namespace {

const uint32_t D1 = 20261121, D2 = 20261122;

void hoyYFecha() {
  // referencias calculadas aparte (UTC): 0 = 1970-01-01; 951782400 = 2000-02-29 00:00; 1792972800 = 2026-10-26 00:00
  assert(ConsumoDatos::fechaDeUnix(0) == 19700101);
  assert(ConsumoDatos::fechaDeUnix(951782400) == 20000229);
  assert(ConsumoDatos::fechaDeUnix(1792972800) == 20261026);
  assert(ConsumoDatos::fechaDeUnix(1792972800 + 86399) == 20261026);
  assert(ConsumoDatos::fechaDeUnix(1792972800 + 86400) == 20261027);
  assert(ConsumoDatos::fechaDeUnix(1798675200) == 20261231);
  assert(ConsumoDatos::fechaDeUnix(1798675200 + 86400) == 20270101);  // cambio de año
  printf("  fecha AAAAMMDD de una hora unix OK\n");
}

void contarPorCanalYDia() {
  FsFalso fs;
  ConsumoDatos c(fs);
  assert(!c.iniciar() && c.dias() == 0);
  uint32_t tx, rx;
  c.bytesHoy(tx, rx);
  assert(tx == 0 && rx == 0);

  c.fijarFecha(D1);
  c.registrar(CanalDatos::WIFI, 1000, 200);
  c.registrar(CanalDatos::BLE, 30, 40);
  c.registrar(CanalDatos::ESPNOW, 5, 6);
  c.registrar(CanalDatos::WIFI, 500, 100);
  c.bytesHoy(tx, rx);
  assert(tx == 1535 && rx == 346);
  c.bytesHoyCanal(CanalDatos::WIFI, tx, rx);
  assert(tx == 1500 && rx == 300);
  c.bytesHoyCanal(CanalDatos::BLE, tx, rx);
  assert(tx == 30 && rx == 40);

  // otro día: hoy empieza en cero y el anterior se conserva
  c.fijarFecha(D2);
  c.bytesHoy(tx, rx);
  assert(tx == 0 && rx == 0);
  c.registrar(CanalDatos::WIFI, 7, 8);
  c.bytesHoy(tx, rx);
  assert(tx == 7 && rx == 8);
  assert(c.dias() == 2 && c.dia(0).fecha == D1 && c.dia(0).tx[0] == 1500 && c.dia(1).fecha == D2);
  // volver al día anterior (reloj corregido) suma a ese día
  c.fijarFecha(D1);
  c.registrar(CanalDatos::WIFI, 1, 1);
  c.bytesHoyCanal(CanalDatos::WIFI, tx, rx);
  assert(tx == 1501 && rx == 301 && c.dias() == 2);

  // los totales saturan en vez de dar la vuelta
  c.registrar(CanalDatos::BLE, 0xFFFFFFF0u, 0);
  c.registrar(CanalDatos::BLE, 100, 0);
  c.bytesHoyCanal(CanalDatos::BLE, tx, rx);
  assert(tx == 0xFFFFFFFFu);
  c.registrar(CanalDatos::ESPNOW, 0xFFFFFFF0u, 0);
  c.bytesHoy(tx, rx);
  assert(tx == 0xFFFFFFFFu);
  printf("  suma por canal, por día, volver a un día anterior y saturación OK\n");
}

void contadoresDeLosEnlaces() {
  FsFalso fs;
  ConsumoDatos c(fs);
  c.fijarFecha(D1);
  uint32_t tx, rx;
  // el contador del enlace crece: se registra lo que creció
  c.observar(CanalDatos::WIFI, 100, 50);   // primera vez: lo que lleve desde el arranque
  c.observar(CanalDatos::WIFI, 100, 50);   // sin cambios
  c.observar(CanalDatos::WIFI, 350, 80);
  c.bytesHoyCanal(CanalDatos::WIFI, tx, rx);
  assert(tx == 350 && rx == 80);
  // el enlace se reinició (el contador bajó): vale el valor nuevo entero
  c.observar(CanalDatos::WIFI, 40, 10);
  c.bytesHoyCanal(CanalDatos::WIFI, tx, rx);
  assert(tx == 390 && rx == 90);
  c.observar(CanalDatos::WIFI, 90, 12);
  c.bytesHoyCanal(CanalDatos::WIFI, tx, rx);
  assert(tx == 440 && rx == 92);
  // cada canal lleva su cuenta
  c.observar(CanalDatos::ESPNOW, 10, 20);
  c.bytesHoy(tx, rx);
  assert(tx == 450 && rx == 112);
  // al pasar medianoche lo que crece cuenta para el día nuevo
  c.fijarFecha(D2);
  c.observar(CanalDatos::WIFI, 190, 12);
  c.bytesHoyCanal(CanalDatos::WIFI, tx, rx);
  assert(tx == 100 && rx == 0);
  assert(c.dia(0).tx[0] == 440 && c.dia(1).tx[0] == 100);
  printf("  contadores de los enlaces: crecimiento, reinicio del enlace y cambio de día OK\n");
}

void sinHoraAunYHistorialDe31() {
  FsFalso fs;
  ConsumoDatos c(fs);
  uint32_t tx, rx;
  // sin hora: se cuenta y se ve en bytesHoy, pero no se guarda; al saber la fecha pasa a ese día
  c.registrar(CanalDatos::WIFI, 100, 10);
  c.observar(CanalDatos::BLE, 7, 3);
  c.bytesHoy(tx, rx);
  assert(tx == 107 && rx == 13 && c.dias() == 0 && !c.hayCambios());
  c.fijarFecha(D1);
  c.bytesHoy(tx, rx);
  assert(tx == 107 && rx == 13 && c.dias() == 1 && c.hayCambios());
  c.registrar(CanalDatos::WIFI, 1, 0);
  c.bytesHoy(tx, rx);
  assert(tx == 108);
  c.fijarFecha(D2);  // lo de antes de la hora no se suma otra vez al día siguiente
  c.bytesHoy(tx, rx);
  assert(tx == 0 && rx == 0 && c.dias() == 1);

  // 40 días seguidos: quedan los 31 últimos
  ConsumoDatos h(fs);
  uint32_t fechas[40];
  for (int i = 0; i < 40; i++) {
    fechas[i] = ConsumoDatos::fechaDeUnix(1792972800 + i * 86400);
    h.fijarFecha(fechas[i]);
    h.registrar(CanalDatos::WIFI, i + 1, 0);
  }
  assert(h.dias() == ConsumoDatos::MAX_DIAS);
  assert(h.dia(0).fecha == fechas[9] && h.dia(30).fecha == fechas[39]);
  assert(h.dia(0).tx[0] == 10 && h.dia(30).tx[0] == 40);
  h.bytesHoy(tx, rx);
  assert(tx == 40);
  // una fecha anterior a todas con el historial lleno no cabe: no rompe nada ni cambia los días guardados
  h.fijarFecha(fechas[0]);
  h.registrar(CanalDatos::WIFI, 999, 0);
  assert(h.dias() == 31 && h.dia(0).fecha == fechas[9] && h.dia(0).tx[0] == 10);
  printf("  sin hora todavía, historial de 31 días y fecha anterior con el historial lleno OK\n");
}

void guardarCada5Minutos() {
  FsFalso fs;
  ConsumoDatos c(fs);
  c.fijarFecha(D1);
  uint32_t ms = 1000;
  assert(!c.atender(ms));  // arranca el reloj
  c.registrar(CanalDatos::WIFI, 500, 100);
  assert(c.hayCambios());
  assert(!c.atender(ms + 4 * 60 * 1000) && !fs.existe("/consumo.ab"));  // aún no pasaron 5 minutos
  assert(c.atender(ms + ConsumoDatos::INTERVALO_MS) && fs.existe("/consumo.ab") && !c.hayCambios());
  ms += ConsumoDatos::INTERVALO_MS;
  // sin cambios no escribe (un corte programado en la operación 0 lo delataría)
  fs.programarCorte(0);
  assert(!c.atender(ms + 10 * ConsumoDatos::INTERVALO_MS));
  fs.sinCorte();
  // con cambios guarda otra vez a los 5 minutos del último guardado
  c.registrar(CanalDatos::BLE, 1, 2);
  assert(!c.atender(ms + 1000) && c.atender(ms + ConsumoDatos::INTERVALO_MS));
  // el contador de milisegundos da la vuelta a los 49 días: la resta sigue valiendo
  ConsumoDatos d(fs);
  d.fijarFecha(D2);
  assert(!d.atender(0xFFFFFFFFu - 1000));
  d.registrar(CanalDatos::WIFI, 1, 1);
  assert(!d.atender(0xFFFFFFFFu - 500));                     // 500 ms después, sin haber dado la vuelta: aún no
  assert(d.atender(0xFFFFFFFFu - 1000 + ConsumoDatos::INTERVALO_MS));
  // si no se pudo guardar, queda pendiente y se reintenta a los 5 minutos
  ConsumoDatos e(fs);
  e.fijarFecha(D2);
  e.atender(0);
  e.registrar(CanalDatos::WIFI, 1, 1);
  fs.agregarFalla = true;
  assert(!e.atender(ConsumoDatos::INTERVALO_MS) && e.hayCambios());
  fs.agregarFalla = false;
  assert(!e.atender(ConsumoDatos::INTERVALO_MS + 1000) && e.hayCambios());
  assert(e.atender(2 * ConsumoDatos::INTERVALO_MS) && !e.hayCambios());
  printf("  guardado cada 5 minutos solo si hay cambios, vuelta del reloj y reintento tras un fallo OK\n");
}

void reiniciarYCortes() {
  FsFalso fs;
  {
    ConsumoDatos c(fs);
    c.fijarFecha(D1);
    c.registrar(CanalDatos::WIFI, 1000, 200);
    c.registrar(CanalDatos::ESPNOW, 3, 4);
    c.fijarFecha(D2);
    c.registrar(CanalDatos::BLE, 9, 9);
    assert(c.guardar());
  }
  // tras reiniciar está todo
  ConsumoDatos r(fs);
  assert(r.iniciar() && r.dias() == 2);
  r.fijarFecha(D2);
  uint32_t tx, rx;
  r.bytesHoy(tx, rx);
  assert(tx == 9 && rx == 9);
  r.fijarFecha(D1);
  r.bytesHoy(tx, rx);
  assert(tx == 1003 && rx == 204);
  // y se sigue contando sobre lo guardado
  r.observar(CanalDatos::WIFI, 50, 5);
  r.bytesHoyCanal(CanalDatos::WIFI, tx, rx);
  assert(tx == 1050 && rx == 205);

  // un corte en cada operación de guardar: queda lo guardado antes o lo de ahora, nunca un archivo roto
  FsFalso base = fs;
  int cortes = 0, antes = 0, despues = 0;
  bool terminado = false;
  for (long k = 0; !terminado; k++) {
    for (uint32_t semilla = 1; semilla <= 16; semilla++) {  // lo que sobrevive de lo no vaciado cambia con el azar
      FsFalso f = base;
      f.azar = semilla * 7919;
      f.programarCorte(k);
      bool cortado = false;
      try {
        ConsumoDatos c(f);
        c.iniciar();
        c.fijarFecha(D2);
        c.registrar(CanalDatos::WIFI, 111, 0);
        assert(c.guardar());
      } catch (CorteDeEnergia &) {
        cortado = true;
      }
      f.sinCorte();
      if (!cortado) {
        terminado = true;
        break;
      }
      cortes++;
      f.aplicarCorte();
      ConsumoDatos q(f);
      assert(q.iniciar() && q.dias() == 2);
      const uint32_t wifi = q.dia(1).tx[0];
      assert(wifi == 0 || wifi == 111);
      (wifi == 0 ? antes : despues)++;
      assert(q.dia(0).tx[0] == 1000 && q.dia(1).tx[1] == 9);
    }
  }
  assert(cortes >= 16 && antes > 0 && despues > 0);

  // archivo dañado o con datos que no valen: arranca sin historial
  FsFalso mala = base;
  mala.archivos["/consumo.ab"].durable[5] ^= 0x01;  // la generación de las dos ranuras: el CRC no cuadra
  mala.archivos["/consumo.ab"].durable[512 + 5] ^= 0x01;
  mala.archivos["/consumo.ab"].cache = mala.archivos["/consumo.ab"].durable;
  ConsumoDatos m(mala);
  assert(!m.iniciar() && m.dias() == 0);
  printf("  reiniciar sin perder el historial, %d cortes de energía al guardar (%d viejo, %d nuevo) y archivo dañado OK\n",
         cortes, antes, despues);
}

// La prueba de una hora del issue, en el ordenador: un enlace que cuenta bytes, loop() cada 30 s, guardado cada 5 min
// y un reinicio a mitad (con el contador del enlace de nuevo en cero). Lo registrado coincide con lo que pasó.
void unaHoraConReinicio() {
  FsFalso fs;
  uint32_t ms = 0, hora = 1792972800 + 20 * 3600;  // 2026-10-26 20:00 UTC: cruza la medianoche
  uint64_t realTx[3] = {0, 0, 0}, realRx[3] = {0, 0, 0};  // lo que de verdad pasó, por día se separa abajo
  uint64_t realDia1Tx = 0, realDia2Tx = 0;
  uint32_t contadorTx[3] = {0, 0, 0}, contadorRx[3] = {0, 0, 0};  // los del enlace, desde su arranque
  auto arrancar = [&](ConsumoDatos &c) {
    c.iniciar();
    contadorTx[0] = contadorTx[1] = contadorTx[2] = 0;  // el enlace empieza de cero al reiniciar
    contadorRx[0] = contadorRx[1] = contadorRx[2] = 0;
  };
  ConsumoDatos *c = new ConsumoDatos(fs);
  arrancar(*c);
  uint32_t semilla = 99;
  auto azar = [&]() { return semilla = semilla * 1664525u + 1013904223u, semilla >> 8; };
  for (int paso = 0; paso < 8 * 60 * 2; paso++) {  // 8 horas, un paso cada 30 s
    ms += 30000;
    hora += 30;
    // el tráfico de este paso
    for (int canal = 0; canal < 2; canal++) {
      const uint32_t t = azar() % 700, r = azar() % 300;
      contadorTx[canal] += t;
      contadorRx[canal] += r;
      realTx[canal] += t;
      realRx[canal] += r;
      (ConsumoDatos::fechaDeUnix(hora) == 20261026 ? realDia1Tx : realDia2Tx) += t;
    }
    c->fijarFecha(ConsumoDatos::fechaDeUnix(hora));
    c->observar(CanalDatos::WIFI, contadorTx[0], contadorRx[0]);
    c->observar(CanalDatos::BLE, contadorTx[1], contadorRx[1]);
    c->atender(ms);
    if (paso == 8 * 60) {  // a las 4 horas, reinicio limpio (se guarda antes, como al terminar el ciclo de 5 min)
      assert(c->guardar());
      delete c;
      c = new ConsumoDatos(fs);
      arrancar(*c);
    }
  }
  assert(c->guardar());
  // lo registrado es lo que pasó, por canal y sumando los días
  uint64_t regTx[3] = {0, 0, 0}, regRx[3] = {0, 0, 0};
  for (size_t i = 0; i < c->dias(); i++)
    for (int k = 0; k < 3; k++) {
      regTx[k] += c->dia(i).tx[k];
      regRx[k] += c->dia(i).rx[k];
    }
  for (int k = 0; k < 2; k++) assert(regTx[k] == realTx[k] && regRx[k] == realRx[k]);
  assert(regTx[2] == 0);
  assert(c->dias() == 2 && c->dia(0).tx[0] + c->dia(0).tx[1] == realDia1Tx && c->dia(1).tx[0] + c->dia(1).tx[1] == realDia2Tx);
  delete c;
  printf("  8 horas simuladas con un reinicio y cambio de día: lo registrado coincide con el contador del enlace OK\n");
}

}  // namespace

int main() {
  hoyYFecha();
  contarPorCanalYDia();
  contadoresDeLosEnlaces();
  sinHoraAunYHistorialDe31();
  guardarCada5Minutos();
  reiniciarYCortes();
  unaHoraConReinicio();
  printf("M3-06 consumo de datos: por canal y día, 31 días, guardado cada 5 min, cortes y contadores de enlace OK\n");
  return 0;
}
