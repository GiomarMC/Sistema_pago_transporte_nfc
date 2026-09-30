"""
Clave maestra del sistema y claves derivadas por tarjeta.

Cada tarjeta tiene su propia clave de firma y su propia contraseña, calculadas a
partir de la clave maestra y el UID. Así, descubrir la contraseña de una tarjeta
no compromete a las demás.

IMPORTANTE: si se pierde la clave maestra, las tarjetas emitidas ya no se pueden
leer ni reescribir (quedan protegidas con una contraseña que nadie conoce).
Haz una copia de seguridad de claves/clave_maestra.bin.
"""

import hashlib
import hmac
import os

from config import RUTA_CLAVE_MAESTRA

_maestra = None


def clave_maestra() -> bytes:
    global _maestra
    if _maestra is None:
        if not RUTA_CLAVE_MAESTRA.exists():
            RUTA_CLAVE_MAESTRA.parent.mkdir(parents=True, exist_ok=True)
            RUTA_CLAVE_MAESTRA.write_bytes(os.urandom(32))
            RUTA_CLAVE_MAESTRA.chmod(0o600)
            print(f"[!] Clave maestra nueva creada en {RUTA_CLAVE_MAESTRA}. "
                  "Haz una copia de seguridad: sin ella no se pueden leer las tarjetas.")
        _maestra = RUTA_CLAVE_MAESTRA.read_bytes()
        if len(_maestra) != 32:
            raise RuntimeError(f"{RUTA_CLAVE_MAESTRA} no tiene 32 bytes")
    return _maestra


def _derivar(etiqueta: bytes, uid: bytes) -> bytes:
    return hmac.new(clave_maestra(), etiqueta + uid, hashlib.sha256).digest()


def clave_firma(uid: bytes) -> bytes:
    """Clave HMAC con la que se firman la cabecera y los registros de saldo."""
    return _derivar(b"MAC", uid)


def contrasena(uid: bytes) -> tuple[bytes, bytes]:
    """(PWD de 4 bytes, PACK de 2 bytes) de la tarjeta."""
    d = _derivar(b"PWD", uid)
    return d[:4], d[4:6]
