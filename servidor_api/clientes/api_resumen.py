"""Resumen mínimo de cuenta y tarjetas usado por el portal de reposiciones."""
from django.db.models import Case, IntegerField, Sum, When
from django.http import JsonResponse
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny

from pagos.api_cliente import titular
from transporte.models import Movimiento, Recarga, Tarjeta

from .models import numero_impreso


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def resumen(request):
    enlace, fallo = titular(request)
    if fallo:
        return fallo
    cuenta = enlace.cuenta
    tarjetas = Tarjeta.objects.filter(cuenta_id=cuenta.pk).exclude(
        estado=Tarjeta.Estado.PENDIENTE).annotate(
        prioridad=Case(When(estado=Tarjeta.Estado.ACTIVA, then=0),
                       default=1, output_field=IntegerField())).order_by("prioridad", "-pk")
    pendientes = Recarga.objects.filter(
        cuenta_id=cuenta.pk, aplicada_en__isnull=True,
        reembolsada_en__isnull=True).aggregate(total=Sum("monto"))["total"] or 0
    salida_tarjetas = []
    for tarjeta in tarjetas:
        ultimo = Movimiento.objects.filter(tarjeta_id=tarjeta.pk, tipo="viaje").order_by(
            "-fecha").values_list("fecha", flat=True).first()
        salida_tarjetas.append({
            "id": tarjeta.pk, "numero_impreso": numero_impreso(tarjeta),
            "estado": tarjeta.estado, "emitida": tarjeta.emitida.isoformat(),
            "ultimo_uso": ultimo.isoformat() if ultimo else None,
        })
    return JsonResponse({
        "cuenta": {"id": cuenta.pk, "saldo": cuenta.saldo,
                   "por_cargar_en_tarjeta": pendientes,
                   "tarifa": {"codigo": cuenta.tarifa_id,
                              "nombre": cuenta.tarifa.nombre,
                              "precio": cuenta.tarifa.precio}},
        "tarjetas": salida_tarjetas,
    })
