#include "ProtocoloSP.h"

#include <mbedtls/md.h>
#include <string.h>
#include <uECC.h>

namespace sp {

namespace {

constexpr size_t TAM_EVENTO = 64, TAM_HOLA = 32, TAM_ACUSE = 8;
constexpr size_t TAM_LISTAS = 12, TAM_ENTRADA = 16, TAM_TARIFAS = 8, TAM_TARIFA = 24;

// --- Enteros little-endian -----------------------------------------------------------

void pon16(uint8_t *p, uint16_t v) {
  p[0] = v;
  p[1] = v >> 8;
}

void pon32(uint8_t *p, uint32_t v) {
  for (int i = 0; i < 4; i++) p[i] = v >> (8 * i);
}

uint16_t lee16(const uint8_t *p) { return p[0] | (uint16_t(p[1]) << 8); }

uint32_t lee32(const uint8_t *p) {
  return p[0] | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

// Texto ASCII en un campo fijo, con relleno de ceros
void ponTexto(uint8_t *p, const char *texto, size_t tam) {
  memset(p, 0, tam);
  for (size_t i = 0; i < tam && texto[i]; i++) p[i] = texto[i];
}

void leeTexto(const uint8_t *p, size_t tam, char *texto) {
  memcpy(texto, p, tam);
  texto[tam] = '\0';
}

size_t tamFirma(uint16_t origen) { return origen == SERVIDOR ? FIRMA_ECDSA : FIRMA_HMAC; }

// Los tipos >= 0x80 solo los emite el servidor; los demás, solo un validador
bool origenValido(uint8_t tipo, uint16_t origen) { return (origen == SERVIDOR) == (tipo >= 0x80); }

bool tipoConocido(uint8_t tipo) {
  return tipo == EVENTOS || tipo == HOLA || tipo == ACUSE || tipo == LISTAS || tipo == TARIFAS;
}

bool igualesTiempoConstante(const uint8_t *a, const uint8_t *b, size_t n) {
  uint8_t dif = 0;
  for (size_t i = 0; i < n; i++) dif |= a[i] ^ b[i];
  return dif == 0;
}

// --- Cuerpos --------------------------------------------------------------------------

void codificarEvento(const Evento &e, uint8_t *p) {
  memset(p, 0, TAM_EVENTO);
  pon32(p, e.idEvento);
  p[4] = e.tipo;
  p[5] = e.largoUid;
  pon16(p + 6, e.operacion);
  pon32(p + 8, e.fecha);
  pon32(p + 12, e.idTarjeta);
  memcpy(p + 16, e.uid, e.largoUid);
  pon32(p + 28, e.monto);
  pon32(p + 32, e.saldoFinal);
  pon32(p + 36, e.contador);
  pon32(p + 40, e.recargaHasta);
  ponTexto(p + 44, e.detalle, 20);
}

bool decodificarEvento(const uint8_t *p, Evento &e) {
  e.idEvento = lee32(p);
  e.tipo = p[4];
  e.largoUid = p[5];
  if (e.largoUid > sizeof(e.uid)) return false;
  e.operacion = lee16(p + 6);
  e.fecha = lee32(p + 8);
  e.idTarjeta = lee32(p + 12);
  memset(e.uid, 0, sizeof(e.uid));
  memcpy(e.uid, p + 16, e.largoUid);
  e.monto = int32_t(lee32(p + 28));
  e.saldoFinal = int32_t(lee32(p + 32));
  e.contador = lee32(p + 36);
  e.recargaHasta = lee32(p + 40);
  leeTexto(p + 44, 20, e.detalle);
  return true;
}

// Largo del cuerpo, o 0 si no es válido (n fuera de rango, UID demasiado largo)
size_t tamCuerpo(const Paquete &paq) {
  switch (paq.cabecera.tipo) {
    case EVENTOS: {
      const Eventos &c = paq.eventos;
      if (c.n < 1 || c.n > MAX_EVENTOS) return 0;
      for (size_t i = 0; i < c.n; i++)
        if (c.eventos[i].largoUid > sizeof(c.eventos[i].uid)) return 0;
      return 4 + c.n * TAM_EVENTO;
    }
    case HOLA: return TAM_HOLA;
    case ACUSE: return TAM_ACUSE;
    case LISTAS: return paq.listas.n <= MAX_ENTRADAS ? TAM_LISTAS + paq.listas.n * TAM_ENTRADA : 0;
    case TARIFAS:
      return paq.tarifas.n <= MAX_TARIFAS ? TAM_TARIFAS + paq.tarifas.n * TAM_TARIFA : 0;
  }
  return 0;
}

// Escribe el cuerpo (ya validado con tamCuerpo) en p
void codificarCuerpo(const Paquete &paq, uint8_t *p) {
  switch (paq.cabecera.tipo) {
    case EVENTOS: {
      const Eventos &c = paq.eventos;
      p[0] = c.n;
      p[1] = p[2] = p[3] = 0;
      for (size_t i = 0; i < c.n; i++) codificarEvento(c.eventos[i], p + 4 + i * TAM_EVENTO);
      return;
    }
    case HOLA: {
      const Hola &c = paq.hola;
      pon32(p, c.versionListas);
      pon32(p + 4, c.versionTarifas);
      pon32(p + 8, c.ultimoEvento);
      pon16(p + 12, c.pendientes);
      pon16(p + 14, c.ajenos);
      p[16] = c.enlace;
      p[17] = uint8_t(c.senal);
      p[18] = p[19] = 0;
      pon32(p + 20, c.bytesTxHoy);
      pon32(p + 24, c.bytesRxHoy);
      pon32(p + 28, c.versionFirmware);
      return;
    }
    case ACUSE:
      pon32(p, paq.acuse.hastaEvento);
      pon32(p + 4, paq.acuse.horaServidor);
      return;
    case LISTAS: {
      const Listas &c = paq.listas;
      pon32(p, c.versionDesde);
      pon32(p + 4, c.versionHasta);
      pon16(p + 8, c.n);
      p[10] = c.urgente ? 1 : 0;
      p[11] = 0;
      for (size_t i = 0; i < c.n; i++) {
        uint8_t *q = p + TAM_LISTAS + i * TAM_ENTRADA;
        q[0] = c.entradas[i].tipo;
        q[1] = c.entradas[i].op;
        q[2] = q[3] = 0;
        pon32(q + 4, c.entradas[i].a);
        pon32(q + 8, c.entradas[i].b);
        pon32(q + 12, c.entradas[i].c);
      }
      return;
    }
    case TARIFAS: {
      const Tarifas &c = paq.tarifas;
      pon32(p, c.version);
      p[4] = c.n;
      p[5] = p[6] = p[7] = 0;
      for (size_t i = 0; i < c.n; i++) {
        uint8_t *q = p + TAM_TARIFAS + i * TAM_TARIFA;
        q[0] = c.tarifas[i].codigo;
        q[1] = q[2] = q[3] = 0;
        pon32(q + 4, c.tarifas[i].precio);
        ponTexto(q + 8, c.tarifas[i].nombre, 16);
      }
      return;
    }
  }
}

bool decodificarCuerpo(uint8_t tipo, const uint8_t *p, size_t largo, Paquete &paq) {
  switch (tipo) {
    case EVENTOS: {
      if (largo < 4) return false;
      Eventos &c = paq.eventos;
      c.n = p[0];
      if (c.n < 1 || c.n > MAX_EVENTOS || largo != 4 + c.n * TAM_EVENTO) return false;
      for (size_t i = 0; i < c.n; i++)
        if (!decodificarEvento(p + 4 + i * TAM_EVENTO, c.eventos[i])) return false;
      return true;
    }
    case HOLA: {
      if (largo != TAM_HOLA) return false;
      Hola &c = paq.hola;
      c.versionListas = lee32(p);
      c.versionTarifas = lee32(p + 4);
      c.ultimoEvento = lee32(p + 8);
      c.pendientes = lee16(p + 12);
      c.ajenos = lee16(p + 14);
      c.enlace = p[16];
      c.senal = int8_t(p[17]);
      c.bytesTxHoy = lee32(p + 20);
      c.bytesRxHoy = lee32(p + 24);
      c.versionFirmware = lee32(p + 28);
      return true;
    }
    case ACUSE:
      if (largo != TAM_ACUSE) return false;
      paq.acuse.hastaEvento = lee32(p);
      paq.acuse.horaServidor = lee32(p + 4);
      return true;
    case LISTAS: {
      if (largo < TAM_LISTAS) return false;
      Listas &c = paq.listas;
      uint16_t n = lee16(p + 8);
      if (n > MAX_ENTRADAS || largo != TAM_LISTAS + n * TAM_ENTRADA) return false;
      c.versionDesde = lee32(p);
      c.versionHasta = lee32(p + 4);
      c.n = n;
      c.urgente = p[10] != 0;
      for (size_t i = 0; i < n; i++) {
        const uint8_t *q = p + TAM_LISTAS + i * TAM_ENTRADA;
        c.entradas[i] = {q[0], q[1], lee32(q + 4), lee32(q + 8), lee32(q + 12)};
      }
      return true;
    }
    case TARIFAS: {
      if (largo < TAM_TARIFAS) return false;
      Tarifas &c = paq.tarifas;
      c.n = p[4];
      if (c.n > MAX_TARIFAS || largo != TAM_TARIFAS + c.n * TAM_TARIFA) return false;
      c.version = lee32(p);
      for (size_t i = 0; i < c.n; i++) {
        const uint8_t *q = p + TAM_TARIFAS + i * TAM_TARIFA;
        c.tarifas[i].codigo = q[0];
        c.tarifas[i].precio = lee32(q + 4);
        leeTexto(q + 8, 16, c.tarifas[i].nombre);
      }
      return true;
    }
  }
  return false;
}

// Clave pública del servidor ya comprobada (que el punto esté en la curva)
struct ClaveEcdsa {
  bool lista = false;
  uint8_t bytes[TAM_CLAVE_PUBLICA];
};
ClaveEcdsa clave;

bool cargarClave(const uint8_t publica[TAM_CLAVE_PUBLICA]) {
  if (clave.lista && memcmp(clave.bytes, publica, TAM_CLAVE_PUBLICA) == 0) return true;
  // micro-ecc usa el punto sin el prefijo 0x04: solo X | Y
  clave.lista = publica[0] == 0x04 && uECC_valid_public_key(publica + 1, uECC_secp256r1());
  if (clave.lista) memcpy(clave.bytes, publica, TAM_CLAVE_PUBLICA);
  return clave.lista;
}

}  // namespace

const char *describir(Motivo m) {
  switch (m) {
    case Motivo::OK: return "ok";
    case Motivo::MAGIA: return "magia";
    case Motivo::VERSION: return "version";
    case Motivo::LARGO: return "largo";
    case Motivo::TIPO: return "tipo";
    case Motivo::ORIGEN: return "origen";
    case Motivo::VALIDADOR: return "validador";
    case Motivo::FIRMA: return "firma";
    case Motivo::CUERPO: return "cuerpo";
  }
  return "?";
}

void firmaHmac(const uint8_t clave[TAM_CLAVE_HMAC], const uint8_t *datos, size_t largo,
               uint8_t firma[FIRMA_HMAC]) {
  uint8_t completa[32];
  mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), clave, TAM_CLAVE_HMAC, datos,
                  largo, completa);
  memcpy(firma, completa, FIRMA_HMAC);
}

