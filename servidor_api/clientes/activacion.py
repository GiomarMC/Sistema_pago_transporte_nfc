"""Códigos de emisión: solo el hash se persiste; el texto se devuelve una vez."""
import secrets

from django.db import transaction
from django.utils import timezone

from .models import Cliente, CodigoActivacion
from transporte.models import Tarjeta

ALFABETO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
LONGITUD = 8


def tiene_cliente(cuenta_id):
    return Cliente.objects.filter(cuenta_id=cuenta_id).exists()


def generar_codigo(tarjeta, operador=None):
    """Debe llamarse con la fila Tarjeta bloqueada y dentro de una transacción."""
    if tiene_cliente(tarjeta.cuenta_id):
        raise ValueError("la cuenta ya tiene cliente; no necesita código de activación")
    ahora = timezone.now()
    CodigoActivacion.objects.filter(tarjeta=tarjeta, usado_en__isnull=True,
                                    anulado_en__isnull=True).update(anulado_en=ahora)
    codigo = "".join(secrets.choice(ALFABETO) for _ in range(LONGITUD))
    registro = CodigoActivacion(tarjeta=tarjeta, emitido_por=operador)
    registro.asignar_codigo(codigo)
    registro.save()
    return codigo, registro


@transaction.atomic
def regenerar_codigo(tarjeta_id, operador):
    tarjeta = Tarjeta.objects.select_for_update().get(pk=tarjeta_id)
    if tarjeta.estado != Tarjeta.Estado.ACTIVA:
        raise ValueError("solo se regenera el código de una tarjeta activa")
    codigo, registro = generar_codigo(tarjeta, operador)
    return tarjeta, codigo, registro
