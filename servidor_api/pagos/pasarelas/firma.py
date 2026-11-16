"""Firma de los webhooks de las pasarelas falsa y simulada (regla 2 del contrato C5).

    X-Firma: t=<unix>,v1=<hex HMAC-SHA256(secreto, "<t>." + cuerpo)>

Se rechaza si `t` difiere más de 300 s de la hora del servidor, para que un webhook
capturado no pueda reenviarse más tarde.
"""

import hashlib
import hmac
import time
from collections.abc import Mapping

from django.core.exceptions import ImproperlyConfigured

from .base import FirmaInvalida

CABECERA = "X-Firma"
TOLERANCIA = 300  # segundos


def _hmac(secreto: str, t: int, cuerpo: bytes) -> str:
    if not secreto:
        raise ImproperlyConfigured("Falta PAGOS_SECRETO_WEBHOOK para firmar los webhooks")
    return hmac.new(secreto.encode(), f"{t}.".encode() + cuerpo, hashlib.sha256).hexdigest()


def firmar(secreto: str, cuerpo: bytes, t: int | None = None) -> str:
    """Devuelve el valor de la cabecera X-Firma para `cuerpo`."""
    t = int(time.time()) if t is None else t
    return f"t={t},v1={_hmac(secreto, t, cuerpo)}"


def verificar(secreto: str, cabeceras: Mapping[str, str], cuerpo: bytes,
              ahora: float | None = None) -> None:
    """Lanza FirmaInvalida si la cabecera falta, está mal formada, no cuadra o está fuera
    de hora."""
    valor = next((v for k, v in cabeceras.items() if k.lower() == CABECERA.lower()), None)
    if not valor:
        raise FirmaInvalida(f"falta la cabecera {CABECERA}")
    partes = {}
    for trozo in valor.split(","):
        clave, igual, dato = trozo.strip().partition("=")
        if not igual:
            raise FirmaInvalida(f"cabecera {CABECERA} mal formada")
        partes[clave] = dato
    try:
        t = int(partes["t"])
        recibida = partes["v1"]
    except (KeyError, ValueError):
        raise FirmaInvalida(f"cabecera {CABECERA} mal formada") from None
    ahora = time.time() if ahora is None else ahora
    if abs(ahora - t) > TOLERANCIA:
        raise FirmaInvalida("firma fuera de hora")
    if not hmac.compare_digest(_hmac(secreto, t, cuerpo), recibida):
        raise FirmaInvalida("firma no válida")
