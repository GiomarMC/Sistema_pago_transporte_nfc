"""
Formato de las APDU de la NTAG 424 DNA (DS §10.2-§10.3, pp. 44-45).

Los comandos nativos van envueltos en ISO 7816-4 con CLA = 90 y la respuesta
termina en 91 <código>; ISOSelectFile usa CLA = 00 y responde 90 00.
"""

CLA_NATIVO = 0x90
CLA_ISO = 0x00

# Códigos de instrucción (DS Tabla 22, p. 44)
INS_SELECT = 0xA4
INS_READ_BINARY = 0xB0     # ISOReadBinary (CLA 00)
INS_GET_VERSION = 0x60
INS_AUTH_EV2_FIRST = 0x71
INS_FRAME_ADICIONAL = 0xAF
INS_CHANGE_KEY = 0xC4
INS_CHANGE_FILE_SETTINGS = 0x5F
INS_GET_FILE_SETTINGS = 0xF5
INS_GET_FILE_COUNTERS = 0xF6
INS_READ_DATA = 0xAD
INS_WRITE_DATA = 0x8D

# Segundo byte de 91xx (DS Tabla 23, p. 45)
OPERATION_OK = 0x00
ILLEGAL_COMMAND_CODE = 0x1C
INTEGRITY_ERROR = 0x1E
NO_SUCH_KEY = 0x40
LENGTH_ERROR = 0x7E
PERMISSION_DENIED = 0x9D
PARAMETER_ERROR = 0x9E
AUTHENTICATION_DELAY = 0xAD
AUTHENTICATION_ERROR = 0xAE
ADDITIONAL_FRAME = 0xAF
BOUNDARY_ERROR = 0xBE
COMMAND_ABORTED = 0xCA
FILE_NOT_FOUND = 0xF0

# ISO 7816-4 (DS Tabla 24, p. 45)
SW_ISO_OK = 0x9000
SW_ISO_NO_ENCONTRADO = 0x6A82
SW_ISO_CLA_NO_SOPORTADA = 0x6E00
SW_ISO_INS_NO_SOPORTADA = 0x6D00

DF_APLICACION_NDEF = bytes.fromhex("D2760000850101")  # DS §10.9.1, p. 77
DF_PICC = bytes.fromhex("D2760000850100")


class TarjetaRetirada(Exception):
    """La tarjeta (simulada) salió del campo a mitad de un comando. El
    controlador de M1-04 la traducirá a tarjeta.base.ErrorTarjeta."""


def nativo(ins: int, datos: bytes = b"") -> bytes:
    """APDU de un comando nativo: 90 INS 00 00 [Lc datos] 00."""
    if len(datos) > 0xFF:
        raise ValueError("los comandos cortos llevan hasta 255 bytes")
    cuerpo = bytes([len(datos)]) + datos if datos else b""
    return bytes([CLA_NATIVO, ins, 0x00, 0x00]) + cuerpo + b"\x00"


def seleccionar_df(nombre: bytes) -> bytes:
    return bytes([CLA_ISO, INS_SELECT, 0x04, 0x00, len(nombre)]) + nombre + b"\x00"


def separar_sw(respuesta: bytes) -> tuple[bytes, int]:
    """(datos, SW de 2 bytes como entero)."""
    if len(respuesta) < 2:
        raise ValueError("una respuesta APDU tiene al menos 2 bytes")
    return respuesta[:-2], (respuesta[-2] << 8) | respuesta[-1]


def sw_nativo(codigo: int) -> bytes:
    return bytes([0x91, codigo])
