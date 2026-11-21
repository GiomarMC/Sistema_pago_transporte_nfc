// M4-04: el SincronizadorV2 del firmware (compilado en el ordenador) habla por HTTP con el
// servidor C1 falso en Python. Comprueba que los paquetes que firma el validador los acepta
// el módulo del servidor, y que los que firma el servidor los verifica el validador.
//
//   e2e_servidor_falso <puerto> <clave pública ECDSA del servidor en hex (65 B)>
// Lo lanza test_host/e2e_servidor_falso.sh, que levanta antes el servidor.
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <deque>
#include <string>
#include <vector>

#include "sincronizador_v2.h"

namespace {

// Claves de prueba que el script pasa también al servidor (--clave 107=...)
uint8_t claveHmac[32];
uint8_t clavePublica[65];

uint32_t ahoraReal() { return (uint32_t)time(nullptr); }

// Canal que manda cada paquete con un POST (usa curl) y guarda los paquetes de la respuesta
class CanalHttp : public CanalPaquetes {
 public:
  explicit CanalHttp(std::string url) : url_(std::move(url)) {}
  const char *nombre() const override { return "http-falso"; }
  uint8_t codigoEnlace() const override { return 1; }
  bool listo() const override { return true; }
  void atender() override {}

  bool enviar(const uint8_t *paquete, size_t largo) override {
    FILE *f = fopen("/tmp/subepe-e2e-tx.bin", "wb");
    if (!f) return false;
    fwrite(paquete, 1, largo, f);
    fclose(f);
    const std::string orden = "curl -s --max-time 5 -H 'Content-Type: application/octet-stream' "
                              "--data-binary @/tmp/subepe-e2e-tx.bin -o /tmp/subepe-e2e-rx.bin -w '%{http_code}' " + url_;
    FILE *p = popen(orden.c_str(), "r");
    if (!p) return false;
    char codigo[16] = {};
    fread(codigo, 1, sizeof(codigo) - 1, p);
    pclose(p);
    if (strcmp(codigo, "200") != 0) {
      fprintf(stderr, "el servidor contestó HTTP %s\n", codigo);
      return false;
    }
    // La respuesta trae cero o más paquetes concatenados
    f = fopen("/tmp/subepe-e2e-rx.bin", "rb");
    if (!f) return true;
    std::vector<uint8_t> resp;
    uint8_t buf[2048];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0) resp.insert(resp.end(), buf, buf + n);
    fclose(f);
    for (size_t i = 0; i < resp.size();) {
      const size_t largoPaquete = sp::medirPaquete(resp.data() + i, resp.size() - i);
      if (largoPaquete == 0) break;
      recibidos_.emplace_back(resp.begin() + i, resp.begin() + i + largoPaquete);
      i += largoPaquete;
    }
    return true;
  }

  size_t recibir(uint8_t *buf, size_t max) override {
    if (recibidos_.empty()) return 0;
    auto p = std::move(recibidos_.front());
    recibidos_.pop_front();
    if (p.size() > max) return 0;
    memcpy(buf, p.data(), p.size());
    return p.size();
  }

 private:
  std::string url_;
  std::deque<std::vector<uint8_t>> recibidos_;
};

class AlmacenEnMemoria : public AlmacenSync {
 public:
  uint32_t ultimoEvento() const override { return ultimo_; }
  uint32_t ultimoAcusado() const override { return acusado_; }
  size_t eventosDesde(uint32_t desde, sp::Evento *salida, size_t max) override {
    size_t n = 0;
    for (uint32_t id = desde + 1; id <= ultimo_ && n < max; id++) {
      sp::Evento e = {};
      e.idEvento = id;
      e.tipo = sp::VIAJE;
      e.largoUid = 4;
      e.operacion = id;
      e.fecha = 1795000000 + id;
      e.idTarjeta = 900 + id;
      e.monto = -130;
      e.saldoFinal = 5000 - 130 * (int32_t)id;
      e.contador = id;
      snprintf(e.detalle, sizeof(e.detalle), "viaje %u", (unsigned)id);
      salida[n++] = e;
    }
    return n;
  }
  void acusarHasta(uint32_t hasta) override { acusado_ = hasta; }
  uint32_t versionListas() const override { return 0; }   // sin nada aplicado: el servidor debe mandar todo
  uint32_t versionTarifas() const override { return 0; }
  uint32_t siguienteSecuencia() override { return ++secuencia_; }
  size_t ajenos() const override { return 0; }
  size_t leerAjeno(size_t, uint8_t *, size_t) override { return 0; }
  void ajenosAcusados(uint16_t, uint32_t) override {}
  void aplicarListas(const sp::Listas &l) override { listas = l; listasRecibidas++; }
  void aplicarTarifas(const sp::Tarifas &t) override { tarifas = t; tarifasRecibidas++; }

