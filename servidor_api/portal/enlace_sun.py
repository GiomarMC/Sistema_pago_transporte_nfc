"""
Enlace SUN de la NTAG 424 DNA: https://mi.<dominio>/t?p=<32 hex>&m=<16 hex> (contrato C3).

leer_enlace() valida el formato. verificar_enlace() hace el resto:
  1. Criptografía (transporte/sun.py): descifra PICCData con K3 y comprueba el
     SDMMAC con K4(UID).
  2. Busca la tarjeta por UID entre las activas y bloqueadas (la emitida más
     reciente, si el UID se reutilizó tras un restablecimiento).
  3. Repetición (decisión C'): el SDMReadCtr debe ser mayor que todos los
     aceptados para ese UID, en cualquier emisión. Tarjeta.contador_sdm se
     actualiza con un UPDATE condicional, así que de dos peticiones simultáneas
     con el mismo enlace solo una pasa.
Sin claves configuradas (SUN_K3, SUN_K4_MAESTRAS) lanza VerificacionNoDisponible.

Referencias: docs/ntag424-personalizacion.md §8.
"""

import logging
import re
from dataclasses import dataclass

from django.db.models import Max

from transporte import sun
from transporte.models import Tarjeta

log = logging.getLogger(__name__)

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
    """El servidor no tiene configuradas las claves SUN."""


@dataclass(frozen=True)
class EnlaceSun:
    picc_data: bytes  # 16 B cifrados con K3
    mac: bytes        # 8 B de SDMMAC


@dataclass(frozen=True)
class ResultadoSun:
    """Lo único que muestra la página pública: nunca saldo ni nombre. cuenta_id no se
    muestra: sirve para saber si quien abre el enlace es el dueño (M20-02)."""

    estado: str  # ACTIVA o BLOQUEADA
    cuenta_id: int | None = None

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


def _rechazar(motivo: str, **contexto) -> EnlaceRechazado:
    # El motivo queda en el registro del servidor, nunca en la página
    log.warning("Enlace SUN rechazado: %s %s", motivo, contexto)
    return EnlaceRechazado(motivo)


def verificar_enlace(enlace: EnlaceSun) -> ResultadoSun:
    """Estado de la tarjeta. Lanza EnlaceRechazado o VerificacionNoDisponible."""
    claves = sun.claves_configuradas()
    if claves is None:
        raise VerificacionNoDisponible("faltan SUN_K3 y SUN_K4_MAESTRAS")
    try:
        lectura = sun.verificar(claves, enlace.picc_data, enlace.mac)
    except sun.SunInvalido as error:
        raise _rechazar(str(error)) from error

    uid = lectura.uid.hex().upper()
    vigentes = Tarjeta.objects.filter(uid=uid, estado__in=[Tarjeta.Estado.ACTIVA, Tarjeta.Estado.BLOQUEADA])
    tarjeta = vigentes.order_by("-emitida", "-id").first()
    if tarjeta is None:
        raise _rechazar("tarjeta inexistente")

    anterior = Tarjeta.objects.filter(uid=uid).aggregate(maximo=Max("contador_sdm"))["maximo"] or 0
    aceptado = lectura.contador > anterior and Tarjeta.objects.filter(
        pk=tarjeta.pk, contador_sdm__lt=lectura.contador).update(contador_sdm=lectura.contador)
    if not aceptado:
        raise _rechazar("contador repetido", tarjeta=tarjeta.pk, contador=lectura.contador, anterior=anterior)
    return ResultadoSun(ACTIVA if tarjeta.estado == Tarjeta.Estado.ACTIVA else BLOQUEADA, tarjeta.cuenta_id)
