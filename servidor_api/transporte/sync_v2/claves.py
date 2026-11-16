"""Claves del protocolo C1 en el servidor (M0-06).

- ECDSA P-256 del servidor: firma LISTAS, ACUSE y TARIFAS. La privada vive solo en el
  ajuste LISTAS_CLAVE_PRIVADA (.env); la pública va en el firmware, en
  pantalla_esp32/src/validador/clave_servidor.h. Ambas salen de `generar_clave_listas`.
- HMAC-SHA256 de cada validador: firma sus EVENTOS y HOLA. Está en Validador.clave_hmac;
  se consulta o rota con `clave_validador`.
"""

import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def pem_en_una_linea(pem: str) -> str:
    """El .env es de una línea por variable: los saltos del PEM se escriben como \\n."""
    return pem.strip().replace("\n", "\\n")


def clave_privada_listas() -> ec.EllipticCurvePrivateKey:
    pem = getattr(settings, "LISTAS_CLAVE_PRIVADA", "").strip().replace("\\n", "\n")
    if not pem:
        raise ImproperlyConfigured(
            "Falta LISTAS_CLAVE_PRIVADA en el .env: genérala con manage.py generar_clave_listas")
    try:
        clave = serialization.load_pem_private_key(pem.encode(), password=None)
    except ValueError as e:
        raise ImproperlyConfigured(f"LISTAS_CLAVE_PRIVADA no es una clave PEM válida: {e}") \
            from None
    if not isinstance(clave, ec.EllipticCurvePrivateKey) or clave.curve.name != "secp256r1":
        raise ImproperlyConfigured("LISTAS_CLAVE_PRIVADA debe ser una clave ECDSA P-256")
    return clave


def clave_publica_listas() -> ec.EllipticCurvePublicKey:
    return clave_privada_listas().public_key()


def punto_publico(clave: ec.EllipticCurvePublicKey) -> bytes:
    """Punto sin comprimir de 65 B (0x04 | X | Y), el formato de clave_servidor.h."""
    return clave.public_bytes(serialization.Encoding.X962,
                              serialization.PublicFormat.UncompressedPoint)


def huella(clave: ec.EllipticCurvePublicKey) -> str:
    """Primeros 8 B del SHA-256 del punto: para comparar servidor y firmware a simple vista."""
    return hashlib.sha256(punto_publico(clave)).hexdigest()[:16]


def clave_hmac_de(numero: int) -> bytes | None:
    """Clave del validador `numero` para desempaquetar(); None si no existe o está inactivo."""
    from transporte.models import Validador

    v = Validador.objects.filter(numero=numero, activo=True).only("clave_hmac").first()
    return v.clave_hmac_bytes if v else None
