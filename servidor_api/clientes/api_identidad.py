"""Activación y acceso del cliente según C4; ningún OTP se persiste en claro."""
from django.contrib.auth import authenticate, get_user_model, login
from datetime import timedelta
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny

from notificaciones.servicios import notificar
from pagos.models import ClienteCuenta
from transporte.models import Tarjeta

from .models import Cliente, CodigoActivacion, CodigoOTP, numero_impreso, usuario_cliente
from .servicios import (SELLO_ACTIVACION, dispositivo_confiable, huella, ip_cliente,
                        nombre_dispositivo, nuevo_otp, recordar_dispositivo,
                        registrar_intento, segundos_bloqueo)


def csrf_exigido(vista):
    """DRF marca sus vistas como exentas; este envoltorio restaura la protección."""
    protegida = csrf_protect(vista)
    protegida.csrf_exempt = False
    return protegida


def fallo(codigo, mensaje, estado, **extra):
    return JsonResponse({"error": {"codigo": codigo, "mensaje": mensaje,
                                   "campos": {}, **extra}}, status=estado)


def _datos(request):
    return request.data if isinstance(request.data, dict) else {}


def _activacion(token):
    try:
        datos = signing.loads(token, salt=SELLO_ACTIVACION, max_age=900)
    except (signing.BadSignature, TypeError, ValueError):
        return None
    if not isinstance(datos, dict) or not all(k in datos for k in ("codigo", "tarjeta", "documento")):
        return None
    registro = CodigoActivacion.objects.select_for_update().select_related(
        "tarjeta__cuenta").filter(pk=datos["codigo"], tarjeta_id=datos["tarjeta"]).first()
    if (registro is None or registro.usado_en or registro.anulado_en
            or registro.expira <= timezone.now() or registro.tarjeta.estado != Tarjeta.Estado.ACTIVA
            or registro.tarjeta.cuenta.documento != datos["documento"]
            or Cliente.objects.filter(cuenta_id=registro.tarjeta.cuenta_id).exists()):
        return None
    return registro


