"""Disparadores: convierten eventos del sistema en notificaciones al cliente.

Los módulos conectan estas funciones a sus servicios:
  - M11 (pagos y recargas): al_acreditar(orden)
  - M12 (identidad y seguridad): al_bloquear(tarjeta) y el resto de avisos de cuenta
  - M19 (panel): al_reposicion_lista(solicitud)
  - M17 (validador): al_registrar_viaje(movimiento)

Reglas que aplican todas:
  - Solo se notifica si la cuenta tiene un correo verificado (correo_verificado).
    Sin correo verificado, no se genera ninguna notificación.
  - Cada función devuelve la lista de notificaciones generadas (puede estar vacía).
"""

import logging

from django.db import transaction
from django.utils import timezone

from transporte.models import Alerta, soles

from .models import Notificacion
from .servicios import notificar

ALERTA_USO_NO_ACTIVA = "uso de tarjeta no activa"  # tipo que crea transporte.servicios
SALDO_BAJO_FACTOR = 2                              # saldo bajo: saldo < 2 × tarifa
TIPO_SALDO_BAJO = "saldo_bajo"
logger = logging.getLogger(__name__)


def intentar_sin_interrumpir(funcion, *args):
    """Prepara un aviso aislado en un savepoint: si falla, el cobro o el bloqueo siguen intactos."""
    try:
        with transaction.atomic():
            return funcion(*args)
    except Exception:
        logger.exception("No se pudo preparar la notificación %s", funcion.__name__)
        return []


def correo_verificado(cuenta):
    """Dirección a la que notificar, o None si el cliente no tiene correo verificado."""
    # Si existe identidad M12, su verificación prevalece sobre el correo
    # heredado en Cuenta; evita avisar a una dirección anterior del cliente.
    from clientes.models import Cliente
    cliente = Cliente.objects.filter(cuenta_id=cuenta.pk).values(
        "correo", "correo_verificado").first()
    if cliente is not None:
        return cliente["correo"] if cliente["correo_verificado"] else None
    if cuenta.correo and cuenta.correo_verificado_en is not None:
        return cuenta.correo
    return None


def _fecha(momento=None):
    return timezone.localtime(momento or timezone.now()).strftime("%d/%m/%Y %H:%M")


def al_acreditar(orden):
    """Una recarga (orden) quedó acreditada en la cuenta."""
    cuenta = orden.cuenta
    destino = correo_verificado(cuenta)
    if destino is None:
        return []
    return [notificar(destino, "recarga_acreditada", nombre=cuenta.nombre,
                      monto=soles(orden.monto), fecha=_fecha(),
                      _clave=f"recarga_acreditada:{orden.pk}")]


def al_bloquear(tarjeta):
    """La tarjeta se bloqueó."""
    cuenta = tarjeta.cuenta
    destino = correo_verificado(cuenta)
    if destino is None:
        return []
    return [notificar(destino, "tarjeta_bloqueada", nombre=cuenta.nombre, tarjeta=tarjeta.id,
                      fecha=_fecha(tarjeta.bloqueada_en),
                      _clave=f"tarjeta_bloqueada:{tarjeta.pk}")]


def al_reposicion_lista(solicitud):
    """Avisa una sola vez cuando la reposición queda lista o se entrega en emisión."""
    cuenta = solicitud.tarjeta.cuenta
    destino = correo_verificado(cuenta)
    if destino is None:
        return []
    return [notificar(destino, "reposicion_lista", nombre=cuenta.nombre,
                      tarjeta=solicitud.tarjeta_id,
                      tarjeta_nueva=solicitud.tarjeta_nueva_id,
                      entregada=solicitud.estado == "entregada",
                      _clave=f"reposicion_lista:{solicitud.pk}")]


def al_registrar_viaje(movimiento):
    """Se registró un viaje: avisa si se usó una tarjeta bloqueada y si el saldo quedó bajo.

    El aviso de saldo bajo se envía como máximo una vez al día por cliente.
    """
    cuenta = movimiento.cuenta
    destino = correo_verificado(cuenta)
    if destino is None:
        return []

    enviadas = []
    alerta_de_uso = movimiento.ref and Alerta.objects.filter(
        tipo=ALERTA_USO_NO_ACTIVA, detalle__startswith=f"{movimiento.ref}:").exists()
    if alerta_de_uso:
        enviadas.append(notificar(destino, "uso_tarjeta_bloqueada", nombre=cuenta.nombre,
                                  tarjeta=movimiento.tarjeta_id, fecha=_fecha(movimiento.fecha),
                                  _clave=f"uso_tarjeta_bloqueada:{movimiento.ref}"))

    precio = cuenta.tarifa.precio
    saldo_bajo = cuenta.saldo < SALDO_BAJO_FACTOR * precio
    ya_avisado_hoy = Notificacion.objects.filter(
        destinatario=destino, tipo=TIPO_SALDO_BAJO, creada__date=timezone.localdate()).exists()
    if saldo_bajo and not ya_avisado_hoy:
        enviadas.append(notificar(destino, TIPO_SALDO_BAJO, nombre=cuenta.nombre,
                                  saldo=soles(cuenta.saldo), tarifa=soles(precio),
                                  _clave=f"saldo_bajo:{cuenta.pk}:{timezone.localdate().isoformat()}"))
    return enviadas
