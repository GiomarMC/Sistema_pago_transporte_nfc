"""Consultas de seguridad y verificación de cambio de correo."""
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny

from notificaciones.servicios import notificar
from pagos.api_cliente import titular

from .api_identidad import csrf_exigido, fallo
from .models import Cliente, CodigoOTP, Dispositivo, IntentoAcceso
from .servicios import dispositivo_confiable, nuevo_otp


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def accesos(request):
    enlace, error_titular = titular(request)
    if error_titular:
        return error_titular
    filas = IntentoAcceso.objects.filter(documento=enlace.cuenta.documento).order_by(
        "-fecha", "-pk")[:20]
    return JsonResponse([{"fecha": fila.fecha.isoformat(), "ip": fila.ip or "",
                          "dispositivo": fila.dispositivo, "exito": fila.exito}
                         for fila in filas], safe=False)


@api_view(["GET", "DELETE"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def dispositivos(request, dispositivo_id=None):
    enlace, error_titular = titular(request)
    if error_titular:
        return error_titular
    cliente = Cliente.objects.filter(cuenta_id=enlace.cuenta_id).first()
    if cliente is None:
        return JsonResponse([], safe=False) if request.method == "GET" else fallo(
            "no_encontrado", "Dispositivo no encontrado", 404)
    if request.method == "DELETE":
        eliminado = Dispositivo.objects.filter(pk=dispositivo_id, cliente=cliente).delete()[0]
        return HttpResponse(status=204) if eliminado else fallo(
            "no_encontrado", "Dispositivo no encontrado", 404)
    actual = dispositivo_confiable(request, cliente)
    return JsonResponse([{
        "id": d.pk, "nombre": d.nombre, "ultimo_uso": d.ultimo_uso.isoformat(),
        "confiable_hasta": d.confiable_hasta.isoformat() if d.confiable_hasta else None,
        "actual": actual is not None and d.pk == actual.pk,
    } for d in cliente.dispositivos.order_by("-ultimo_uso", "-pk")], safe=False)


@csrf_exigido
@api_view(["POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def pedir_cambio_correo(request):
    enlace, error_titular = titular(request)
    if error_titular:
        return error_titular
    correo = request.data.get("correo") if isinstance(request.data, dict) else None
    try:
        if not isinstance(correo, str):
            raise ValidationError("Correo inválido")
        correo = correo.strip().lower()
        validate_email(correo)
    except ValidationError:
        return fallo("validacion", "Correo inválido", 400)
    if Cliente.objects.filter(correo=correo).exists():
        return fallo("validacion", "Correo no disponible", 400)
    with transaction.atomic():
        cliente = Cliente.objects.select_for_update().filter(cuenta_id=enlace.cuenta_id).first()
        if cliente is None:
            return fallo("prohibido", "Cuenta sin identidad de cliente", 403)
        previo = CodigoOTP.objects.filter(
            cliente=cliente, proposito=CodigoOTP.Proposito.CORREO
        ).order_by("-pk").first()
        if previo and previo.creado > timezone.now() - timedelta(seconds=60):
            return fallo("bloqueo_temporal", "Espera antes de reenviar", 429,
                         reintentar_en=max(1, int((previo.creado + timedelta(seconds=60)
                                                    - timezone.now()).total_seconds()) + 1))
        nuevo_otp(cliente=cliente, correo=correo, proposito=CodigoOTP.Proposito.CORREO)
    return JsonResponse({"enviado": True})


@csrf_exigido
@api_view(["POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def confirmar_cambio_correo(request):
    enlace, error_titular = titular(request)
    if error_titular:
        return error_titular
    codigo = request.data.get("otp") if isinstance(request.data, dict) else None
    with transaction.atomic():
        cliente = Cliente.objects.select_for_update().select_related("cuenta", "usuario").filter(
            cuenta_id=enlace.cuenta_id).first()
        if cliente is None:
            return fallo("prohibido", "Cuenta sin identidad de cliente", 403)
        otp = CodigoOTP.objects.select_for_update().filter(
            cliente=cliente, proposito=CodigoOTP.Proposito.CORREO,
            usado=False).order_by("-pk").first()
        if otp is None:
            return fallo("otp_invalido", "Código inválido", 400)
        if otp.expira <= timezone.now():
            otp.usado = True
            otp.save(update_fields=["usado"])
            return fallo("otp_expirado", "Código vencido", 400)
        if not isinstance(codigo, str) or not otp.verificar_codigo(codigo):
            otp.intentos = min(otp.intentos + 1, 5)
            otp.usado = otp.intentos >= 5
            otp.save(update_fields=["intentos", "usado"])
            return fallo("otp_invalido", "Código inválido", 400)
        anterior = cliente.correo
        try:
            with transaction.atomic():
                cliente.correo = otp.correo_destino
                cliente.correo_verificado = True
                cliente.save(update_fields=["correo", "correo_verificado"])
        except IntegrityError:
            return fallo("validacion", "Correo no disponible", 400)
        cliente.usuario.email = cliente.correo
        cliente.usuario.save(update_fields=["email"])
        cliente.cuenta.correo = cliente.correo
        cliente.cuenta.correo_verificado_en = timezone.now()
        cliente.cuenta.save(update_fields=["correo", "correo_verificado_en"])
        otp.usado = True
        otp.save(update_fields=["usado"])
        notificar(anterior, "correo_cambiado", nombre=cliente.cuenta.nombre,
                 correo_nuevo=cliente.correo,
                 fecha=timezone.localtime().strftime("%d/%m/%Y %H:%M"))
    return JsonResponse({"ok": True})
