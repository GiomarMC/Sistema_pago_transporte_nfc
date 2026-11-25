// M3-05: paquetes ajenos (/ajenos) y LISTAS firmados (/firmados) en la microSD, con CanalFalso y cortes de energía.
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include <algorithm>
#include <vector>

#include <CanalFalso.h>
#include <PaquetesGuardados.h>
#include <ProtocoloSP.h>

#include "fs_falso.h"

namespace {

using Bytes = std::vector<uint8_t>;

const uint8_t CLAVE[32] = {1, 2, 3};

// Un EVENTOS firmado con HMAC: `n` eventos con ids desde `primero`
Bytes eventos(uint16_t origen, uint32_t secuencia, uint32_t creado, uint32_t primero, uint8_t n = 3) {
  static sp::Paquete p;
  memset(&p, 0, sizeof(p));
  p.cabecera = {sp::EVENTOS, origen, sp::SERVIDOR, secuencia, creado};
  p.eventos.n = n;
  for (uint8_t i = 0; i < n; i++) {
    p.eventos.eventos[i].idEvento = primero + i;
    p.eventos.eventos[i].tipo = sp::VIAJE;
    p.eventos.eventos[i].fecha = creado;
  }
  Bytes b(sp::TAM_MAXIMO);
  const size_t largo = sp::empaquetar(p, CLAVE, b.data(), b.size());
  assert(largo > 0);
  b.resize(largo);
  return b;
}

// Un LISTAS "del servidor": la firma ECDSA no se comprueba aquí, así que son 64 bytes cualquiera
Bytes listas(uint32_t desde, uint32_t hasta, uint32_t secuencia, uint32_t creado = 1000, uint16_t entradas = 2) {
  const size_t cuerpo = 12 + entradas * 16;
  Bytes b(sp::TAM_CABECERA + cuerpo + sp::FIRMA_ECDSA, 0xEE);
  b[0] = 'S';
  b[1] = 'P';
  b[2] = sp::VERSION;
  b[3] = sp::LISTAS;
  auto pon32 = [&](size_t pos, uint32_t v) {
    for (int i = 0; i < 4; i++) b[pos + i] = v >> (8 * i);
  };
  b[4] = b[5] = 0;  // origen: servidor
  b[6] = 0x34, b[7] = 0x12;
  pon32(8, secuencia);
  pon32(12, creado);
  b[16] = cuerpo, b[17] = cuerpo >> 8;
  b[18] = b[19] = 0;
  memset(&b[sp::TAM_CABECERA], 0, cuerpo);
  pon32(sp::TAM_CABECERA, desde);
  pon32(sp::TAM_CABECERA + 4, hasta);
  b[sp::TAM_CABECERA + 8] = entradas;
  return b;
}

size_t archivosEn(FsFalso &fs, const char *carpeta) {
  std::vector<std::string> n;
  fs.listar(carpeta, n);
  return n.size();
}

std::vector<InfoPaquete> todos(PaquetesAjenos &p) {
  std::vector<InfoPaquete> v(PaquetesAjenos::MAXIMO + 5);
  v.resize(p.listar(v.data(), v.size()));
  return v;
}

void guardarListarYAcusar() {
  FsFalso fs;
  PaquetesAjenos p(fs);
  assert(p.iniciar() && p.cantidad() == 0);
  // del origen 5: ids 1-3 y 4-6; del 6: ids 10-12. Guardados sin orden por hora (creado)
  const Bytes a2 = eventos(5, 11, 200, 4), a1 = eventos(5, 10, 100, 1), b1 = eventos(6, 3, 150, 10);
  assert(p.guardar(a2.data(), a2.size()) && p.guardar(a1.data(), a1.size()) && p.guardar(b1.data(), b1.size()));
  assert(p.cantidad() == 3 && archivosEn(fs, "/ajenos") == 3);
  assert(fs.existe("/ajenos/5-10.bin") && fs.existe("/ajenos/5-11.bin") && fs.existe("/ajenos/6-3.bin"));

  // listar: del más antiguo al más nuevo (por la hora del paquete, no por cuándo llegó)
  auto l = todos(p);
  assert(l.size() == 3);
  assert(l[0].a == 5 && l[0].b == 10 && l[0].creado == 100 && l[0].ultimoEvento == 3 && l[0].largo == a1.size());
  assert(l[1].a == 6 && l[1].b == 3 && l[1].ultimoEvento == 12);
  assert(l[2].a == 5 && l[2].b == 11 && l[2].ultimoEvento == 6);

  // leer devuelve lo mismo que se guardó
  Bytes buf(sp::TAM_MAXIMO);
  assert(p.leer(5, 11, buf.data(), buf.size()) == a2.size() && memcmp(buf.data(), a2.data(), a2.size()) == 0);
  assert(p.leer(5, 99, buf.data(), buf.size()) == 0);   // no existe
  assert(p.leer(5, 11, buf.data(), 10) == 0);            // no cabe

  // guardar otra vez el mismo no cambia nada ni escribe (un corte programado en la operación 0 lo delataría)
  fs.programarCorte(0);
  assert(p.guardar(a1.data(), a1.size()) && p.cantidad() == 3);
  fs.sinCorte();

  // acuse: del origen 5 hasta el evento 3 cubre el primero y no el segundo (llega al 6)
  assert(p.acusar(5, 2) == 0);  // no llega ni al 3
  assert(p.acusar(5, 3) == 1);
  assert(!fs.existe("/ajenos/5-10.bin") && fs.existe("/ajenos/5-11.bin") && p.cantidad() == 2);
  assert(p.acusar(7, 1000) == 0 && p.cantidad() == 2);  // otro origen, no hay nada
  assert(p.acusar(5, 1000) == 1 && p.acusar(5, 1000) == 0);  // el resto del 5; ya no queda
  assert(p.cantidad() == 1 && fs.existe("/ajenos/6-3.bin"));
  assert(p.acusar(6, 11) == 0 && p.acusar(6, 12) == 1 && p.cantidad() == 0 && archivosEn(fs, "/ajenos") == 0);
  printf("  guardar, listar por hora, leer, duplicados y borrar por acuse OK\n");
}

void limiteDe500() {
  FsFalso fs;
  PaquetesAjenos p(fs);
  assert(p.iniciar());
  // 510 paquetes; llegan en orden de hora salvo que cada 7.º llega con una hora vieja
  std::vector<uint32_t> horas;
  for (uint32_t i = 0; i < 510; i++) {
    const uint32_t hora = 10000 + i * 10 - (i % 7 == 0 && i > 0 ? 5000 : 0);
    horas.push_back(hora);
    const Bytes b = eventos(1 + i % 4, 1000 + i, hora, 1 + i * 3);
    assert(p.guardar(b.data(), b.size()));
    assert(p.cantidad() <= PaquetesAjenos::MAXIMO);
  }
  assert(p.cantidad() == 500 && archivosEn(fs, "/ajenos") == 500);
  // se quedaron las 500 horas más nuevas
  std::vector<uint32_t> orden = horas;
  std::sort(orden.begin(), orden.end());
  orden.erase(orden.begin(), orden.begin() + 10);
  auto l = todos(p);
  assert(l.size() == 500);
  for (size_t i = 0; i < l.size(); i++) assert(l[i].creado == orden[i]);
  // y el recién guardado siempre sigue, aunque sea el más viejo de todos: se borra otro
  const Bytes viejo = eventos(9, 1, 1, 1);
  assert(p.guardar(viejo.data(), viejo.size()) && p.cantidad() == 500 && fs.existe("/ajenos/9-1.bin"));
  l = todos(p);
  assert(l[0].a == 9 && l[0].creado == 1 && l[1].creado == orden[1]);
  printf("  límite de 500: 510 guardados, quedan los 500 más nuevos por hora OK\n");
}

void formaDelPaquete() {
  FsFalso fs;
  PaquetesAjenos p(fs);
  assert(p.iniciar());
  const Bytes bueno = eventos(5, 1, 100, 1);
  Bytes x = bueno;
  assert(p.guardar(x.data(), x.size() - 1) == false);              // cortado
  x.push_back(0);
  assert(p.guardar(x.data(), x.size()) == false);                  // bytes de más
  x = bueno;
  x[0] = 'X';
  assert(!p.guardar(x.data(), x.size()));                          // magia
  x = bueno;
  x[3] = sp::HOLA;
  assert(!p.guardar(x.data(), x.size()));                          // no es EVENTOS
  const Bytes dellsrv = listas(0, 1, 1);
  assert(!p.guardar(dellsrv.data(), dellsrv.size()));              // LISTAS
  x = bueno;
  x[4] = x[5] = 0;
  assert(!p.guardar(x.data(), x.size()));                          // origen 0 = servidor, no es ajeno
  x = bueno;
  x[sp::TAM_CABECERA] = 0;                                         // n = 0 eventos
  assert(!p.guardar(x.data(), x.size()));
  assert(!p.guardar(nullptr, 0));
  assert(p.cantidad() == 0 && archivosEn(fs, "/ajenos") == 0);
  assert(p.guardar(bueno.data(), bueno.size()) && p.cantidad() == 1);

  // la tarjeta falla al escribir (también el diario): no queda nada a medias, y después sigue funcionando
  fs.agregarFalla = true;
  const Bytes otro = eventos(5, 2, 200, 4);
  assert(!p.guardar(otro.data(), otro.size()));
  fs.agregarFalla = false;
  assert(p.cantidad() == 1 && !fs.existe("/ajenos/5-2.bin") && archivosEn(fs, "/ajenos") == 1);
  assert(p.guardar(otro.data(), otro.size()) && p.cantidad() == 2);
  PaquetesAjenos r(fs);
  assert(r.iniciar() && r.cantidad() == 2);
  printf("  paquetes que no valen y escritura fallida OK\n");
}

// Tras comprobar uno a uno, todo lo anotado tiene su archivo entero y todo archivo de la carpeta está anotado
// (una entrada sin archivo, resto de una BAJA sin vaciar que se perdió, se quita al leerla)
void coherente(FsFalso &fs, PaquetesAjenos &p) {
  Bytes buf(sp::TAM_MAXIMO);
  for (const InfoPaquete &i : todos(p)) {
    const size_t n = p.leer(i.a, i.b, buf.data(), buf.size());
    assert(n == i.largo || n == 0);  // o está entero, o no tenía archivo y lo quita del índice
    if (n == 0) assert(p.leer(i.a, i.b, buf.data(), buf.size()) == 0);
  }
  assert(archivosEn(fs, "/ajenos") == p.cantidad());
}

void arranqueConDiario() {
  FsFalso fs;
  {
    PaquetesAjenos p(fs);
    assert(p.iniciar());
    for (uint32_t i = 0; i < 20; i++) {
      const Bytes b = eventos(2 + i % 3, 50 + i, 500 + i, 1 + i * 3);
      assert(p.guardar(b.data(), b.size()));
    }
  }
  // cosas ajenas en la carpeta: ni se miran ni se tocan
  fs.archivos["/ajenos/notas.txt"].durable = fs.archivos["/ajenos/notas.txt"].cache = {1, 2, 3};
  fs.archivos["/ajenos/5-6.tmp"].durable = fs.archivos["/ajenos/5-6.tmp"].cache = {1};

  // el arranque lee el diario, nada más: no abre ningún paquete ni recorre la carpeta
  fs.lecturas = 0;
  PaquetesAjenos p(fs);
  assert(p.iniciar() && p.cantidad() == 20);
  assert(fs.lecturas == 1);
  assert(fs.existe("/ajenos/notas.txt") && archivosEn(fs, "/ajenos") == 22);
  auto l = todos(p);
  assert(l.size() == 20 && l[0].creado == 500 && l[19].creado == 519 && l[0].ultimoEvento == 3);

  // un registro cortado al final del diario (corte a mitad de anotar): se descarta, y se sigue escribiendo bien
  FsFalso corte = fs;
  auto &log = corte.archivos["/ajenos.log"];
  log.durable.insert(log.durable.end(), {9, 9, 9, 9, 9, 9, 9, 9, 9, 9});
  log.cache = log.durable;
  PaquetesAjenos q(corte);
  assert(q.iniciar() && q.cantidad() == 20);
  assert(corte.archivos["/ajenos.log"].cache.size() % CarpetaPaquetes::TAM_REGISTRO == 0);  // quedó reescrito
  const Bytes nuevo = eventos(7, 1, 1000, 1);
  assert(q.guardar(nuevo.data(), nuevo.size()));
  PaquetesAjenos q2(corte);
  assert(q2.iniciar() && q2.cantidad() == 21);

  // un registro dañado en el medio: lo que sigue se pierde del índice, pero no se rompe nada
  FsFalso mala = fs;
  mala.archivos["/ajenos.log"].durable[CarpetaPaquetes::TAM_REGISTRO * 5 + 6] ^= 0x10;
  mala.archivos["/ajenos.log"].cache = mala.archivos["/ajenos.log"].durable;
  PaquetesAjenos m(mala);
  assert(m.iniciar() && m.cantidad() == 5);

  // sin diario y con un .tmp a medias (sin el registro FIN del final) no vale: arranca vacío
  FsFalso medio = fs;
  auto copia = medio.archivos["/ajenos.log"].cache;
  copia.resize(3 * CarpetaPaquetes::TAM_REGISTRO);
  medio.archivos.erase("/ajenos.log");
  medio.archivos["/ajenos.tmp"].durable = medio.archivos["/ajenos.tmp"].cache = copia;
  PaquetesAjenos t(medio);
  assert(t.iniciar() && t.cantidad() == 0);

  // sin diario (tarjeta nueva o borrada) arranca vacío
  FsFalso vacia;
  PaquetesAjenos v(vacia);
  assert(v.iniciar() && v.cantidad() == 0);
  printf("  arranque leyendo solo el diario, cosas ajenas en la carpeta, final cortado y registro dañado OK\n");
}

void archivosCortadosOFaltantes() {
  FsFalso fs;
  PaquetesAjenos p(fs);
  assert(p.iniciar());
  const Bytes b1 = eventos(3, 8, 80, 1), b2 = eventos(3, 9, 90, 4), b3 = eventos(3, 10, 100, 7);
  for (const Bytes *b : {&b1, &b2, &b3}) assert(p.guardar(b->data(), b->size()));
  Bytes buf(sp::TAM_MAXIMO);

  // un archivo cortado (el corte llegó entre anotar y terminar de escribir): al leerlo se descubre y se quita
  fs.archivos["/ajenos/3-8.bin"].cache.resize(30);
  fs.archivos["/ajenos/3-8.bin"].durable.resize(30);
  assert(p.leer(3, 8, buf.data(), buf.size()) == 0);
  assert(p.cantidad() == 2 && !fs.existe("/ajenos/3-8.bin"));
  // y si el mismo paquete llega otra vez, se vuelve a guardar entero
  assert(p.guardar(b1.data(), b1.size()) && p.cantidad() == 3);
  assert(p.leer(3, 8, buf.data(), buf.size()) == b1.size() && memcmp(buf.data(), b1.data(), b1.size()) == 0);

  // un paquete cortado que se vuelve a recibir sin haberlo leído antes también se arregla
  fs.archivos["/ajenos/3-9.bin"].cache.resize(10);
  fs.archivos["/ajenos/3-9.bin"].durable.resize(10);
  assert(p.guardar(b2.data(), b2.size()) && p.cantidad() == 3);
  assert(p.leer(3, 9, buf.data(), buf.size()) == b2.size());

  // un archivo que falta
  fs.archivos.erase("/ajenos/3-10.bin");
  assert(p.leer(3, 10, buf.data(), buf.size()) == 0 && p.cantidad() == 2);
  // lo anotado como quitado sigue quitado tras reiniciar
  PaquetesAjenos r(fs);
  assert(r.iniciar() && r.cantidad() == 2);
  printf("  archivo cortado o que falta: se descubre al leerlo y se arregla al recibirlo otra vez OK\n");
}

void arranqueConQuinientosYExpulsionExacta() {
  FsFalso base;
  {
    PaquetesAjenos p(base);
    assert(p.iniciar());
    for (uint32_t i = 0; i < PaquetesAjenos::MAXIMO; i++) {
      const Bytes b = eventos(1 + i % 5, 100 + i, 5000 + i, 1 + i * 3);
      assert(p.guardar(b.data(), b.size()));
    }
  }
  FsFalso fs = base;
  fs.lecturas = 0;
  PaquetesAjenos p(fs);
  assert(p.iniciar() && p.cantidad() == 500);
  assert(fs.lecturas == 1);  // el diario, no los 500 paquetes (en la microSD real son ~90 s)
  const Bytes nuevo = eventos(9, 1, 9000, 1);
  fs.lecturas = 0;
  assert(p.guardar(nuevo.data(), nuevo.size()));
  assert(p.cantidad() == 500 && archivosEn(fs, "/ajenos") == 500 && fs.existe("/ajenos/9-1.bin"));
  assert(!fs.existe("/ajenos/1-100.bin"));  // el más antiguo (creado 5000)
  assert(fs.lecturas == 0);
  // un acuse con el índice recién cargado es exacto (ya no hay nada por leer): se van los 99 que quedan del origen 1 (el 100.º ya se expulsó)
  assert(p.acusar(1, 1000000) == 2 && p.confirmadosPorBorrar() == 97 && p.cantidad() == 498);  // de 2 en 2, no los 99 de golpe
  assert(p.acusar(1, 5, 5) == 5 && p.confirmadosPorBorrar() == 92);                              // y se puede pedir otro tope
  while (p.borrarConfirmados(2) > 0) {
  }
  assert(p.confirmadosPorBorrar() == 0 && p.cantidad() == 401);
  const Bytes repetido = eventos(1, 100, 5000, 1);  // el primero del origen 1, ya confirmado
  assert(p.guardar(repetido.data(), repetido.size()) && p.cantidad() == 401 && !fs.existe("/ajenos/1-100.bin"));
  printf("  arranque con 500: 1 lectura (el diario); guardar el 501.º borra exactamente el más antiguo OK\n");
}

void cortesAlGuardar() {
  FsFalso base;
  {
    PaquetesAjenos p(base);
    assert(p.iniciar());
    for (uint32_t i = 0; i < PaquetesAjenos::MAXIMO; i++) {
      const Bytes b = eventos(1 + i % 3, 100 + i, 5000 + i, 1 + i * 3);
      assert(p.guardar(b.data(), b.size()));
    }
  }
  const Bytes nuevo = eventos(9, 1, 9000, 1);
  int cortes = 0;
  for (long k = 0;; k++) {
    FsFalso fs = base;
    fs.programarCorte(k);
    bool cortado = false;
    try {
      PaquetesAjenos p(fs);
      p.iniciar();
      assert(p.guardar(nuevo.data(), nuevo.size()));
    } catch (CorteDeEnergia &) {
      cortado = true;
    }
    fs.sinCorte();
    if (!cortado) break;
    cortes++;
    fs.aplicarCorte();
    PaquetesAjenos r(fs);
    assert(r.iniciar() && r.cantidad() <= 500);
    // quien lo recibió lo vuelve a mandar: queda guardado, dentro del límite, y nada anotado sin archivo ni al revés
    assert(r.guardar(nuevo.data(), nuevo.size()));
    assert(r.cantidad() <= 500 && fs.existe("/ajenos/9-1.bin"));
    coherente(fs, r);
    PaquetesAjenos r2(fs);
    assert(r2.iniciar() && r2.cantidad() == r.cantidad());
  }
  assert(cortes >= 4);
  printf("  %d cortes de energía al guardar con 500 en la carpeta: sin cortados ni huérfanos OK\n", cortes);
}

void cortesAlReescribirElDiario() {
  FsFalso base;
  size_t antes = 0;
  {
    PaquetesAjenos p(base);
    assert(p.iniciar());
    for (uint32_t i = 0; i < 10; i++) {
      const Bytes b = eventos(1, 1 + i, 100 + i, 1 + i * 3);
      assert(p.guardar(b.data(), b.size()));
    }
    // altas y bajas hasta dejar el diario justo antes de reescribirse
    uint32_t n = 0, ev = 1000;
    while (p.registrosDelDiario() < 2 * p.cantidad() + CarpetaPaquetes::REGISTROS_DE_MAS) {
      const Bytes b = eventos(2, 100 + n, 5000 + n, ev);
      assert(p.guardar(b.data(), b.size()));
      assert(p.acusar(2, ev + 2) == 1);
      ev += 3;
      n++;
    }
    assert(n > 40);
    antes = p.cantidad();
    assert(antes == 10);
  }
  const size_t tamAntes = base.archivos["/ajenos.log"].cache.size();
  assert(tamAntes > 100 * CarpetaPaquetes::TAM_REGISTRO);
  // la operación que hace saltar la reescritura: un acuse que quita el primero (eventos 1 a 3)
  int cortes = 0;
  for (long k = 0;; k++) {
    FsFalso fs = base;
    fs.programarCorte(k);
    bool cortado = false;
    try {
      PaquetesAjenos p(fs);
      p.iniciar();
      assert(p.acusar(1, 3) == 1);
    } catch (CorteDeEnergia &) {
      cortado = true;
    }
    fs.sinCorte();
    if (!cortado) {
      PaquetesAjenos r(fs);
      assert(r.iniciar() && r.cantidad() == 9);
      assert(fs.archivos["/ajenos.log"].cache.size() < tamAntes / 2);  // se reescribió y quedó corto
      break;
    }
    cortes++;
    fs.aplicarCorte();
    PaquetesAjenos r(fs);
    assert(r.iniciar());
    // una BAJA sin vaciar pudo perderse: queda una entrada sin archivo, que se descubre al leerla
    coherente(fs, r);
    assert(r.cantidad() == 9 || r.cantidad() == 10);
    r.acusar(1, 3);  // el servidor lo vuelve a confirmar
    assert(r.cantidad() == 9);
    coherente(fs, r);
    PaquetesAjenos r2(fs);
    assert(r2.iniciar() && r2.cantidad() == 9);
  }
  assert(cortes >= 6);
  printf("  %d cortes de energía mientras se reescribe el diario: siempre queda un índice entero OK\n", cortes);
}

void firmados() {
  FsFalso fs;
  PaquetesFirmados p(fs);
  assert(p.iniciar() && p.cantidad() == 0);
  // 60 LISTAS con secuencia creciente; las versiones no tienen por qué crecer (una foto va con versiones de relleno)
  for (uint32_t i = 0; i < 60; i++) {
    const Bytes b = (i % 10 == 0) ? listas(0, 0x80000000u + i, 300 + i) : listas(100 + i, 101 + i, 300 + i);
    assert(p.guardar(b.data(), b.size()));
    assert(p.cantidad() <= PaquetesFirmados::MAXIMO);
  }
  assert(p.cantidad() == 50 && archivosEn(fs, "/firmados") == 50);
  std::vector<InfoPaquete> l(60);
  l.resize(p.listar(l.data(), l.size()));
  assert(l.size() == 50);
  for (size_t i = 0; i < l.size(); i++) assert(l[i].secuencia == 310 + i);  // los 50 últimos, del más viejo al más nuevo
  assert(l[0].a == 0 && l[0].b == 0x80000000u + 10 && l[0].largo == 12 + 2 * 16 + sp::TAM_CABECERA + sp::FIRMA_ECDSA);

  // mismas versiones otra vez: sustituye (la firma o la secuencia pudieron cambiar) y no crece
  const Bytes igual = listas(158, 159, 999, 2000);
  assert(p.guardar(igual.data(), igual.size()) && p.cantidad() == 50);
  Bytes buf(sp::TAM_MAXIMO);
  assert(p.leer(158, 159, buf.data(), buf.size()) == igual.size() && memcmp(buf.data(), igual.data(), igual.size()) == 0);
  l.resize(60);
  l.resize(p.listar(l.data(), l.size()));
  assert(l.back().secuencia == 999 && l.size() == 50);

  // lo que no es LISTAS del servidor no se guarda
  Bytes x = listas(1, 2, 5);
  x[4] = 7;  // origen 7
  assert(!p.guardar(x.data(), x.size()));
  x = listas(1, 2, 5);
  x[3] = sp::TARIFAS;
  assert(!p.guardar(x.data(), x.size()));
  const Bytes ev = eventos(5, 1, 1, 1);
  assert(!p.guardar(ev.data(), ev.size()));
  assert(p.cantidad() == 50);

  // reiniciar
  PaquetesFirmados r(fs);
  fs.lecturas = 0;
  assert(r.iniciar() && r.cantidad() == 50 && fs.lecturas == 1);
  l.resize(60);
  l.resize(r.listar(l.data(), l.size()));
  assert(l.size() == 50 && l.back().secuencia == 999);
  assert(r.leer(158, 159, buf.data(), buf.size()) == igual.size());
  printf("  firmados: 60 guardados, quedan los 50 últimos, sustitución, filtro y reinicio OK\n");
}

// Un bus recibe EVENTOS de otros por un canal, los guarda y, cuando hay red con el servidor, los reenvía;
// con el acuse del servidor los borra.
void conCanalFalso() {
  FsFalso fs;
  PaquetesAjenos p(fs);
  assert(p.iniciar());
  CanalFalso vecinos("espnow", 4), servidor("mqtt-wifi", 1);

  const Bytes b1 = eventos(5, 1, 100, 1), b2 = eventos(5, 2, 110, 4), b3 = eventos(6, 1, 120, 20, 2);
  for (const Bytes *b : {&b1, &b2, &b3}) vecinos.inyectar(b->data(), b->size());
  Bytes buf(sp::TAM_MAXIMO);
  size_t n;
  while ((n = vecinos.recibir(buf.data(), buf.size())) > 0) assert(p.guardar(buf.data(), n));
  assert(p.cantidad() == 3);

  // sin red con el servidor no se puede enviar y no se pierde nada
  servidor.ponerListo(false);
  std::vector<InfoPaquete> l(10);
  l.resize(p.listar(l.data(), l.size()));
  for (const InfoPaquete &i : l) {
    const size_t k = p.leer(i.a, i.b, buf.data(), buf.size());
    assert(k > 0 && !servidor.enviar(buf.data(), k));
  }
  assert(servidor.enviados() == 0 && p.cantidad() == 3);

  // con red: se envían en orden, y siguen guardados hasta que llegue el acuse
  servidor.ponerListo(true);
  for (const InfoPaquete &i : l) {
    const size_t k = p.leer(i.a, i.b, buf.data(), buf.size());
    assert(servidor.enviar(buf.data(), k));
  }
  assert(servidor.enviados() == 3 && p.cantidad() == 3);
  // el servidor dio por recibido hasta el evento 6 del bus 5 (b1 y b2) y nada del 6
  assert(p.acusar(5, 6) == 2 && p.cantidad() == 1);
  assert(p.acusar(6, 21) == 1 && p.cantidad() == 0);
  printf("  flujo con CanalFalso: recibir de vecinos, no perder sin red, reenviar y borrar con el acuse OK\n");
}

}  // namespace

int main() {
  guardarListarYAcusar();
  limiteDe500();
  formaDelPaquete();
  arranqueConDiario();
  archivosCortadosOFaltantes();
  arranqueConQuinientosYExpulsionExacta();
  cortesAlGuardar();
  cortesAlReescribirElDiario();
  firmados();
  conCanalFalso();
  printf("M3-05 paquetes ajenos y firmados: guardar, listar, acuse, límites, arranque con diario y cortes OK\n");
  return 0;
}
