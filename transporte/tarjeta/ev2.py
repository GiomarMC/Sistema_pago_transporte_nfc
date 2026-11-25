"""
Criptografía de la NTAG 424 DNA: AuthenticateEV2First y mensajería segura AES.

Funciones puras (sin lector ni estado global) que usan por igual el simulador
(lado tarjeta, M1-03) y el controlador (lado lector, M1-04). Referencias en
docs/ntag424-personalizacion.md (DS = hoja de datos NT4H2421Gx Rev. 3.0):

  - claves de sesión: DS §9.1.7, p. 26
  - CmdCtr y TI: DS §9.1.1-§9.1.2, p. 23
  - MAC truncado: DS §9.1.3, p. 23
  - IV y relleno del cifrado: DS §9.1.4, p. 24
  - MAC y Full: DS §9.1.9-§9.1.10, pp. 27-28
  - ChangeKey y CRC32NK: DS §10.6.1, p. 62
"""

import hmac
import zlib
from dataclasses import dataclass, replace

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.cmac import CMAC

TAM_BLOQUE = 16
TAM_CLAVE = 16
TAM_TI = 4
TAM_MAC = 8
CMDCTR_MAXIMO = 0xFFFF
IV_CERO = bytes(TAM_BLOQUE)

_ETIQUETA_SV_ENC = bytes.fromhex("A55A00010080")
_ETIQUETA_SV_MAC = bytes.fromhex("5AA500010080")
_ETIQUETA_IV_COMANDO = bytes.fromhex("A55A")
_ETIQUETA_IV_RESPUESTA = bytes.fromhex("5AA5")


class ErrorIntegridad(ValueError):
    """MAC, relleno o CRC incorrectos (la tarjeta respondería 91 1E)."""


def _comprobar_clave(clave: bytes) -> None:
    if len(clave) != TAM_CLAVE:
        raise ValueError(f"una clave AES-128 tiene {TAM_CLAVE} bytes")


# --- Primitivas AES -----------------------------------------------------------------

def aes_ecb(clave: bytes, bloque: bytes) -> bytes:
    _comprobar_clave(clave)
    cifrador = Cipher(algorithms.AES(clave), modes.ECB()).encryptor()
    return cifrador.update(bloque) + cifrador.finalize()


def aes_cbc_cifrar(clave: bytes, iv: bytes, datos: bytes) -> bytes:
    _comprobar_clave(clave)
    if len(datos) % TAM_BLOQUE:
        raise ValueError("AES-CBC sin relleno exige múltiplos de 16 bytes")
    cifrador = Cipher(algorithms.AES(clave), modes.CBC(iv)).encryptor()
    return cifrador.update(datos) + cifrador.finalize()


def aes_cbc_descifrar(clave: bytes, iv: bytes, datos: bytes) -> bytes:
    _comprobar_clave(clave)
    if len(datos) % TAM_BLOQUE:
        raise ValueError("AES-CBC sin relleno exige múltiplos de 16 bytes")
    descifrador = Cipher(algorithms.AES(clave), modes.CBC(iv)).decryptor()
    return descifrador.update(datos) + descifrador.finalize()


def cmac(clave: bytes, datos: bytes) -> bytes:
    """AES-CMAC completo (16 bytes), NIST SP 800-38B."""
    _comprobar_clave(clave)
    calculo = CMAC(algorithms.AES(clave))
    calculo.update(datos)
    return calculo.finalize()


def truncar_mac(mac: bytes) -> bytes:
    """Los 8 bytes de índice impar (1, 3, ..., 15): DS §9.1.3."""
    if len(mac) != TAM_BLOQUE:
        raise ValueError("el CMAC tiene 16 bytes")
    return mac[1::2]


def mac_truncado(clave: bytes, datos: bytes) -> bytes:
    return truncar_mac(cmac(clave, datos))


def mac_igual(recibido: bytes, esperado: bytes) -> bool:
    """Compara MAC en tiempo constante (el controlador verifica las respuestas)."""
    return hmac.compare_digest(recibido, esperado)


def rellenar(datos: bytes) -> bytes:
    """ISO/IEC 9797-1 método 2: siempre 80h y ceros hasta múltiplo de 16."""
    relleno = TAM_BLOQUE - (len(datos) % TAM_BLOQUE)
    return datos + b"\x80" + bytes(relleno - 1)


def quitar_relleno(datos: bytes) -> bytes:
    if not datos or len(datos) % TAM_BLOQUE:
        raise ErrorIntegridad("longitud cifrada inválida")
    sin_ceros = datos.rstrip(b"\x00")
    if not sin_ceros.endswith(b"\x80") or len(datos) - len(sin_ceros) >= TAM_BLOQUE:
        raise ErrorIntegridad("relleno inválido")
    return sin_ceros[:-1]


def rotar_izquierda(datos: bytes) -> bytes:
    return datos[1:] + datos[:1]


def rotar_derecha(datos: bytes) -> bytes:
    return datos[-1:] + datos[:-1]


# --- Autenticación ------------------------------------------------------------------

