"""
Paquetes binarios del protocolo de sincronización v2 (contrato C1).

Formato fijo little-endian: cabecera de 20 B + cuerpo + firma. Los paquetes del
servidor (origen 0) van firmados con ECDSA P-256 (64 B, r||s) y los de un
validador con HMAC-SHA256 truncado a 16 B. La biblioteca C++ de la ESP32 (M0-02)
debe producir exactamente los mismos bytes: ver docs/contratos/vectores_c1.json.
"""

import hashlib
import hmac
import struct
from dataclasses import dataclass
from typing import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (decode_dss_signature,
                                                             encode_dss_signature)

MAGIA = b"SP"
VERSION = 1
SERVIDOR = 0          # origen/destino del servidor
TODOS = 0xFFFF        # destino: todos los validadores
TAM_MAXIMO = 2048     # cabecera + cuerpo + firma
FIRMA_HMAC = 16
FIRMA_ECDSA = 64

# Tipos de paquete (C1 5.2). Los >= 0x80 los emite solo el servidor.
EVENTOS, HOLA, ACUSE, LISTAS, TARIFAS = 0x01, 0x02, 0x81, 0x82, 0x83

# Evento (C1 5.3)
VIAJE, INCIDENCIA, FRAUDE = 1, 2, 3
SIN_CONTADOR = 0xFFFFFFFF

# Entradas de LISTAS (C1 5.5)
BLOQUEO, RECARGA = 1, 2
AGREGAR, QUITAR = 1, 2           # op de BLOQUEO
PENDIENTE, APLICADA = 1, 2       # op de RECARGA

# Enlace del HOLA (C1 5.4)
WIFI, RED_4G, BLE, REENVIADO = 1, 2, 3, 4

_CABECERA = struct.Struct("<2sBBHHIIHH")      # 20 B
_EVENTO = struct.Struct("<IBBHII10s2xiiII20s")  # 64 B
_EVENTOS = struct.Struct("<B3x")              # 4 B + n × evento
_HOLA = struct.Struct("<IIIHHBb2xIII")        # 32 B
_ACUSE = struct.Struct("<II")                 # 8 B
_LISTAS = struct.Struct("<IIHBx")             # 12 B + n × entrada
_ENTRADA = struct.Struct("<BB2xIII")          # 16 B
_TARIFAS = struct.Struct("<IB3x")             # 8 B + n × tarifa
_TARIFA = struct.Struct("<B3xI16s")           # 24 B

MAX_EVENTOS = 30
MAX_ENTRADAS = 120


class PaqueteInvalido(ValueError):
    """Paquete rechazado.

    motivo: magia, version, largo, tipo, origen, validador, firma o cuerpo.
    """

    def __init__(self, motivo: str, detalle: str = ""):
        super().__init__(f"{motivo}: {detalle}" if detalle else motivo)
        self.motivo = motivo


def _texto(campo: bytes) -> str:
    return campo.rstrip(b"\0").decode("ascii", "replace")


def _ascii(texto: str, largo: int) -> bytes:
    return texto.encode("ascii", "replace")[:largo]


def _contar(datos: bytes, fijo: struct.Struct, tam_item: int, n: int, maximo: int, minimo=0):
    """Comprueba que el cuerpo mida exactamente cabecera + n ítems."""
    if not minimo <= n <= maximo or len(datos) != fijo.size + n * tam_item:
        raise PaqueteInvalido("cuerpo", f"{n} elementos en {len(datos)} B")


# --- Cuerpos --------------------------------------------------------------------------

@dataclass(frozen=True)
class Evento:
    id_evento: int
    tipo: int
    operacion: int
    fecha: int
    id_tarjeta: int
    uid: bytes                      # 4 o 7 bytes (0 si el evento no tiene tarjeta)
    monto: int = 0                  # céntimos
    saldo_final: int = 0
    contador: int = SIN_CONTADOR
    recarga_hasta: int = 0
    detalle: str = ""               # ASCII, se trunca a 20 caracteres

    def a_bytes(self) -> bytes:
        if len(self.uid) > 10:
            raise ValueError("el UID tiene más de 10 bytes")
        return _EVENTO.pack(self.id_evento, self.tipo, len(self.uid), self.operacion, self.fecha,
                            self.id_tarjeta, self.uid, self.monto, self.saldo_final, self.contador,
                            self.recarga_hasta, _ascii(self.detalle, 20))

    @classmethod
    def de_bytes(cls, datos: bytes) -> "Evento":
        (id_evento, tipo, largo_uid, operacion, fecha, id_tarjeta, uid, monto, saldo_final,
         contador, recarga_hasta, detalle) = _EVENTO.unpack(datos)
        if largo_uid > 10:
            raise PaqueteInvalido("cuerpo", f"UID de {largo_uid} B")
        return cls(id_evento, tipo, operacion, fecha, id_tarjeta, uid[:largo_uid], monto,
                   saldo_final, contador, recarga_hasta, _texto(detalle))


