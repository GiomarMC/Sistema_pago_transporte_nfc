"""Cambios fuera de servicios.py: tarifas del admin y borrados excepcionales."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Recarga, Tarifa, Tarjeta
from .sync_cambios import registrar_recarga, registrar_tarifa, registrar_tarjeta


@receiver(post_save, sender=Tarifa)
def tarifa_guardada(sender, instance, raw=False, **kwargs):
    if not raw:
        registrar_tarifa(instance)


@receiver(post_delete, sender=Tarifa)
def tarifa_borrada(sender, instance, **kwargs):
    registrar_tarifa(instance, activa=False)


@receiver(post_delete, sender=Tarjeta)
def tarjeta_borrada(sender, instance, **kwargs):
    if instance.estado in (Tarjeta.Estado.BLOQUEADA, Tarjeta.Estado.ANULADA):
        registrar_tarjeta(instance.pk, bloqueada=False)


@receiver(post_delete, sender=Recarga)
def recarga_borrada(sender, instance, **kwargs):
    if instance.aplicada_en is None:
        registrar_recarga(instance, pendiente=False)