def claves_sesion(clave: bytes, rnd_a: bytes, rnd_b: bytes) -> tuple[bytes, bytes]:
    """(SesAuthENCKey, SesAuthMACKey) a partir de la clave usada y RndA/RndB."""
    if len(rnd_a) != TAM_BLOQUE or len(rnd_b) != TAM_BLOQUE:
        raise ValueError("RndA y RndB tienen 16 bytes")
    mezcla = bytes(a ^ b for a, b in zip(rnd_a[2:8], rnd_b[0:6]))
    contexto = rnd_a[0:2] + mezcla + rnd_b[6:16] + rnd_a[8:16]
    return cmac(clave, _ETIQUETA_SV_ENC + contexto), cmac(clave, _ETIQUETA_SV_MAC + contexto)


@dataclass(frozen=True)
class Sesion:
    """Estado de la mensajería segura tras AuthenticateEV2First.

    Inmutable: avanzada() devuelve la sesión con el CmdCtr incrementado. El lado
    que envía calcula el comando con el CmdCtr actual y la respuesta con el
    siguiente (DS §9.1.2)."""

    clave_enc: bytes
    clave_mac: bytes
    ti: bytes
    cmd_ctr: int = 0

    def __post_init__(self):
        _comprobar_clave(self.clave_enc)
        _comprobar_clave(self.clave_mac)
        if len(self.ti) != TAM_TI:
            raise ValueError("el TI tiene 4 bytes")
        if not 0 <= self.cmd_ctr <= CMDCTR_MAXIMO:
            raise ValueError("CmdCtr fuera de rango")

    @property
    def agotada(self) -> bool:
        """Con CmdCtr = FFFFh la tarjeta rechaza el siguiente comando (DS §9.1.2)."""
        return self.cmd_ctr == CMDCTR_MAXIMO

    def avanzada(self) -> "Sesion":
        if self.agotada:
            raise ErrorIntegridad("CmdCtr agotado")
        return replace(self, cmd_ctr=self.cmd_ctr + 1)

    def _ctr(self) -> bytes:
        return self.cmd_ctr.to_bytes(2, "little")

    def _iv(self, etiqueta: bytes) -> bytes:
        return aes_ecb(self.clave_enc, etiqueta + self.ti + self._ctr() + bytes(8))

    def mac_comando(self, ins: int, cabecera: bytes = b"", datos: bytes = b"") -> bytes:
        return mac_truncado(self.clave_mac, bytes([ins]) + self._ctr() + self.ti + cabecera + datos)

    def mac_respuesta(self, codigo: int, datos: bytes = b"") -> bytes:
        """MAC de la respuesta; llamar sobre la sesión ya avanzada."""
        return mac_truncado(self.clave_mac, bytes([codigo]) + self._ctr() + self.ti + datos)

    def cifrar_comando(self, datos: bytes) -> bytes:
        return aes_cbc_cifrar(self.clave_enc, self._iv(_ETIQUETA_IV_COMANDO), rellenar(datos))

    def descifrar_comando(self, cifrado: bytes) -> bytes:
        if not cifrado or len(cifrado) % TAM_BLOQUE:
            raise ErrorIntegridad("longitud cifrada inválida")
        return quitar_relleno(aes_cbc_descifrar(self.clave_enc, self._iv(_ETIQUETA_IV_COMANDO), cifrado))

    def cifrar_respuesta(self, datos: bytes) -> bytes:
        """Llamar sobre la sesión ya avanzada."""
        return aes_cbc_cifrar(self.clave_enc, self._iv(_ETIQUETA_IV_RESPUESTA), rellenar(datos))

    def descifrar_respuesta(self, cifrado: bytes) -> bytes:
        if not cifrado or len(cifrado) % TAM_BLOQUE:
            raise ErrorIntegridad("longitud cifrada inválida")
        return quitar_relleno(aes_cbc_descifrar(self.clave_enc, self._iv(_ETIQUETA_IV_RESPUESTA), cifrado))


# --- ChangeKey ------------------------------------------------------------------------

def crc32nk(clave: bytes) -> bytes:
    """CRC32 de ChangeKey: complemento de zlib.crc32, LSB primero (DS Tabla 63;
    comprobado con AN12196 Tabla 25: 789DFADC)."""
    return (~zlib.crc32(clave) & 0xFFFFFFFF).to_bytes(4, "little")


def datos_change_key(numero: int, nueva: bytes, anterior: bytes, version: int) -> bytes:
    """KeyData en claro (antes de rellenar y cifrar). K0: NewKey || KeyVer.
    K1-K4: (NewKey xor OldKey) || KeyVer || CRC32NK(NewKey)."""
    _comprobar_clave(nueva)
    if not 0 <= version <= 0xFF:
        raise ValueError("KeyVer es un byte")
    if numero == 0:
        return nueva + bytes([version])
    if not 1 <= numero <= 4:
        raise ValueError("la NTAG 424 tiene las claves 0 a 4")
    _comprobar_clave(anterior)
    return bytes(a ^ b for a, b in zip(nueva, anterior)) + bytes([version]) + crc32nk(nueva)
