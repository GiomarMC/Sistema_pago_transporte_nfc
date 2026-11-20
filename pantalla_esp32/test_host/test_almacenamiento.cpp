// M3-01 y M3-02: cola de eventos y estado persistente, con cortes de energía simulados.
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include <algorithm>
#include <vector>

#include <ColaEventos.h>
#include <Crc32.h>
#include <EstadoPersistente.h>

#include "fs_falso.h"

namespace {

int incidencias = 0;
void alIncidencia(const char *) { incidencias++; }

sp::Evento evento(uint32_t semilla) {
  sp::Evento e = {};
  e.tipo = sp::VIAJE;
  e.largoUid = 4;
  e.operacion = semilla & 0xFFFF;
  e.fecha = 1795000000 + semilla;
  e.idTarjeta = 700 + semilla % 50;
  e.uid[0] = semilla;
  e.uid[1] = semilla >> 8;
  e.monto = -130;
  e.saldoFinal = 10000 - 130 * (int32_t)(semilla % 70);
  e.contador = semilla;
  snprintf(e.detalle, sizeof(e.detalle), "cobro %u", (unsigned)semilla);
  return e;
}

bool igual(const sp::Evento &a, const sp::Evento &b) {
  uint8_t x[sp::TAM_EVENTO], y[sp::TAM_EVENTO];
  return sp::eventoABytes(a, x) && sp::eventoABytes(b, y) && memcmp(x, y, sizeof(x)) == 0;
}

// --- CRC32 ---------------------------------------------------------------------------------

void crc() {
  assert(crc32("123456789", 9) == 0xCBF43926u);  // el valor de referencia del CRC-32 estándar
  assert(crc32("", 0) == 0);
}

// --- Estado persistente --------------------------------------------------------------------

void estadoBasico() {
  FsFalso fs;
  EstadoPersistente e(fs);
  assert(!e.cargar() && e.estado().siguienteId == 1 && e.estado().hastaAcusado == 0);  // validador nuevo

  assert(e.fijarSiguienteId(41) && e.fijarHastaAcusado(30) && e.fijarVersiones(7, 9));
  EstadoPersistente otro(fs);
  assert(otro.cargar());
  assert(otro.estado().siguienteId == 41 && otro.estado().hastaAcusado == 30 && otro.estado().versionListas == 7 &&
         otro.estado().versionTarifas == 9);
  // Tras guardar quedan solo .bin y .bak; ningún .tmp
  assert(fs.existe(EstadoPersistente::ARCHIVO) && fs.existe(EstadoPersistente::ANTERIOR) && !fs.existe(EstadoPersistente::TEMPORAL));
}

void estadoCortePorCadaOperacion() {
  // Se corta la luz en cada una de las operaciones de un guardado: siempre queda un estado entero,
  // el anterior o el nuevo, y nunca algo a medias
  for (long k = 0; k < 12; k++) {
    FsFalso fs;
    {
      EstadoPersistente e(fs);
      e.cargar();
      assert(e.fijarHastaAcusado(10) && e.fijarVersiones(3, 4));  // estado anterior, ya guardado
    }
    bool cortado = false;
    {
      EstadoPersistente e(fs);
      assert(e.cargar());
      fs.programarCorte(k);
      try {
        e.fijarHastaAcusado(25);
        e.fijarVersiones(5, 6);
      } catch (CorteDeEnergia &) {
        cortado = true;
      }
    }
    fs.sinCorte();
    if (cortado) fs.aplicarCorte();
    EstadoPersistente r(fs);
    assert(r.cargar());
    const Estado &s = r.estado();
    const bool anterior = s.hastaAcusado == 10 && s.versionListas == 3 && s.versionTarifas == 4;
    const bool medio = s.hastaAcusado == 25 && s.versionListas == 3 && s.versionTarifas == 4;
    const bool nuevo = s.hastaAcusado == 25 && s.versionListas == 5 && s.versionTarifas == 6;
    assert(anterior || medio || nuevo);
    assert(r.fijarVersiones(8, 8));  // y se puede seguir guardando
  }
}

void estadoCaeEnLaCopiaAnterior() {
  FsFalso fs;
  EstadoPersistente e(fs);
  e.cargar();
  e.fijarHastaAcusado(10);
  e.fijarHastaAcusado(20);  // .bin = 20, .bak = 10
  fs.archivos[EstadoPersistente::ARCHIVO].durable[12] ^= 0xFF;  // se corrompe un byte de .bin
  fs.archivos[EstadoPersistente::ARCHIVO].cache = fs.archivos[EstadoPersistente::ARCHIVO].durable;
  EstadoPersistente r(fs);
  assert(r.cargar() && r.estado().hastaAcusado == 10);  // el CRC no cuadra: se usa .bak
  assert(r.fijarHastaAcusado(30));
  EstadoPersistente r2(fs);
  assert(r2.cargar() && r2.estado().hastaAcusado == 30);
}

void secuenciaNuncaSeRepite50Cortes() {
  FsFalso fs;
  uint32_t mayorEntregada = 0;
  int cortes = 0;
  for (int ronda = 0; ronda < 50; ronda++) {
    EstadoPersistente e(fs);
    e.cargar();
    fs.programarCorte(1 + fs.siguienteAzar() % 23);  // cien secuencias hacen ~24 operaciones de disco
    try {
      for (int i = 0; i < 100; i++) {
        const uint32_t s = e.siguienteSecuencia();
        assert(s != 0);
        assert(s > mayorEntregada);  // nunca una que ya se entregó, ni siquiera antes del corte
        mayorEntregada = s;
      }
    } catch (CorteDeEnergia &) {
      cortes++;
      fs.sinCorte();
      fs.aplicarCorte();
    }
    fs.sinCorte();
  }
  assert(cortes == 50);  // las 50 rondas terminaron en un corte de energía
  EstadoPersistente e(fs);
  e.cargar();
  assert(e.siguienteSecuencia() > mayorEntregada);
}

// --- Cola de eventos -----------------------------------------------------------------------

void colaBasica() {
  FsFalso fs;
  EstadoPersistente est(fs);
  est.cargar();
  ColaEventos cola(fs, est, alIncidencia);
  assert(cola.iniciar() && cola.registros() == 0 && cola.siguienteId() == 1);

  std::vector<sp::Evento> guardados;
  for (uint32_t i = 1; i <= 100; i++) {
    sp::Evento e = evento(i);
    assert(cola.agregar(e) && e.idEvento == i);
    guardados.push_back(e);
  }
  assert(cola.registros() == 100 && cola.ultimoId() == 100 && cola.pendientes() == 100);
  assert(fs.archivos[ColaEventos::ARCHIVO].durable.size() == 100 * sp::TAM_EVENTO);  // todo quedó vaciado a disco

  sp::Evento sal[30];
  assert(cola.leerDesde(0, sal, 30) == 30 && sal[0].idEvento == 1 && sal[29].idEvento == 30);
  assert(igual(sal[7], guardados[7]));
  assert(cola.leerDesde(95, sal, 30) == 5 && sal[0].idEvento == 96 && igual(sal[4], guardados[99]));
  assert(cola.leerDesde(100, sal, 30) == 0);

  // Un acuse de menos de la mitad no compacta; de más de la mitad sí
  assert(cola.acusarHasta(40) && cola.registros() == 100 && cola.compactaciones() == 0);
  assert(cola.acusarHasta(60) && cola.registros() == 40 && cola.compactaciones() == 1);
  assert(cola.pendientes() == 40 && cola.leerDesde(60, sal, 30) == 30 && sal[0].idEvento == 61 && igual(sal[0], guardados[60]));
  assert(fs.archivos[ColaEventos::ARCHIVO].durable.size() == 40 * sp::TAM_EVENTO);

  // Los ids siguen donde iban, incluso con todo confirmado y el archivo vacío, y tras reiniciar
  sp::Evento e = evento(101);
  assert(cola.agregar(e) && e.idEvento == 101);
  assert(cola.acusarHasta(101) && cola.registros() == 0 && cola.pendientes() == 0);
  EstadoPersistente est2(fs);
  est2.cargar();
  ColaEventos cola2(fs, est2, alIncidencia);
  assert(cola2.iniciar() && cola2.registros() == 0 && cola2.siguienteId() == 102);
  sp::Evento f = evento(102);
  assert(cola2.agregar(f) && f.idEvento == 102);
}

void servidorConfirmaMasDeLoQueHay() {
  // Se perdió la tarjeta y el validador empieza de cero, pero el servidor ya tiene hasta el evento 130
  FsFalso fs;
  EstadoPersistente est(fs);
  est.cargar();
  ColaEventos cola(fs, est, alIncidencia);
  cola.iniciar();
  for (uint32_t i = 1; i <= 3; i++) {
    sp::Evento e = evento(i);
    cola.agregar(e);
  }
  assert(cola.acusarHasta(130));
  assert(cola.ultimoId() == 130 && cola.pendientes() == 0 && cola.registros() == 0);
  sp::Evento e = evento(4);
  assert(cola.agregar(e) && e.idEvento == 131 && cola.pendientes() == 1);  // el siguiente es el 131, no el 4

  // y se mantiene tras reiniciar
  EstadoPersistente est2(fs);
  est2.cargar();
  ColaEventos cola2(fs, est2, alIncidencia);
  assert(cola2.iniciar() && cola2.ultimoId() == 131 && cola2.pendientes() == 1);
  sp::Evento f = evento(5);
  assert(cola2.agregar(f) && f.idEvento == 132);
}

void registroIncompletoSeDescarta() {
  FsFalso fs;
  EstadoPersistente est(fs);
  est.cargar();
  ColaEventos cola(fs, est, alIncidencia);
  cola.iniciar();
  for (uint32_t i = 1; i <= 5; i++) {
    sp::Evento e = evento(i);
    cola.agregar(e);
  }
  // Un corte dejó medio registro al final
  auto &a = fs.archivos[ColaEventos::ARCHIVO];
  a.durable.insert(a.durable.end(), 30, 0xAB);
  a.cache = a.durable;

  incidencias = 0;
  EstadoPersistente est2(fs);
  est2.cargar();
  ColaEventos c2(fs, est2, alIncidencia);
  assert(c2.iniciar());
  assert(incidencias == 1);  // se avisó de la incidencia
  assert(c2.registros() == 5 && c2.ultimoId() == 5);
  assert(fs.archivos[ColaEventos::ARCHIVO].durable.size() == 5 * sp::TAM_EVENTO);
  sp::Evento e = evento(6);
  assert(c2.agregar(e) && e.idEvento == 6);
}

void escrituraFallidaNoDesalinea() {
  FsFalso fs;
  EstadoPersistente est(fs);
  est.cargar();
  ColaEventos cola(fs, est, alIncidencia);
  cola.iniciar();
  sp::Evento e1 = evento(1);
  assert(cola.agregar(e1));

  fs.agregarFalla = true;  // la tarjeta escribe la mitad y falla
  sp::Evento e2 = evento(2);
  assert(!cola.agregar(e2));  // no se da el cobro por guardado
  assert(cola.registros() == 1 && cola.ultimoId() == 1);
  fs.agregarFalla = false;
  sp::Evento e3 = evento(3);
  assert(cola.agregar(e3) && e3.idEvento == 2);  // reutiliza el id: el 2 nunca se guardó
  sp::Evento sal[4];
  assert(cola.leerDesde(0, sal, 4) == 2 && igual(sal[0], e1) && igual(sal[1], e3));  // sin restos de la escritura rota
}

void compactacionCorteEnCadaOperacion() {
  // Se corta la luz en cada operación de la compactación. Los eventos sin acuse (61 a 100)
  // tienen que estar todos, una sola vez y enteros, y el siguiente id tiene que ser el 101
  for (long k = 0; k < 40; k++) {
    FsFalso fs;
    std::vector<sp::Evento> originales;
    {
      EstadoPersistente est(fs);
      est.cargar();
      ColaEventos cola(fs, est, alIncidencia);
      cola.iniciar();
      for (uint32_t i = 1; i <= 100; i++) {
        sp::Evento e = evento(i);
        assert(cola.agregar(e));
        originales.push_back(e);
      }
      assert(cola.acusarHasta(40));  // todavía sin compactar
    }
    bool cortado = false;
    {
      EstadoPersistente est(fs);
      est.cargar();
      ColaEventos cola(fs, est, alIncidencia);
      cola.iniciar();
      fs.programarCorte(k);
      try {
        cola.acusarHasta(60);
      } catch (CorteDeEnergia &) {
        cortado = true;
      }
    }
    fs.sinCorte();
    if (cortado) fs.aplicarCorte();

    EstadoPersistente est(fs);
    est.cargar();
    ColaEventos cola(fs, est, alIncidencia);
    assert(cola.iniciar());
    assert(cola.siguienteId() == 101 && cola.ultimoId() == 100);
    const uint32_t acusado = est.estado().hastaAcusado;
    assert(acusado == 40 || acusado == 60);
    sp::Evento sal[100];
    const size_t n = cola.leerDesde(acusado, sal, 100);
    assert(n == 100 - acusado);
    for (size_t i = 0; i < n; i++) {
      assert(sal[i].idEvento == acusado + 1 + i);
      assert(igual(sal[i], originales[acusado + i]));
    }
    // Y se puede seguir trabajando
    sp::Evento e = evento(101);
    assert(cola.agregar(e) && e.idEvento == 101);
  }
}

void veinteCortesEnCobrosConsecutivos() {
  FsFalso fs;
  std::vector<sp::Evento> confirmados;  // los cobros que se mostraron como hechos en pantalla
  uint32_t semilla = 1;
  int cortes = 0;
  for (int ronda = 0; ronda < 20; ronda++) {
    {
      EstadoPersistente est(fs);
      est.cargar();
      ColaEventos cola(fs, est, alIncidencia);
      assert(cola.iniciar());

      // Todo cobro confirmado antes sigue ahí (los que el servidor ya confirmó pueden estar compactados)
      const uint32_t acusado = est.estado().hastaAcusado;
      std::vector<sp::Evento> en_disco(cola.registros() + 1);
      const size_t n = cola.leerDesde(0, en_disco.data(), en_disco.size());
      for (const sp::Evento &c : confirmados) {
        if (c.idEvento <= acusado) continue;
        bool esta = false;
        for (size_t i = 0; i < n && !esta; i++) esta = en_disco[i].idEvento == c.idEvento && igual(en_disco[i], c);
        assert(esta);  // un cobro confirmado en pantalla no se perdió
      }
      for (size_t i = 1; i < n; i++) assert(en_disco[i].idEvento == en_disco[i - 1].idEvento + 1);  // ids sin huecos ni repetidos

      fs.programarCorte(2 + fs.siguienteAzar() % 45);
      try {
        for (int cobro = 0; cobro < 40; cobro++) {
          sp::Evento e = evento(semilla++);
          if (cola.agregar(e)) confirmados.push_back(e);  // solo aquí se muestra el resultado en pantalla
          if (cobro % 9 == 8) cola.acusarHasta(cola.ultimoId() - 3);
        }
      } catch (CorteDeEnergia &) {
        cortes++;
      }
    }
    fs.sinCorte();
    fs.aplicarCorte();
  }
  assert(cortes >= 15);
  // Tras el último corte, también
  EstadoPersistente est(fs);
  est.cargar();
  ColaEventos cola(fs, est, alIncidencia);
  assert(cola.iniciar());
  assert(!confirmados.empty() && cola.ultimoId() >= confirmados.back().idEvento);
}

}  // namespace

int main() {
  crc();
  estadoBasico();
  estadoCortePorCadaOperacion();
  estadoCaeEnLaCopiaAnterior();
  secuenciaNuncaSeRepite50Cortes();
  colaBasica();
  servidorConfirmaMasDeLoQueHay();
  registroIncompletoSeDescarta();
  escrituraFallidaNoDesalinea();
  compactacionCorteEnCadaOperacion();
  veinteCortesEnCobrosConsecutivos();
  puts("M3-01/02 almacenamiento: estado atómico (50 cortes), cola binaria, compactación y 20 cortes en cobros OK");
}
