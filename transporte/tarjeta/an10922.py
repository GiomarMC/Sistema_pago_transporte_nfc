"""
Diversificación de claves AES-128 de NXP AN10922 (M1-05, #39): una clave por tarjeta a partir de una maestra y
el UID, para que copiar las claves de una tarjeta no sirva para las demás.

Contrato compartido con el firmware (M2-04, #47) y el servidor (transporte/sun.py, M1-08):

    M = UID(7) || "SubePe" || ranura(1) || versión(1) || 00        (16 bytes)
    K = AES-CMAC(maestra, 01h || M)                                 (CMAC estándar, RFC 4493)

Con |M| >= 16, AN10922 §2.2 coincide con el CMAC estándar (§2.2.1: «If the length of M is more than 15 bytes,
standard CMAC algorithm can be used»): AN10922 rellena D = 01h || M hasta 32 bytes con 80h 00…, igual que CMAC
cuando el último bloque está incompleto. Con M más corta (como la de 13 bytes de antes) no coincide.

- Ranuras 0, 1, 2 y 4. K3 no se diversifica (AN12196 §3.4.2: el servidor no conoce el UID antes de descifrar).
- Versión 01h-FFh, la misma que el KeyVer grabado con ChangeKey; 00h es la clave de fábrica.
- El último byte de M está reservado y vale 00h.
- UID de 7 bytes en orden de anticolisión (UID0 = 04h primero).

Funciones puras: no leen claves ni configuración (dónde viven las maestras es la decisión de custodia A).
"""

from cryptography.hazmat.primitives.ciphers import algorithms
from cryptography.hazmat.primitives.cmac import CMAC

SISTEMA = b"SubePe"
RANURAS = (0, 1, 2, 4)
TAM_CLAVE = 16
TAM_UID = 7
TAM_M = 16
_DIV_AES128 = 0x01      # constante de diversificación de AES-128 (AN10922 §2.2)
_RESERVADO = 0x00


def cmac_aes128(clave: bytes, datos: bytes) -> bytes:
    """AES-CMAC completo de 16 bytes (RFC 4493 / NIST SP 800-38B)."""
    if len(clave) != TAM_CLAVE:
        raise ValueError(f"la clave AES-128 tiene {TAM_CLAVE} bytes")
    calculo = CMAC(algorithms.AES(clave))
    calculo.update(datos)
    return calculo.finalize()


def entrada_m(uid: bytes, ranura: int, version: int) -> bytes:
    """Los 16 bytes de M del contrato."""
    if not isinstance(uid, (bytes, bytearray)) or len(uid) != TAM_UID:
        raise ValueError(f"el UID tiene {TAM_UID} bytes")
    if ranura not in RANURAS:
        raise ValueError(f"ranura {ranura!r}: se diversifican las claves {RANURAS} (K3 no)")
    if not isinstance(version, int) or not 0x01 <= version <= 0xFF:
        raise ValueError("la versión va de 01h a FFh (00h es la clave de fábrica)")
    return bytes(uid) + SISTEMA + bytes([ranura, version, _RESERVADO])


def an10922_aes128(maestra: bytes, m: bytes) -> bytes:
    """AN10922 §2.2 para AES-128 con 16 <= |M| <= 31, donde equivale al CMAC estándar de 01h || M."""
    if not TAM_M <= len(m) <= 31:
        raise ValueError("M debe tener de 16 a 31 bytes para que AN10922 coincida con el CMAC estándar")
    return cmac_aes128(maestra, bytes([_DIV_AES128]) + bytes(m))


def diversificar_aes128(maestra: bytes, uid: bytes, ranura: int, version: int) -> bytes:
    """Clave de la ranura `ranura` de la tarjeta `uid`."""
    if not isinstance(maestra, (bytes, bytearray)) or len(maestra) != TAM_CLAVE:
        raise ValueError(f"la maestra AES-128 tiene {TAM_CLAVE} bytes")
    return an10922_aes128(bytes(maestra), entrada_m(uid, ranura, version))
