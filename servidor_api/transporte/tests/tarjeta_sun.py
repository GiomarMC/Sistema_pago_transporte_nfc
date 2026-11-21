"""
Lado TARJETA del SUN, solo para las pruebas: genera el enlace que mostraría una
NTAG 424 DNA personalizada según M1-02 (decisión D: SDMMAC sobre entrada vacía).

Se escribe aparte de transporte/sun.py a propósito, para que las pruebas no
comprueben el código con el propio código; se valida con el ejemplo de AN12196
(§3.4.2 y §3.4.4.2.1, Tablas 2 y 4) en test_sun.py.
"""

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.cmac import CMAC


def _cmac(clave: bytes, datos: bytes) -> bytes:
    calculo = CMAC(algorithms.AES(clave))
    calculo.update(datos)
    return calculo.finalize()


def k4_de_tarjeta(maestra: bytes, uid: bytes, version: int = 1) -> bytes:
    """K4 grabada en la tarjeta: AN10922 con M = UID || "SubePe" || 04 || versión || 00 (#39)."""
    return _cmac(maestra, b"\x01" + uid + b"SubePe" + bytes([4, version, 0]))


def enlace(k3: bytes, k4: bytes, uid: bytes, contador: int, relleno: bytes = bytes(5)) -> tuple[str, str]:
    """(p, m) en hexadecimal mayúsculas, como los escribe la tarjeta en la URL."""
    claro = bytes([0xC7]) + uid + contador.to_bytes(3, "little") + relleno
    cifrador = Cipher(algorithms.AES(k3), modes.CBC(bytes(16))).encryptor()
    picc = cifrador.update(claro) + cifrador.finalize()
    sesion = _cmac(k4, bytes.fromhex("3CC300010080") + uid + contador.to_bytes(3, "little"))
    mac = _cmac(sesion, b"")[1::2]
    return picc.hex().upper(), mac.hex().upper()