// Con micro-ecc (lib/micro-ecc, a -O3) en vez de mbedTLS: ~67 ms frente a ~290 ms en la
// ESP32 clásica, que no acelera curvas elípticas.
bool ecdsaValida(const uint8_t clavePublica[TAM_CLAVE_PUBLICA], const uint8_t *datos,
                 size_t largo, const uint8_t firma[FIRMA_ECDSA]) {
  if (!cargarClave(clavePublica)) return false;
  uint8_t resumen[32];
  mbedtls_md(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), datos, largo, resumen);
  // La firma ya viene como r | s, el formato que espera micro-ecc
  return uECC_verify(clave.bytes + 1, resumen, sizeof(resumen), firma, uECC_secp256r1()) == 1;
}

size_t codificar(const Paquete &p, uint8_t *salida, size_t capacidad) {
  const Cabecera &c = p.cabecera;
  if (!tipoConocido(c.tipo) || !origenValido(c.tipo, c.origen)) return 0;
  size_t largo = tamCuerpo(p);
  if (largo == 0 || TAM_CABECERA + largo + tamFirma(c.origen) > TAM_MAXIMO ||
      TAM_CABECERA + largo > capacidad)
    return 0;
  salida[0] = 'S';
  salida[1] = 'P';
  salida[2] = VERSION;
  salida[3] = c.tipo;
  pon16(salida + 4, c.origen);
  pon16(salida + 6, c.destino);
  pon32(salida + 8, c.secuencia);
  pon32(salida + 12, c.creado);
  pon16(salida + 16, largo);
  pon16(salida + 18, 0);
  codificarCuerpo(p, salida + TAM_CABECERA);
  return TAM_CABECERA + largo;
}