@dataclass(frozen=True)
class Eventos:
    TIPO = EVENTOS
    eventos: tuple[Evento, ...]

    def __post_init__(self):
        object.__setattr__(self, "eventos", tuple(self.eventos))

    def a_bytes(self) -> bytes:
        if not 1 <= len(self.eventos) <= MAX_EVENTOS:
            raise ValueError(f"EVENTOS lleva de 1 a {MAX_EVENTOS} eventos")
        return _EVENTOS.pack(len(self.eventos)) + b"".join(e.a_bytes() for e in self.eventos)

    @classmethod
    def de_bytes(cls, datos: bytes) -> "Eventos":
        if len(datos) < _EVENTOS.size:
            raise PaqueteInvalido("cuerpo", f"EVENTOS de {len(datos)} B")
        (n,) = _EVENTOS.unpack_from(datos)
        _contar(datos, _EVENTOS, _EVENTO.size, n, MAX_EVENTOS, minimo=1)
        return cls(tuple(Evento.de_bytes(d) for d in _trozos(datos[_EVENTOS.size:], _EVENTO.size)))


@dataclass(frozen=True)
class Hola:
    TIPO = HOLA
    version_listas: int
    version_tarifas: int
    ultimo_evento: int
    pendientes: int
    ajenos: int
    enlace: int
    senal: int                      # dBm/2; 0 = no disponible
    bytes_tx_hoy: int
    bytes_rx_hoy: int
    version_firmware: int           # AAAAMMDD

    def a_bytes(self) -> bytes:
        return _HOLA.pack(self.version_listas, self.version_tarifas, self.ultimo_evento,
                          self.pendientes, self.ajenos, self.enlace, self.senal,
                          self.bytes_tx_hoy, self.bytes_rx_hoy, self.version_firmware)

    @classmethod
    def de_bytes(cls, datos: bytes) -> "Hola":
        if len(datos) != _HOLA.size:
            raise PaqueteInvalido("cuerpo", f"HOLA de {len(datos)} B")
        return cls(*_HOLA.unpack(datos))


@dataclass(frozen=True)
class Acuse:
    TIPO = ACUSE
    hasta_evento: int
    hora_servidor: int

    def a_bytes(self) -> bytes:
        return _ACUSE.pack(self.hasta_evento, self.hora_servidor)

    @classmethod
    def de_bytes(cls, datos: bytes) -> "Acuse":
        if len(datos) != _ACUSE.size:
            raise PaqueteInvalido("cuerpo", f"ACUSE de {len(datos)} B")
        return cls(*_ACUSE.unpack(datos))


@dataclass(frozen=True)
class EntradaLista:
    tipo: int                       # BLOQUEO o RECARGA
    op: int
    a: int                          # BLOQUEO: id_tarjeta · RECARGA: id_cuenta
    b: int = 0                      # RECARGA: seq
    c: int = 0                      # RECARGA: monto en céntimos

    def a_bytes(self) -> bytes:
        return _ENTRADA.pack(self.tipo, self.op, self.a, self.b, self.c)

    @classmethod
    def de_bytes(cls, datos: bytes) -> "EntradaLista":
        return cls(*_ENTRADA.unpack(datos))


@dataclass(frozen=True)
class Listas:
    TIPO = LISTAS
    version_desde: int              # 0 = foto completa: borrar y reconstruir
    version_hasta: int
    entradas: tuple[EntradaLista, ...] = ()
    urgente: bool = False

    def __post_init__(self):
        object.__setattr__(self, "entradas", tuple(self.entradas))

    def a_bytes(self) -> bytes:
        if len(self.entradas) > MAX_ENTRADAS:
            raise ValueError(f"LISTAS lleva como máximo {MAX_ENTRADAS} entradas")
        return (_LISTAS.pack(self.version_desde, self.version_hasta, len(self.entradas),
                             int(self.urgente))
                + b"".join(e.a_bytes() for e in self.entradas))

    @classmethod
    def de_bytes(cls, datos: bytes) -> "Listas":
        if len(datos) < _LISTAS.size:
            raise PaqueteInvalido("cuerpo", f"LISTAS de {len(datos)} B")
        desde, hasta, n, urgente = _LISTAS.unpack_from(datos)
        _contar(datos, _LISTAS, _ENTRADA.size, n, MAX_ENTRADAS)
        entradas = tuple(EntradaLista.de_bytes(d)
                         for d in _trozos(datos[_LISTAS.size:], _ENTRADA.size))
        return cls(desde, hasta, entradas, bool(urgente))


