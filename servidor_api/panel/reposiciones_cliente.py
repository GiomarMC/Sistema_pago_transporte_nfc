from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import render
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny

from pagos.api_cliente import error, titular
from transporte.models import Tarjeta

from .models import SolicitudReposicion


def _salida(solicitud):
    return {"id": solicitud.pk, "tarjeta_id": solicitud.tarjeta_id,
            "estado": solicitud.estado, "creada": solicitud.creada.isoformat()}


@api_view(["GET", "POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def api_reposiciones(request):
    cliente, fallo = titular(request)
    if fallo:
        return fallo
    if request.method == "GET":
        return JsonResponse([_salida(s) for s in SolicitudReposicion.objects.filter(
            cliente=cliente).order_by("-creada")], safe=False)
    try:
        tarjeta_id = int(request.data.get("tarjeta_id", 0))
    except (ValueError, TypeError):
        return error("validacion", "Tarjeta inválida", 400)
    tarjeta = Tarjeta.objects.filter(pk=tarjeta_id, cuenta=cliente.cuenta).first()
    if not tarjeta or tarjeta.estado != Tarjeta.Estado.BLOQUEADA:
        return error("tarjeta_no_activa", "Solo se repone una tarjeta bloqueada propia", 409)
    try:
        with transaction.atomic():
            anterior = SolicitudReposicion.objects.filter(tarjeta=tarjeta).first()
            solicitud = anterior or SolicitudReposicion.objects.create(cliente=cliente, tarjeta=tarjeta)
            creada = anterior is None
    except IntegrityError:
        solicitud = SolicitudReposicion.objects.get(tarjeta=tarjeta)
        creada = False
    return JsonResponse(_salida(solicitud), status=201 if creada else 200)


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def pagina_reposiciones(request):
    cliente, fallo = titular(request)
    if fallo:
        return fallo
    solicitudes = SolicitudReposicion.objects.filter(cliente=cliente).select_related("tarjeta").order_by("-creada")
    return render(request, "panel/reposiciones_cliente.html", {"solicitudes": solicitudes})
