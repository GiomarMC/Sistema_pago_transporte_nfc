"""
Formato de los datos del sistema de transporte en la tarjeta (versión 2).

Independiente del tipo de tarjeta: aquí solo se convierte entre objetos Python
y bytes firmados. La lectura/escritura física está en ntag215.py.

Mapa de memoria en NTAG215 (página = 4 bytes, enteros en big-endian):

  Pág 8      'T' 'R' | versión (1) | estado (1: activa, 2: bloqueada)
  Pág 9      id_tarjeta (uint32)
  Pág 10     id_cuenta  (uint32)
  Pág 11     tarifa (1) | reservado (1) | fecha de emisión (uint16, días desde 2020-01-01)
  Pág 12-13  firma de la cabecera (8 bytes)
  Pág 14-15  reservado
  Pág 16-20  registro A ┐ saldo en céntimos (int32) | nº operación (uint16) |
  Pág 21-25  registro B ┘ fecha-hora (uint32, unix) | validador (uint16) |
                          última recarga aplicada (uint32) | firma (4)

La "última recarga aplicada" va dentro del registro firmado para que saldo y
recarga se graben a la vez: una escritura cortada no puede dejar una recarga
sumada pero sin marcar (se aplicaría dos veces) ni al revés.

Se escribe siempre en el registro que NO está vigente, así un corte a mitad de
escritura deja intacto el anterior.
"""

import hashlib
import hmac
import struct
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

MARCA = b"TR"
VERSION = 2
ACTIVA, BLOQUEADA = 1, 2
ESTADOS = {ACTIVA: "activa", BLOQUEADA: "bloqueada"}

PAG_INICIO = 8           # primera página del área del sistema (y de la protección)
PAG_CABECERA = 8         # 6 páginas: 8-13
PAG_REGISTROS = (16, 21)  # 5 páginas cada uno
TAM_REGISTRO = 20
PAG_FIN = 25
TAM_AREA = (PAG_FIN - PAG_INICIO + 1) * 4  # 72 bytes

EPOCA = date(2020, 1, 1)


class ErrorFormato(Exception):
    pass


def _firma(clave: bytes, *partes: bytes, largo: int) -> bytes:
    return hmac.new(clave, b"".join(partes), hashlib.sha256).digest()[:largo]


@dataclass
class Cabecera:
    id_tarjeta: int
    id_cuenta: int
    tarifa: int
    emision: date
    estado: int = ACTIVA
    version: int = VERSION

    def a_bytes(self, uid: bytes, clave: bytes) -> bytes:
        dias = (self.emision - EPOCA).days
        cuerpo = (MARCA + struct.pack(">BBIIBBH", self.version, self.estado,
                                      self.id_tarjeta, self.id_cuenta,
                                      self.tarifa, 0, dias))
        return cuerpo + _firma(clave, b"CAB", uid, cuerpo, largo=8)  # 24 bytes

    @classmethod
    def desde_bytes(cls, datos: bytes, uid: bytes, clave: bytes) -> "Cabecera":
        cuerpo, firma = datos[:16], datos[16:24]
        if cuerpo[:2] != MARCA:
            raise ErrorFormato("la tarjeta no está emitida (sin marca 'TR')")
        version, estado, id_t, id_c, tarifa, _, dias = struct.unpack(">BBIIBBH", cuerpo[2:])
        if version != VERSION:
            raise ErrorFormato(f"versión de formato no soportada: {version}")
        if not hmac.compare_digest(firma, _firma(clave, b"CAB", uid, cuerpo, largo=8)):
            raise ErrorFormato("firma de la cabecera inválida (datos alterados o de otra tarjeta)")
        return cls(id_t, id_c, tarifa, EPOCA + timedelta(days=dias), estado, version)


@dataclass
class Registro:
    saldo: int          # céntimos de sol
    operacion: int      # se incrementa en cada cambio de saldo
    fecha_hora: datetime
    validador: int
    recarga: int = 0    # n.º de la última recarga de la cuenta aplicada en la tarjeta

    def a_bytes(self, uid: bytes, clave: bytes, cab: Cabecera) -> bytes:
        cuerpo = struct.pack(">iHIHI", self.saldo, self.operacion,
                             int(self.fecha_hora.timestamp()), self.validador, self.recarga)
        # La firma incluye UID e id de tarjeta: un registro no sirve en otra tarjeta
        return cuerpo + _firma(clave, b"REG", uid, struct.pack(">I", cab.id_tarjeta),
                               cuerpo, largo=4)  # 20 bytes

    @classmethod
    def desde_bytes(cls, datos: bytes, uid: bytes, clave: bytes, cab: Cabecera):
        """Devuelve el registro, o None si está vacío o su firma no es válida."""
        cuerpo, firma = datos[:16], datos[16:20]
        esperada = _firma(clave, b"REG", uid, struct.pack(">I", cab.id_tarjeta), cuerpo, largo=4)
        if not hmac.compare_digest(firma, esperada):
            return None
        saldo, op, ts, val, recarga = struct.unpack(">iHIHI", cuerpo)
        return cls(saldo, op, datetime.fromtimestamp(ts, timezone.utc), val, recarga)


@dataclass
class EstadoTarjeta:
    cabecera: Cabecera
    registros: list          # [Registro|None, Registro|None]
    vigente: int             # índice (0 = A, 1 = B) del registro válido más reciente

    @property
    def registro(self) -> Registro:
        return self.registros[self.vigente]

    @property
    def ranura_libre(self) -> int:
        return 1 - self.vigente


def decodificar(area: bytes, uid: bytes, clave: bytes) -> EstadoTarjeta:
    """area = los 64 bytes de las páginas 8-23."""
    if len(area) != TAM_AREA:
        raise ErrorFormato(f"se esperaban {TAM_AREA} bytes, llegaron {len(area)}")
    cab = Cabecera.desde_bytes(area[0:24], uid, clave)
    regs = []
    for pag in PAG_REGISTROS:
        off = (pag - PAG_INICIO) * 4
        regs.append(Registro.desde_bytes(area[off:off + TAM_REGISTRO], uid, clave, cab))
    validos = [i for i, r in enumerate(regs) if r is not None]
    if not validos:
        raise ErrorFormato("ningún registro de saldo válido (tarjeta dañada o alterada)")
    vigente = max(validos, key=lambda i: regs[i].operacion)
    return EstadoTarjeta(cab, regs, vigente)


def codificar_emision(cab: Cabecera, saldo: int, uid: bytes, clave: bytes,
                      recarga: int = 0) -> bytes:
    """Área completa de una tarjeta recién emitida: registro A con el saldo
    inicial, registro B vacío. `recarga` = última recarga de la cuenta ya
    incluida en ese saldo."""
    reg = Registro(saldo, 1, datetime.now(timezone.utc).replace(microsecond=0), 0, recarga)
    area = bytearray(TAM_AREA)
    area[0:24] = cab.a_bytes(uid, clave)
    off_a = (PAG_REGISTROS[0] - PAG_INICIO) * 4
    area[off_a:off_a + TAM_REGISTRO] = reg.a_bytes(uid, clave, cab)
    return bytes(area)
