from django.conf import settings

from .base import ErrorPasarela


def obtener_pasarela(nombre=None):
    nombre = nombre or settings.PAGOS_PASARELA
    if nombre == "simulada":
        from .simulada import PasarelaSimulada
        return PasarelaSimulada()
    raise ErrorPasarela(f"pasarela no configurada: {nombre}")
