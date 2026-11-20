"""
Enlace SUN de la NTAG 424 DNA: https://mi.<dominio>/t?p=<32 hex>&m=<16 hex> (contrato C3).

Este módulo solo lee y valida el formato del enlace y define lo que la vista
espera de la verificación. La verificación criptográfica (descifrar PICCData
con K3, CMAC con K4(UID), contador repetido) va en transporte/sun.py (M1-08b) y
depende de decisiones aún abiertas: SDMMACInput (D), diversificación AN10922
(M1-05) y contador_sdm (C'). Mientras tanto verificar_enlace() lanza
VerificacionNoDisponible y la página lo dice.

Referencias: docs/ntag424-personalizacion.md §8.
"""

import re
from dataclasses import dataclass

TAM_PICC_DATA = 16  # 32 caracteres hexadecimales
TAM_MAC = 8         # 16 caracteres hexadecimales
_HEX_PICC = re.compile(r"[0-9A-Fa-f]{32}")
_HEX_MAC = re.compile(r"[0-9A-Fa-f]{16}")

ACTIVA, BLOQUEADA = "activa", "bloqueada"


class EnlaceInvalido(ValueError):
    """El enlace no tiene la forma ?p=<32 hex>&m=<16 hex>."""


class EnlaceRechazado(Exception):
    """El enlace tiene buena forma pero no supera la verificación (CMAC alterado,
    contador repetido o tarjeta inexistente). La página no dice cuál, para no dar
    pistas a quien pruebe enlaces falsos."""


class VerificacionNoDisponible(Exception):
    """Todavía no se puede verificar (M1-08b sin implementar)."""


@dataclass(frozen=True)
class EnlaceSun:
    picc_data: bytes  # 16 B cifrados con K3
    mac: bytes        # 8 B de SDMMAC


@dataclass(frozen=True)
class ResultadoSun:
    """Lo único que muestra la página pública: nunca saldo ni nombre."""

    estado: str  # ACTIVA o BLOQUEADA

    def __post_init__(self):
        if self.estado not in (ACTIVA, BLOQUEADA):
            raise ValueError(f"estado desconocido: {self.estado!r}")


def _unico(parametros, nombre: str) -> str:
    valores = parametros.getlist(nombre)
    if len(valores) != 1:
        raise EnlaceInvalido(f"se esperaba un único parámetro {nombre!r}")
    return valores[0]


def leer_enlace(parametros) -> EnlaceSun:
    """`parametros` es request.GET (un QueryDict). Lanza EnlaceInvalido."""
    picc_data, mac = _unico(parametros, "p"), _unico(parametros, "m")
    if not _HEX_PICC.fullmatch(picc_data):
        raise EnlaceInvalido("p debe tener 32 caracteres hexadecimales")
    if not _HEX_MAC.fullmatch(mac):
        raise EnlaceInvalido("m debe tener 16 caracteres hexadecimales")
    return EnlaceSun(bytes.fromhex(picc_data), bytes.fromhex(mac))


def verificar_enlace(enlace: EnlaceSun) -> ResultadoSun:
    """Devuelve el estado de la tarjeta o lanza EnlaceRechazado (M1-08b)."""
    raise VerificacionNoDisponible("verificación SUN pendiente de M1-08b")