  uint32_t ultimo_ = 0, acusado_ = 0, secuencia_ = 0;
  uint32_t listasRecibidas = 0, tarifasRecibidas = 0;
  sp::Listas listas = {};
  sp::Tarifas tarifas = {};
};

void deHex(const char *hex, uint8_t *salida, size_t n) {
  assert(strlen(hex) == 2 * n);
  for (size_t i = 0; i < n; i++) {
    unsigned v;
    sscanf(hex + 2 * i, "%2x", &v);
    salida[i] = v;
  }
}

ConfigSyncV2 config(const uint8_t *publica) {
  ConfigSyncV2 c;
  c.validador = 107;
  c.claveHmac = claveHmac;
  c.clavePublicaServidor = publica;
  c.versionFirmware = 20261120;
  c.ahoraUnix = ahoraReal;
  return c;
}

}  // namespace

int main(int argc, char **argv) {
  if (argc != 3) {
    fprintf(stderr, "uso: %s <puerto> <clave pública en hex>\n", argv[0]);
    return 2;
  }
  for (int i = 0; i < 32; i++) claveHmac[i] = (0x10 + i) & 0xFF;
  deHex(argv[2], clavePublica, sizeof(clavePublica));
  CanalHttp canal(std::string("http://127.0.0.1:") + argv[1] + "/api/v2/paquetes/");

  // 1) Un validador con 100 viajes sin subir se sincroniza con el servidor falso
  {
    AlmacenEnMemoria alm;
    alm.ultimo_ = 100;
    SincronizadorV2 s(alm, config(clavePublica));
    uint32_t ahora = 1000;
    for (int vuelta = 0; vuelta < 100 && !(alm.acusado_ == 100 && alm.listasRecibidas && alm.tarifasRecibidas); vuelta++) {
      s.atender(&canal, ahora);
      ahora += 200;
    }
    assert(s.rechazados() == 0);                         // el validador aceptó todas las firmas del servidor
    assert(alm.acusado_ == 100 && s.pendientes() == 0);  // y el servidor aceptó todos los eventos del validador
    assert(s.paquetesEventos() == 4 && s.holasEnviados() == 1);
    assert(alm.listasRecibidas == 1 && alm.listas.versionDesde == 0 && alm.listas.versionHasta == 2 && alm.listas.n == 2);
    assert(alm.listas.entradas[0].tipo == sp::BLOQUEO && alm.listas.entradas[0].a == 4242 && alm.listas.entradas[1].a == 77);
    assert(alm.tarifasRecibidas == 1 && alm.tarifas.n == 2 && alm.tarifas.tarifas[0].precio == 130);
    assert(strcmp(alm.tarifas.tarifas[1].nombre, "estudiante") == 0);
    const int64_t dif = (int64_t)s.horaActual(ahora) - (int64_t)ahoraReal();
    assert(dif >= -3 && dif <= 3);  // la hora del servidor, a ±3 s de la real
    printf("e2e 1: 100 eventos en %u paquetes, acuse hasta 100, %u bloqueos, %u tarifas, hora a %lld s\n",
           (unsigned)s.paquetesEventos(), (unsigned)alm.listas.n, (unsigned)alm.tarifas.n, (long long)dif);
  }

  // 2) Con otra clave pública (un servidor que no es el esperado) el validador no acepta nada
  {
    uint8_t ajena[65];
    memcpy(ajena, clavePublica, 65);
    ajena[20] ^= 0x01;
    AlmacenEnMemoria alm;
    alm.ultimo_ = 10;
    SincronizadorV2 s(alm, config(ajena));
    for (uint32_t ahora = 1000, v = 0; v < 5; v++, ahora += 200) s.atender(&canal, ahora);
    assert(s.rechazados() > 0 && alm.acusado_ == 0 && alm.listasRecibidas == 0 && s.horaServidor() == 0);
    printf("e2e 2: servidor con otra clave: %u paquetes rechazados, nada confirmado\n", (unsigned)s.rechazados());
  }
  puts("E2E servidor C1 falso: el firmware y el módulo Python del servidor se entienden OK");
  return 0;
}