@csrf_exigido
@api_view(["POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def activar_identidad(request):
    datos = _datos(request)
    documento = datos.get("documento", "")
    numero = str(datos.get("numero_tarjeta", "")).replace(" ", "")
    codigo = datos.get("codigo", "")
    if not isinstance(documento, str):
        documento = ""
    documento = documento.strip()
    espera = segundos_bloqueo(documento, ip_cliente(request), minutos=30,
                              max_documento=5, motivo=("activacion",))
    if espera:
        return fallo("bloqueo_temporal", "Intenta más tarde", 429, reintentar_en=espera)
    with transaction.atomic():
        tarjeta = None
        if numero.isdigit() and len(numero) == 9:
            tarjeta = Tarjeta.objects.select_for_update().select_related("cuenta").filter(
                pk=int(numero[:8]), estado=Tarjeta.Estado.ACTIVA).first()
        registro = (CodigoActivacion.objects.select_for_update().filter(
            tarjeta=tarjeta, usado_en__isnull=True, anulado_en__isnull=True).first()
            if tarjeta else None)
        valido = (len(documento) == 8 and documento.isdigit() and tarjeta is not None
                  and numero_impreso(tarjeta).replace(" ", "") == numero
                  and tarjeta.cuenta.documento == documento and isinstance(codigo, str)
                  and registro is not None and registro.vigente(codigo))
        if not valido:
            if registro is not None and registro.intentos < 65535:
                registro.intentos += 1
                registro.save(update_fields=["intentos"])
            registrar_intento(request, documento, False, "activacion")
            espera = segundos_bloqueo(documento, ip_cliente(request), minutos=30,
                                      max_documento=5, motivo=("activacion",))
            if espera:
                return fallo("bloqueo_temporal", "Intenta más tarde", 429, reintentar_en=espera)
            return fallo("activacion_invalida", "Datos de activación inválidos", 400)
        token = signing.dumps({"codigo": registro.pk, "tarjeta": tarjeta.pk,
                               "documento": documento}, salt=SELLO_ACTIVACION)
    return JsonResponse({"token_activacion": token})


@csrf_exigido
@api_view(["POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def activar_correo(request):
    datos = _datos(request)
    token = datos.get("token_activacion")
    correo = datos.get("correo")
    try:
        if not isinstance(correo, str):
            raise ValidationError("Correo inválido")
        correo = correo.strip().lower()
        validate_email(correo)
    except ValidationError:
        return fallo("validacion", "Correo inválido", 400)
    with transaction.atomic():
        registro = _activacion(token)
        if registro is None:
            return fallo("activacion_invalida", "Activación inválida o vencida", 400)
        if Cliente.objects.filter(correo=correo).exists():
            return fallo("validacion", "Correo no disponible", 400)
        previo = CodigoOTP.objects.filter(
            token_activacion=huella(token), proposito=CodigoOTP.Proposito.ACTIVACION
        ).order_by("-pk").first()
        if previo and previo.creado > timezone.now() - timedelta(seconds=60):
            return fallo("bloqueo_temporal", "Espera antes de reenviar", 429,
                         reintentar_en=max(1, int((previo.creado + timedelta(seconds=60)
                                                    - timezone.now()).total_seconds()) + 1))
        nuevo_otp(token_activacion=huella(token), correo=correo)
    return JsonResponse({"enviado": True})


@csrf_exigido
@api_view(["POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def activar_confirmar(request):
    datos = _datos(request)
    token, codigo, clave = (datos.get("token_activacion"), datos.get("otp"),
                            datos.get("contrasena"))
    if not isinstance(clave, str) or len(clave) < 10:
        return fallo("validacion", "Contraseña de al menos 10 caracteres", 400)
    with transaction.atomic():
        registro = _activacion(token)
        if registro is None:
            return fallo("activacion_invalida", "Activación inválida o vencida", 400)
        usuario = get_user_model()(username=usuario_cliente(registro.tarjeta.cuenta.documento))
        try:
            validate_password(clave, user=usuario)
        except ValidationError as exc:
            return fallo("validacion", "; ".join(exc.messages), 400)
        otp = CodigoOTP.objects.select_for_update().filter(
            token_activacion=huella(token), proposito=CodigoOTP.Proposito.ACTIVACION,
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
        try:
            with transaction.atomic():
                usuario.email = otp.correo_destino
                usuario.set_password(clave)
                usuario.save()
                cliente = Cliente.objects.create(
                    usuario=usuario, cuenta=registro.tarjeta.cuenta,
                    correo=otp.correo_destino, correo_verificado=True)
                ClienteCuenta.objects.create(usuario=usuario, cuenta=cliente.cuenta)
                cliente.cuenta.correo = otp.correo_destino
                cliente.cuenta.correo_verificado_en = timezone.now()
                cliente.cuenta.save(update_fields=["correo", "correo_verificado_en"])
        except IntegrityError:
            return fallo("activacion_invalida", "Cuenta ya activada", 400)
        otp.usado = True
        otp.save(update_fields=["usado"])
        registro.usado_en = timezone.now()
        registro.save(update_fields=["usado_en"])
        notificar(cliente.correo, "activacion_completa", nombre=cliente.cuenta.nombre)
    login(request, usuario, backend="django.contrib.auth.backends.ModelBackend")
    request.session.set_expiry(900)
    respuesta = JsonResponse({"ok": True}, status=201)
    return recordar_dispositivo(request, respuesta, cliente)


@csrf_exigido
@api_view(["POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def iniciar_sesion(request):
    datos = _datos(request)
    documento, clave = datos.get("documento"), datos.get("contrasena")
    documento = documento.strip() if isinstance(documento, str) else ""
    ip = ip_cliente(request)
    espera = segundos_bloqueo(documento, ip, minutos=15, max_documento=5,
                              max_ip=20, motivo=("clave", "otp"))
    if espera:
        return fallo("bloqueo_temporal", "Intenta más tarde", 429, reintentar_en=espera)
    usuario = (authenticate(request, username=usuario_cliente(documento), password=clave)
               if isinstance(clave, str) else None)
    cliente = (Cliente.objects.select_related("cuenta").filter(usuario=usuario).first()
               if usuario else None)
    if cliente is None or not cliente.correo_verificado:
        registrar_intento(request, documento, False, "clave")
        espera = segundos_bloqueo(documento, ip, minutos=15, max_documento=5,
                                  max_ip=20, motivo=("clave", "otp"))
        if espera:
            return fallo("bloqueo_temporal", "Intenta más tarde", 429, reintentar_en=espera)
        return fallo("credenciales_invalidas", "Credenciales inválidas", 401)
    dispositivo = dispositivo_confiable(request, cliente)
    if dispositivo:
        dispositivo.ultimo_uso = timezone.now()
        dispositivo.save(update_fields=["ultimo_uso"])
        login(request, usuario)
        request.session.set_expiry(900)
        registrar_intento(request, documento, True, "dispositivo confiable")
        return JsonResponse({"requiere_otp": False})
    enviado = request.session.get("m12_otp_enviado_en", 0)
    if (request.session.get("m12_otp_cliente_id") == cliente.pk
            and timezone.now().timestamp() - enviado < 60):
        return JsonResponse({"requiere_otp": True})
    with transaction.atomic():
        Cliente.objects.select_for_update().get(pk=cliente.pk)
        nuevo_otp(cliente=cliente, correo=cliente.correo)
    request.session["m12_otp_cliente_id"] = cliente.pk
    request.session["m12_otp_enviado_en"] = timezone.now().timestamp()
    request.session.set_expiry(600)
    return JsonResponse({"requiere_otp": True})


@csrf_exigido
@api_view(["POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def confirmar_otp(request):
    cliente_id = request.session.get("m12_otp_cliente_id")
    if not cliente_id:
        return fallo("otp_invalido", "Código inválido", 400)
    codigo = _datos(request).get("otp")
    with transaction.atomic():
        cliente = Cliente.objects.select_for_update().select_related("usuario", "cuenta").filter(
            pk=cliente_id).first()
        if cliente is None:
            return fallo("otp_invalido", "Código inválido", 400)
        otp = CodigoOTP.objects.select_for_update().filter(
            cliente=cliente, proposito=CodigoOTP.Proposito.INICIO,
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
            registrar_intento(request, cliente.cuenta.documento, False, "otp")
            return fallo("otp_invalido", "Código inválido", 400)
        otp.usado = True
        otp.save(update_fields=["usado"])
    login(request, cliente.usuario, backend="django.contrib.auth.backends.ModelBackend")
    request.session.pop("m12_otp_cliente_id", None)
    request.session.pop("m12_otp_enviado_en", None)
    request.session.set_expiry(900)
    registrar_intento(request, cliente.cuenta.documento, True, "otp")
    notificar(cliente.correo, "nuevo_inicio_sesion", nombre=cliente.cuenta.nombre,
             fecha=timezone.localtime().strftime("%d/%m/%Y %H:%M"),
             dispositivo=nombre_dispositivo(request))
    respuesta = JsonResponse({"ok": True})
    if _datos(request).get("recordar_dispositivo") is True:
        recordar_dispositivo(request, respuesta, cliente)
    return respuesta


@csrf_exigido
@api_view(["POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def reenviar_otp(request):
    cliente_id = request.session.get("m12_otp_cliente_id")
    if not cliente_id:
        return fallo("otp_invalido", "No hay acceso pendiente", 400)
    enviado = request.session.get("m12_otp_enviado_en", 0)
    restante = 60 - (timezone.now().timestamp() - enviado)
    if restante > 0:
        return fallo("bloqueo_temporal", "Espera antes de reenviar", 429,
                     reintentar_en=int(restante) + 1)
    with transaction.atomic():
        cliente = Cliente.objects.select_for_update().filter(pk=cliente_id).first()
        if cliente is None:
            return fallo("otp_invalido", "No hay acceso pendiente", 400)
        nuevo_otp(cliente=cliente, correo=cliente.correo)
    request.session["m12_otp_enviado_en"] = timezone.now().timestamp()
    return JsonResponse({"ok": True})
