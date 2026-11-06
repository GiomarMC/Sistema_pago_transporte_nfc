// Paquetes binarios del protocolo de sincronización v2 (contrato C1).
// Traducción exacta de servidor_api/transporte/sync_v2/paquetes.py: los bytes deben
// coincidir con los de Python (test/test_protocolo compara con docs/contratos/vectores_c1.json).
//
// Paquete = cabecera (20 B) | cuerpo (largo B) | firma, todo little-endian.
//   cabecera: 'S' 'P' | versión | tipo | origen(2) | destino(2) | secuencia(4) |
//             creado(4) | largo(2) | reservado(2)
//   firma:    origen != 0 (validador): HMAC-SHA256(K_val, cabecera||cuerpo)[0:16]
//             origen == 0 (servidor):  ECDSA P-256 r||s (64 B). La ESP32 solo la verifica.
//
// Sin String ni memoria dinámica: el llamador da los búferes (TAM_MAXIMO como mucho).
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace sp {

constexpr uint8_t VERSION = 1;
constexpr uint16_t SERVIDOR = 0;     // origen/destino del servidor
constexpr uint16_t TODOS = 0xFFFF;   // destino: todos los validadores
constexpr size_t TAM_CABECERA = 20;
constexpr size_t TAM_MAXIMO = 2048;  // cabecera + cuerpo + firma
constexpr size_t FIRMA_HMAC = 16;
constexpr size_t FIRMA_ECDSA = 64;
constexpr size_t TAM_CLAVE_HMAC = 32;
constexpr size_t TAM_CLAVE_PUBLICA = 65;  // punto sin comprimir: 0x04 | X | Y

// Tipos de paquete (C1 5.2). Los >= 0x80 los emite solo el servidor.
constexpr uint8_t EVENTOS = 0x01, HOLA = 0x02, ACUSE = 0x81, LISTAS = 0x82, TARIFAS = 0x83;

// Evento (C1 5.3)
constexpr uint8_t VIAJE = 1, INCIDENCIA = 2, FRAUDE = 3;
constexpr uint32_t SIN_CONTADOR = 0xFFFFFFFF;

// Entradas de LISTAS (C1 5.5)
constexpr uint8_t BLOQUEO = 1, RECARGA = 2;
constexpr uint8_t AGREGAR = 1, QUITAR = 2;       // op de BLOQUEO
constexpr uint8_t PENDIENTE = 1, APLICADA = 2;   // op de RECARGA

// Enlace del HOLA (C1 5.4)
constexpr uint8_t WIFI = 1, RED_4G = 2, BLE = 3, REENVIADO = 4;

constexpr size_t MAX_EVENTOS = 30;
constexpr size_t MAX_ENTRADAS = 120;
// Tope por tamaño: 20 + 8 + 81 × 24 + 64 = 2036 <= 2048
constexpr size_t MAX_TARIFAS = (TAM_MAXIMO - TAM_CABECERA - 8 - FIRMA_ECDSA) / 24;

struct Cabecera {
  uint8_t tipo;
  uint16_t origen;     // n.º de validador; 0 = servidor
  uint16_t destino;    // n.º de validador; 0 = servidor; 0xFFFF = todos
  uint32_t secuencia;  // creciente por emisor, nunca se repite
  uint32_t creado;     // unix (s) en el emisor
};

struct Evento {  // 64 B
  uint32_t idEvento;
  uint8_t tipo;
  uint8_t largoUid;  // 4 o 7 (0 si el evento no tiene tarjeta)
  uint16_t operacion;
  uint32_t fecha;
  uint32_t idTarjeta;
  uint8_t uid[10];
  int32_t monto;  // céntimos
  int32_t saldoFinal;
  uint32_t contador;
  uint32_t recargaHasta;
  char detalle[21];  // ASCII, 20 caracteres como mucho y terminado en '\0'
};

struct Eventos {
  uint8_t n;  // 1 a MAX_EVENTOS
  Evento eventos[MAX_EVENTOS];
};

struct Hola {  // 32 B
  uint32_t versionListas;
  uint32_t versionTarifas;
  uint32_t ultimoEvento;
  uint16_t pendientes;
  uint16_t ajenos;
  uint8_t enlace;
  int8_t senal;  // dBm/2; 0 = no disponible
  uint32_t bytesTxHoy;
  uint32_t bytesRxHoy;
  uint32_t versionFirmware;  // AAAAMMDD
};

struct Acuse {  // 8 B
  uint32_t hastaEvento;
  uint32_t horaServidor;
};

struct EntradaLista {  // 16 B
  uint8_t tipo;  // BLOQUEO o RECARGA
  uint8_t op;
  uint32_t a;  // BLOQUEO: id_tarjeta · RECARGA: id_cuenta
  uint32_t b;  // RECARGA: seq
  uint32_t c;  // RECARGA: monto en céntimos
};

struct Listas {
  uint32_t versionDesde;  // 0 = foto completa: borrar y reconstruir
  uint32_t versionHasta;
  bool urgente;
  uint8_t n;  // 0 a MAX_ENTRADAS
  EntradaLista entradas[MAX_ENTRADAS];
};

struct Tarifa {  // 24 B
  uint8_t codigo;
  uint32_t precio;  // céntimos
  char nombre[17];  // ASCII, 16 caracteres como mucho y terminado en '\0'
};

struct Tarifas {
  uint32_t version;  // unix de la última modificación de cualquier tarifa
  uint8_t n;
  Tarifa tarifas[MAX_TARIFAS];
};

// El cuerpo válido es el que indica cabecera.tipo. ~2.3 KB: mejor estático, no en la pila.
struct Paquete {
  Cabecera cabecera;
  union {
    Eventos eventos;
    Hola hola;
    Acuse acuse;
    Listas listas;
    Tarifas tarifas;
  };
};

// Motivo del rechazo, con los mismos nombres que PaqueteInvalido.motivo en Python
enum class Motivo : uint8_t { OK, MAGIA, VERSION, LARGO, TIPO, ORIGEN, VALIDADOR, FIRMA, CUERPO };
const char *describir(Motivo m);

// Devuelve la clave HMAC (32 B) del validador n, o nullptr si no se conoce.
using ClaveHmacDe = const uint8_t *(*)(uint16_t validador);

// Cabecera + cuerpo, sin firma. Devuelve los bytes escritos, o 0 si el paquete no es
// coherente (tipo desconocido, origen que no corresponde al tipo, n fuera de rango) o
// no cabe en `capacidad`.
size_t codificar(const Paquete &p, uint8_t *salida, size_t capacidad);

// Paquete completo de un validador (EVENTOS u HOLA) firmado con HMAC. 0 si hay error.
size_t empaquetar(const Paquete &p, const uint8_t claveHmac[TAM_CLAVE_HMAC], uint8_t *salida,
                  size_t capacidad);

// Valida magia, versión, largo, tipo, origen y firma, en ese orden, y recién entonces lee
// el cuerpo. Si devuelve false, `salida` queda a medias y no se debe usar.
bool desempaquetar(const uint8_t *datos, size_t largo, ClaveHmacDe claveHmacDe,
                   const uint8_t clavePublica[TAM_CLAVE_PUBLICA], Paquete &salida,
                   Motivo *motivo = nullptr);

// Tamaño total del primer paquete de `datos` según su cabecera (para separar paquetes
// concatenados). 0 si no hay cabecera completa, la magia no es 'SP' o el paquete se corta.
size_t medirPaquete(const uint8_t *datos, size_t largo);

// HMAC-SHA256 truncado a 16 B y verificación ECDSA P-256 sobre SHA-256 (mbedtls)
void firmaHmac(const uint8_t clave[TAM_CLAVE_HMAC], const uint8_t *datos, size_t largo,
               uint8_t firma[FIRMA_HMAC]);
bool ecdsaValida(const uint8_t clavePublica[TAM_CLAVE_PUBLICA], const uint8_t *datos,
                 size_t largo, const uint8_t firma[FIRMA_ECDSA]);

}  // namespace sp
