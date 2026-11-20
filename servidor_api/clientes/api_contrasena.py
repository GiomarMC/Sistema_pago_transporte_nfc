"""Recuperación y cambio de contraseña del cliente."""
import binascii
import logging
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny

from notificaciones.disparadores import correo_verificado
from notificaciones.models import Notificacion
from notificaciones.servicios import notificar
from pagos.api_cliente import titular

from .api_identidad import csrf_exigido, fallo
from .models import Cliente

logger = logging.getLogger(__name__)
generador = PasswordResetTokenGenerator()


def _datos(request):
    return request.data if isinstance(request.data, dict) else {}


def _comprobar_clave(clave, usuario):
    if not isinstance(clave, str) or len(clave) < 10:
        return "La contraseña necesita al menos 10 caracteres"
    try:
        validate_password(clave, user=usuario)
    except ValidationError as exc:
        return "; ".join(exc.messages)
    return None


def _cerrar_otras_sesiones(usuario, conservar=None):
    for sesion in Session.objects.all().iterator():
        if sesion.session_key == conservar:
            continue
        try:
            pertenece = sesion.get_decoded().get("_auth_user_id") == str(usuario.pk)
        except Exception:
            pertenece = False
        if pertenece:
            sesion.delete()


@csrf_exigido
@api_view(["POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def recuperar(request):
    documento = _datos(request).get("documento")
    with transaction.atomic():
        cliente = (Cliente.objects.select_for_update().select_related("usuario", "cuenta").filter(
            cuenta__documento=documento, correo_verificado=True).first()
            if isinstance(documento, str) else None)
        if cliente and not Notificacion.objects.filter(
                destinatario=cliente.correo, tipo="recuperar_contrasena",
                creada__gte=timezone.now() - timedelta(minutes=1)).exists():
            usuario = cliente.usuario
            base = ("/restablecer/" if getattr(request, "es_portal_cliente", False)
                    else "/mi/restablecer/")
            enlace = request.build_absolute_uri(base) + "?" + urlencode({
                "uid": urlsafe_base64_encode(force_bytes(usuario.pk)),
                "token": generador.make_token(usuario)})
            try:
                notificar(cliente.correo, "recuperar_contrasena",
                         nombre=cliente.cuenta.nombre, enlace=enlace)
            except Exception:
                logger.exception("No se pudo preparar recuperación de contraseña")
    return JsonResponse({"ok": True}, status=202)


@csrf_exigido
@api_view(["POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def restablecer(request):
    datos = _datos(request)
    try:
        uid = force_str(urlsafe_base64_decode(datos.get("uid", "")))
        usuario = get_user_model().objects.get(pk=uid)
    except (ValueError, TypeError, OverflowError, UnicodeDecodeError,
            binascii.Error, get_user_model().DoesNotExist):
        return fallo("validacion", "Enlace inválido o vencido", 400)
    cliente = Cliente.objects.select_related("cuenta").filter(usuario=usuario).first()
    if cliente is None or not generador.check_token(usuario, datos.get("token")):
        return fallo("validacion", "Enlace inválido o vencido", 400)
    mensaje = _comprobar_clave(datos.get("contrasena"), usuario)
    if mensaje:
        return fallo("validacion", mensaje, 400)
    with transaction.atomic():
        usuario = get_user_model().objects.select_for_update().get(pk=usuario.pk)
        if not generador.check_token(usuario, datos.get("token")):
            return fallo("validacion", "Enlace inválido o vencido", 400)
        usuario.set_password(datos["contrasena"])
        usuario.save(update_fields=["password"])
        _cerrar_otras_sesiones(usuario)
        notificar(cliente.correo, "contrasena_cambiada",
                 nombre=cliente.cuenta.nombre,
                 fecha=timezone.localtime().strftime("%d/%m/%Y %H:%M"))
    return JsonResponse({"ok": True})


@csrf_exigido
@api_view(["POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def cambiar(request):
    enlace, error_titular = titular(request)
    if error_titular:
        return error_titular
    datos = _datos(request)
    if not isinstance(datos.get("actual"), str) or not request.user.check_password(datos["actual"]):
        return fallo("credenciales_invalidas", "Contraseña actual incorrecta", 401)
    mensaje = _comprobar_clave(datos.get("nueva"), request.user)
    if mensaje:
        return fallo("validacion", mensaje, 400)
    with transaction.atomic():
        usuario = get_user_model().objects.select_for_update().get(pk=request.user.pk)
        if not usuario.check_password(datos["actual"]):
            return fallo("credenciales_invalidas", "Contraseña actual incorrecta", 401)
        usuario.set_password(datos["nueva"])
        usuario.save(update_fields=["password"])
        update_session_auth_hash(request, usuario)
        _cerrar_otras_sesiones(usuario, conservar=request.session.session_key)
        destino = correo_verificado(enlace.cuenta)
        if destino:
            notificar(destino, "contrasena_cambiada", nombre=enlace.cuenta.nombre,
                     fecha=timezone.localtime().strftime("%d/%m/%Y %H:%M"))
    return JsonResponse({"ok": True})