@dataclass(frozen=True)
class Tarifa:
    codigo: int
    precio: int                     # céntimos
    nombre: str                     # ASCII, se trunca a 16 caracteres

    def a_bytes(self) -> bytes:
        return _TARIFA.pack(self.codigo, self.precio, _ascii(self.nombre, 16))

    @classmethod
    def de_bytes(cls, datos: bytes) -> "Tarifa":
        codigo, precio, nombre = _TARIFA.unpack(datos)
        return cls(codigo, precio, _texto(nombre))


@dataclass(frozen=True)
class Tarifas:
    TIPO = TARIFAS
    version: int                    # unix de la última modificación de cualquier tarifa
    tarifas: tuple[Tarifa, ...]

    def __post_init__(self):
        object.__setattr__(self, "tarifas", tuple(self.tarifas))

    def a_bytes(self) -> bytes:
        if len(self.tarifas) > 255:
            raise ValueError("TARIFAS lleva como máximo 255 tarifas")
        return (_TARIFAS.pack(self.version, len(self.tarifas))
                + b"".join(t.a_bytes() for t in self.tarifas))

    @classmethod
    def de_bytes(cls, datos: bytes) -> "Tarifas":
        if len(datos) < _TARIFAS.size:
            raise PaqueteInvalido("cuerpo", f"TARIFAS de {len(datos)} B")
        version, n = _TARIFAS.unpack_from(datos)
        _contar(datos, _TARIFAS, _TARIFA.size, n, 255)
        return cls(version, tuple(Tarifa.de_bytes(d)
                                  for d in _trozos(datos[_TARIFAS.size:], _TARIFA.size)))


CUERPOS = {c.TIPO: c for c in (Eventos, Hola, Acuse, Listas, Tarifas)}
Cuerpo = Eventos | Hola | Acuse | Listas | Tarifas


def _trozos(datos: bytes, tam: int):
    return (datos[i:i + tam] for i in range(0, len(datos), tam))


# --- Cabecera y paquete ---------------------------------------------------------------

@dataclass(frozen=True)
class Cabecera:
    """Cabecera común (C1 5.1). magia, versión y largo se ponen al empaquetar."""
    tipo: int
    origen: int                     # n.º de validador; 0 = servidor
    destino: int                    # n.º de validador; 0 = servidor; 0xFFFF = todos
    secuencia: int                  # creciente por emisor, nunca se repite
    creado: int                     # unix (s) en el emisor

    def a_bytes(self, largo: int) -> bytes:
        return _CABECERA.pack(MAGIA, VERSION, self.tipo, self.origen, self.destino,
                              self.secuencia, self.creado, largo, 0)


@dataclass(frozen=True)
class Paquete:
    cabecera: Cabecera
    cuerpo: Cuerpo


def _tam_firma(origen: int) -> int:
    return FIRMA_ECDSA if origen == SERVIDOR else FIRMA_HMAC


def _firma_hmac(clave: bytes, datos: bytes) -> bytes:
    return hmac.new(clave, datos, hashlib.sha256).digest()[:FIRMA_HMAC]


