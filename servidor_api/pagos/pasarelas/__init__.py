"""Elección de la pasarela de pago activa (contrato C5).

    from pagos.pasarelas import obtener_pasarela
    cobro = obtener_pasarela().crear_cobro(...)

El ajuste PAGOS_PASARELA dice cuál usar. Se crea una sola instancia por proceso (así un
test puede preparar la falsa y la vista que la use ve el mismo estado); se descarta
cuando un test cambia el ajuste con override_settings.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.module_loading import import_string

from .base import Pasarela

PASARELAS = {
    "falsa": "pagos.pasarelas.falsa.PasarelaFalsa",
    "simulada": "pagos.pasarelas.simulada.PasarelaSimulada",  # M11-02
}

_activa: Pasarela | None = None


def obtener_pasarela() -> Pasarela:
    global _activa
    if _activa is None:
        nombre = getattr(settings, "PAGOS_PASARELA", "")
        if nombre not in PASARELAS:
            raise ImproperlyConfigured(
                f"PAGOS_PASARELA={nombre!r} no es válida; opciones: {', '.join(PASARELAS)}")
        try:
            clase = import_string(PASARELAS[nombre])
        except ImportError as e:
            raise ImproperlyConfigured(
                f"La pasarela {nombre!r} todavía no está implementada ({e})") from e
        _activa = clase()
    return _activa


@receiver(setting_changed)
def _olvidar_pasarela(*, setting, **kwargs):
    global _activa
    if setting in ("PAGOS_PASARELA", "PAGOS_SECRETO_WEBHOOK"):
        _activa = None