size_t empaquetar(const Paquete &p, const uint8_t claveHmac[TAM_CLAVE_HMAC], uint8_t *salida,
                  size_t capacidad) {
  if (p.cabecera.origen == SERVIDOR) return 0;  // la ESP32 no firma con ECDSA
  size_t largo = codificar(p, salida, capacidad);
  if (largo == 0 || largo + FIRMA_HMAC > capacidad) return 0;
  firmaHmac(claveHmac, salida, largo, salida + largo);
  return largo + FIRMA_HMAC;
}

bool desempaquetar(const uint8_t *datos, size_t largo, ClaveHmacDe claveHmacDe,
                   const uint8_t clavePublica[TAM_CLAVE_PUBLICA], Paquete &salida,
                   Motivo *motivo) {
  Motivo m = Motivo::OK;
  uint8_t tipo = 0;
  uint16_t origen = 0, largoCuerpo = 0;
  if (largo < TAM_CABECERA) {
    m = Motivo::LARGO;
  } else if (datos[0] != 'S' || datos[1] != 'P') {
    m = Motivo::MAGIA;
  } else if (datos[2] != VERSION) {
    m = Motivo::VERSION;
  } else {
    tipo = datos[3];
    origen = lee16(datos + 4);
    largoCuerpo = lee16(datos + 16);
    size_t total = TAM_CABECERA + largoCuerpo + tamFirma(origen);
    if (total > TAM_MAXIMO || largo != total) {
      m = Motivo::LARGO;
    } else if (!tipoConocido(tipo)) {
      m = Motivo::TIPO;
    } else if (!origenValido(tipo, origen)) {
      m = Motivo::ORIGEN;
    }
  }

  const size_t firmado = TAM_CABECERA + largoCuerpo;
  if (m == Motivo::OK) {
    if (origen == SERVIDOR) {
      if (clavePublica == nullptr || !ecdsaValida(clavePublica, datos, firmado, datos + firmado))
        m = Motivo::FIRMA;
    } else {
      const uint8_t *claveHmac = claveHmacDe ? claveHmacDe(origen) : nullptr;
      uint8_t esperada[FIRMA_HMAC];
      if (claveHmac == nullptr) {
        m = Motivo::VALIDADOR;
      } else {
        firmaHmac(claveHmac, datos, firmado, esperada);
        if (!igualesTiempoConstante(esperada, datos + firmado, FIRMA_HMAC)) m = Motivo::FIRMA;
      }
    }
  }

  if (m == Motivo::OK) {
    salida.cabecera = {tipo, origen, lee16(datos + 6), lee32(datos + 8), lee32(datos + 12)};
    if (!decodificarCuerpo(tipo, datos + TAM_CABECERA, largoCuerpo, salida)) m = Motivo::CUERPO;
  }
  if (motivo) *motivo = m;
  return m == Motivo::OK;
}

size_t medirPaquete(const uint8_t *datos, size_t largo) {
  if (largo < TAM_CABECERA || datos[0] != 'S' || datos[1] != 'P') return 0;
  size_t total = TAM_CABECERA + lee16(datos + 16) + tamFirma(lee16(datos + 4));
  return total <= largo ? total : 0;
}

}  // namespace sp
