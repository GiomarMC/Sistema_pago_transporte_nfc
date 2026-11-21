"""
Verificación del mensaje SUN de la NTAG 424 DNA (M1-08b, #42).

La tarjeta escribe en su NDEF https://mi.<dominio>/t?p=<PICCData cifrado>&m=<SDMMAC>
(contrato C3). Este módulo es solo criptografía, sin base de datos; el contador y
la búsqueda de la tarjeta están en portal/enlace_sun.py.

Referencias (docs/ntag424-personalizacion.md, §1):
  - descifrado de PICCData: AN12196 §3.4.2.2, Tabla 2
  - claves de sesión SDM: AN12196 §3.3, Tabla 1
  - SDMMAC: AN12196 §3.4.4.2.1, Tabla 4; MAC truncado: DS §9.1.3

Decisiones tomadas en M1-08 (PR #203):
  - D (SDMMACInput): SDMMACInputOffset == SDMMACOffset, así que el SDMMAC se
    calcula sobre una entrada vacía. Sigue ligado al UID y al SDMReadCtr porque
    los dos entran en la clave de sesión (SV2); si se altera `p`, el descifrado da
    otro UID o contador y el MAC no coincide.
  - K3 (SDMMetaReadKey) es común a todas las tarjetas: el servidor no conoce el
    UID antes de descifrar (AN12196 §3.4.2).
  - K4 (SDMFileReadKey) se diversifica con AN10922, ranura 4, con el contrato de
    M1-05 (#39). ⚠️ Pendiente de la conformidad de M2-04 (#47).
  - La versión de K4 no viaja en el enlace: se prueban las maestras configuradas,
    de la versión más nueva a la más antigua. Así la rotación no necesita guardar
    la versión de cada tarjeta.

Configuración (variables de entorno, nunca en el repositorio):
    SUN_K3            32 caracteres hexadecimales
    SUN_K4_MAESTRAS   versión:maestra separadas por comas, p. ej. "1:<32 hex>"
Sin ninguna de las dos, la verificación no está disponible (la página responde 501).
"""

import hmac
import re
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.cmac import CMAC
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

TAM_UID = 7
TAM_PICC = 16
TAM_MAC = 8

_ETIQUETA_PICC = 0xC7          # UID y SDMReadCtr reflejados, UID de 7 bytes (AN12196 Tabla 2)
_SV2 = bytes.fromhex("3CC300010080")
_SISTEMA = b"SubePe"           # contrato AN10922 de M1-05
_RANURA_K4 = 4
_HEX_CLAVE = re.compile(r"[0-9A-Fa-f]{32}")


class SunInvalido(Exception):
    """El enlace no supera la verificación criptográfica."""


@dataclass(frozen=True)
class ClavesSun:
    k3: bytes
    k4_maestras: tuple[tuple[int, bytes], ...]  # (versión, maestra), de la más nueva a la más antigua


@dataclass(frozen=True)
class LecturaSun:
    uid: bytes
    contador: int
    version_k4: int


def _cmac(clave: bytes, datos: bytes) -> bytes:
    calculo = CMAC(algorithms.AES(clave))
    calculo.update(datos)
    return calculo.finalize()


def descifrar_picc(k3: bytes, picc: bytes) -> tuple[bytes, int]:
    """(UID, SDMReadCtr) de PICCData = AES-CBC⁻¹(K3, IV = 0)."""
    if len(picc) != TAM_PICC:
        raise SunInvalido("PICCData debe tener 16 bytes")
    descifrador = Cipher(algorithms.AES(k3), modes.CBC(bytes(16))).decryptor()
    claro = descifrador.update(picc) + descifrador.finalize()
    if claro[0] != _ETIQUETA_PICC:
        raise SunInvalido("PICCDataTag inesperado")
    return claro[1:1 + TAM_UID], int.from_bytes(claro[8:11], "little")


def clave_sesion_mac(k4: bytes, uid: bytes, contador: int) -> bytes:
    """KSesSDMFileReadMAC = CMAC(K4, 3CC3 0001 0080 || UID || SDMReadCtr LSB primero)."""
    return _cmac(k4, _SV2 + uid + contador.to_bytes(3, "little"))


def sdmmac(k4: bytes, uid: bytes, contador: int) -> bytes:
    """MACt(KSesSDMFileReadMAC, entrada vacía): los bytes de índice impar (DS §9.1.3)."""
    return _cmac(clave_sesion_mac(k4, uid, contador), b"")[1::2]


def diversificar_k4(maestra: bytes, uid: bytes, version: int) -> bytes:
    """K4 de la tarjeta: AES-CMAC(maestra, 01 || UID || "SubePe" || 04 || versión || 00)."""
    if not 0x01 <= version <= 0xFF:
        raise ValueError("la versión va de 01h a FFh")
    return _cmac(maestra, b"\x01" + uid + _SISTEMA + bytes([_RANURA_K4, version, 0]))


def verificar(claves: ClavesSun, picc: bytes, mac: bytes) -> LecturaSun:
    """Descifra y comprueba el SDMMAC. Lanza SunInvalido sin decir qué falló."""
    if len(mac) != TAM_MAC:
        raise SunInvalido("SDMMAC debe tener 8 bytes")
    uid, contador = descifrar_picc(claves.k3, picc)
    for version, maestra in claves.k4_maestras:
        esperado = sdmmac(diversificar_k4(maestra, uid, version), uid, contador)
        if hmac.compare_digest(esperado, mac):
            return LecturaSun(uid=uid, contador=contador, version_k4=version)
    raise SunInvalido("SDMMAC incorrecto")


def _clave(texto: str, nombre: str) -> bytes:
    if not _HEX_CLAVE.fullmatch(texto):
        raise ImproperlyConfigured(f"{nombre} debe tener 32 caracteres hexadecimales")
    return bytes.fromhex(texto)


def _maestras(texto: str) -> tuple[tuple[int, bytes], ...]:
    maestras = {}
    for elemento in texto.split(","):
        version, separador, clave = elemento.strip().partition(":")
        if not separador or not version.strip().isdigit():
            raise ImproperlyConfigured("SUN_K4_MAESTRAS: cada elemento es versión:maestra")
        numero = int(version)
        if not 0x01 <= numero <= 0xFF:
            raise ImproperlyConfigured("SUN_K4_MAESTRAS: la versión va de 1 a 255")
        if numero in maestras:
            raise ImproperlyConfigured(f"SUN_K4_MAESTRAS: versión {numero} repetida")
        maestras[numero] = _clave(clave.strip(), f"SUN_K4_MAESTRAS (versión {numero})")
    return tuple(sorted(maestras.items(), reverse=True))


def claves_configuradas() -> ClavesSun | None:
    """Claves de settings, o None si no se configuró ninguna."""
    k3 = getattr(settings, "SUN_K3", "").strip()
    maestras = getattr(settings, "SUN_K4_MAESTRAS", "").strip()
    if not k3 and not maestras:
        return None
    if not k3 or not maestras:
        raise ImproperlyConfigured("SUN_K3 y SUN_K4_MAESTRAS se configuran juntas")
    return ClavesSun(k3=_clave(k3, "SUN_K3"), k4_maestras=_maestras(maestras))
