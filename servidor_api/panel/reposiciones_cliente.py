from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import render
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny

from pagos.api_cliente import error, titular
from transporte import servicios
from transporte.models import Cuenta, Tarjeta

from .models import SolicitudReposicion


def _salida(solicitud):
    return {"id": solicitud.pk, "tarjeta_id": solicitud.tarjeta_id,
            "tarjeta_nueva_id": solicitud.tarjeta_nueva_id,
            "estado": solicitud.estado, "creada": solicitud.creada.isoformat()}


@api_view(["POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def api_bloquear_tarjeta(request, tarjeta_id):
    cliente, fallo = titular(request)
    if fallo:
        return fallo
    datos = request.data if isinstance(request.data, dict) else {}
    motivo = datos.get("motivo")
    contrasena = datos.get("contrasena")
    if motivo not in ("robo", "perdida") or not isinstance(contrasena, str) or not contrasena:
        return error("validacion", "Indica robo o pérdida y confirma tu contraseña", 400)
    if not request.user.check_password(contrasena):
        return error("credenciales_invalidas", "Contraseña incorrecta", 403)
    with transaction.atomic():
        # Mismo orden de bloqueo que confirmar_emision: cuenta y luego tarjeta.
        Cuenta.objects.select_for_update().get(pk=cliente.cuenta_id)
        tarjeta = Tarjeta.objects.select_for_update().filter(
            pk=tarjeta_id, cuenta_id=cliente.cuenta_id).first()
        if tarjeta is None:
            return error("no_encontrado", "Tarjeta no encontrada", 404)
        if tarjeta.estado != Tarjeta.Estado.ACTIVA:
            return error("tarjeta_no_activa", "La tarjeta ya no está activa", 409)
        servicios.bloquear(tarjeta, motivo, tipo="bloqueo solicitado por cliente")
    return JsonResponse({"estado": tarjeta.estado,
                         "bloqueada_en": tarjeta.bloqueada_en.isoformat()})


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
    try:
        with transaction.atomic():
            Cuenta.objects.select_for_update().get(pk=cliente.cuenta_id)
            tarjeta = Tarjeta.objects.select_for_update().filter(
                pk=tarjeta_id, cuenta_id=cliente.cuenta_id).first()
            if not tarjeta or tarjeta.estado != Tarjeta.Estado.BLOQUEADA:
                return error("tarjeta_no_activa", "Solo se repone una tarjeta bloqueada propia", 409)
            anterior = SolicitudReposicion.objects.filter(tarjeta=tarjeta).first()
            if anterior is None and Tarjeta.objects.filter(
                    cuenta_id=cliente.cuenta_id, estado=Tarjeta.Estado.ACTIVA).exists():
                return error("reposicion_emitida", "La cuenta ya tiene una tarjeta activa", 409)
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