def _firma_ecdsa(clave: ec.EllipticCurvePrivateKey, datos: bytes) -> bytes:
    # Determinista (RFC 6979): los mismos datos dan la misma firma, así los vectores
    # de prueba se pueden comparar byte a byte.
    der = clave.sign(datos, ec.ECDSA(hashes.SHA256(), deterministic_signing=True))
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _ecdsa_valida(clave: ec.EllipticCurvePublicKey, datos: bytes, firma: bytes) -> bool:
    der = encode_dss_signature(int.from_bytes(firma[:32], "big"), int.from_bytes(firma[32:], "big"))
    try:
        clave.verify(der, datos, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


def _origen_valido(tipo: int, origen: int) -> bool:
    """Los tipos >= 0x80 solo los emite el servidor; los demás, solo un validador."""
    return (origen == SERVIDOR) == (tipo >= 0x80)


def empaquetar(cabecera: Cabecera, cuerpo: Cuerpo, clave_hmac: bytes | None = None,
               clave_ecdsa: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """Cabecera + cuerpo + firma. Un validador firma con clave_hmac; el servidor con clave_ecdsa."""
    if cabecera.tipo != cuerpo.TIPO:
        raise ValueError(f"la cabecera dice tipo {cabecera.tipo:#04x} y el cuerpo es "
                         f"{type(cuerpo).__name__}")
    if not _origen_valido(cabecera.tipo, cabecera.origen):
        raise ValueError(f"el tipo {cabecera.tipo:#04x} no puede tener origen {cabecera.origen}")
    datos = cuerpo.a_bytes()
    firmado = cabecera.a_bytes(len(datos)) + datos
    if cabecera.origen == SERVIDOR:
        if clave_ecdsa is None:
            raise ValueError("un paquete del servidor necesita clave_ecdsa")
        paquete = firmado + _firma_ecdsa(clave_ecdsa, firmado)
    else:
        if clave_hmac is None:
            raise ValueError("un paquete de validador necesita clave_hmac")
        paquete = firmado + _firma_hmac(clave_hmac, firmado)
    if len(paquete) > TAM_MAXIMO:
        raise ValueError(f"el paquete mide {len(paquete)} B (máximo {TAM_MAXIMO})")
    return paquete


def desempaquetar(datos: bytes, clave_hmac_de: Callable[[int], bytes | None],
                  clave_publica: ec.EllipticCurvePublicKey | None) -> Paquete:
    """
    Valida magia, versión, largo, tipo, origen y firma, en ese orden, y recién entonces
    lee el cuerpo. clave_hmac_de(n) devuelve la clave del validador n (None si no existe).
    Lanza PaqueteInvalido; nunca devuelve un paquete a medias.
    """
    if len(datos) < _CABECERA.size:
        raise PaqueteInvalido("largo", f"{len(datos)} B no alcanzan para la cabecera")
    magia, version, tipo, origen, destino, secuencia, creado, largo, _ = \
        _CABECERA.unpack_from(datos)
    if magia != MAGIA:
        raise PaqueteInvalido("magia", magia.hex())
    if version != VERSION:
        raise PaqueteInvalido("version", str(version))
    total = _CABECERA.size + largo + _tam_firma(origen)
    if total > TAM_MAXIMO or len(datos) != total:
        raise PaqueteInvalido("largo", f"la cabecera indica {total} B y llegaron {len(datos)}")
    if tipo not in CUERPOS:
        raise PaqueteInvalido("tipo", f"{tipo:#04x}")
    if not _origen_valido(tipo, origen):
        raise PaqueteInvalido("origen", f"tipo {tipo:#04x} desde {origen}")

    firmado, firma = datos[:_CABECERA.size + largo], datos[_CABECERA.size + largo:]
    if origen == SERVIDOR:
        if clave_publica is None or not _ecdsa_valida(clave_publica, firmado, firma):
            raise PaqueteInvalido("firma", "ECDSA del servidor")
    else:
        clave = clave_hmac_de(origen)
        if clave is None:
            raise PaqueteInvalido("validador", f"n.º {origen} desconocido")
        if not hmac.compare_digest(_firma_hmac(clave, firmado), firma):
            raise PaqueteInvalido("firma", f"HMAC del validador {origen}")

    cuerpo = CUERPOS[tipo].de_bytes(firmado[_CABECERA.size:])
    return Paquete(Cabecera(tipo, origen, destino, secuencia, creado), cuerpo)


def separar(flujo: bytes) -> list[bytes]:
    """Parte los paquetes concatenados de un cuerpo HTTP (/api/v2/paquetes/) según su largo."""
    paquetes, i = [], 0
    while i < len(flujo):
        if len(flujo) - i < _CABECERA.size:
            raise PaqueteInvalido("largo", f"sobran {len(flujo) - i} B al final")
        magia, _, _, origen, _, _, _, largo, _ = _CABECERA.unpack_from(flujo, i)
        if magia != MAGIA:
            raise PaqueteInvalido("magia", f"en el byte {i}")
        fin = i + _CABECERA.size + largo + _tam_firma(origen)
        if fin > len(flujo):
            raise PaqueteInvalido("largo", f"el paquete del byte {i} se corta")
        paquetes.append(flujo[i:fin])
        i = fin
    return paquetes
