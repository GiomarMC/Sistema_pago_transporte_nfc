"""Primitivas de identidad: códigos persistentes, intentos y dispositivos."""
import hashlib
import ipaddress
import secrets
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.utils import timezone

from notificaciones.servicios import notificar

from .models import CodigoOTP, Dispositivo, IntentoAcceso

COOKIE_DISPOSITIVO = "sp_disp"
SELLO_DISPOSITIVO = "subepe-dispositivo"
SELLO_ACTIVACION = "subepe-activacion"


def huella(valor):
    return hashlib.sha256(valor.encode()).hexdigest()


def nombre_dispositivo(request):
    return (request.META.get("HTTP_USER_AGENT") or "Navegador")[:120]


def _ip_valida(valor):
    try:
        return str(ipaddress.ip_address(valor.strip()))
    except (AttributeError, ValueError):
        return None


def ip_cliente(request):
    # Detrás de Caddy (DJANGO_HTTPS=1) REMOTE_ADDR es la IP del contenedor de Caddy: con ella el límite de 20
    # fallos por IP sería uno solo para todos los clientes. Caddy, sin trusted_proxies, descarta la
    # X-Forwarded-For que mande el cliente y pone la IP que ve; se toma la última por si otro proxy la ampliara.
    if getattr(settings, "IP_DESDE_PROXY", False):
        reenviada = _ip_valida(request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[-1])
        if reenviada:
            return reenviada
    return _ip_valida(request.META.get("REMOTE_ADDR", ""))


def registrar_intento(request, documento, exito, motivo=""):
    return IntentoAcceso.objects.create(
        documento=documento[:20], ip=ip_cliente(request), exito=exito,
        motivo=motivo, dispositivo=nombre_dispositivo(request))


def segundos_bloqueo(documento, ip, *, minutos, max_documento, max_ip=None, motivo=None):
    desde = timezone.now() - timedelta(minutes=minutos)
    consulta = IntentoAcceso.objects.filter(exito=False, fecha__gte=desde)
    if motivo:
        consulta = consulta.filter(motivo__in=motivo)
    por_documento = consulta.filter(documento=documento).order_by("-fecha")
    fallo = list(por_documento.values_list("fecha", flat=True)[:max_documento])
    bloqueo = (fallo[-1] + timedelta(minutes=minutos) - timezone.now()).total_seconds() \
        if len(fallo) >= max_documento else 0
    if max_ip and ip:
        por_ip = list(consulta.filter(ip=ip).order_by("-fecha").values_list(
            "fecha", flat=True)[:max_ip])
        if len(por_ip) >= max_ip:
            bloqueo = max(bloqueo, (por_ip[-1] + timedelta(minutes=minutos)
                                    - timezone.now()).total_seconds())
    return max(0, int(bloqueo) + 1) if bloqueo > 0 else 0


def nuevo_otp(*, cliente=None, token_activacion=None, correo, proposito=None):
    proposito = proposito or (CodigoOTP.Proposito.INICIO if cliente
                             else CodigoOTP.Proposito.ACTIVACION)
    filtro = {"cliente": cliente} if cliente else {"token_activacion": token_activacion}
    CodigoOTP.objects.filter(proposito=proposito, usado=False, **filtro).update(usado=True)
    codigo = f"{secrets.randbelow(1_000_000):06d}"
    otp = CodigoOTP(proposito=proposito, correo_destino=correo, **filtro)
    otp.asignar_codigo(codigo)
    otp.save()
    notificar(correo, "otp", codigo=codigo, minutos=10)
    return otp


def id_dispositivo(request):
    valor = request.COOKIES.get(COOKIE_DISPOSITIVO)
    if not valor:
        return None
    try:
        identificador = signing.loads(valor, salt=SELLO_DISPOSITIVO,
                                       max_age=365 * 86400)
    except signing.BadSignature:
        return None
    return identificador if isinstance(identificador, str) and len(identificador) > 20 else None


def dispositivo_confiable(request, cliente):
    identificador = id_dispositivo(request)
    if identificador is None:
        return None
    return Dispositivo.objects.filter(
        cliente=cliente, huella=huella(identificador),
        confiable_hasta__gt=timezone.now()).first()


def recordar_dispositivo(request, respuesta, cliente):
    identificador = secrets.token_urlsafe(32)
    Dispositivo.objects.create(
        cliente=cliente, huella=huella(identificador),
        nombre=nombre_dispositivo(request),
        confiable_hasta=timezone.now() + timedelta(days=90))
    respuesta.set_cookie(
        COOKIE_DISPOSITIVO, signing.dumps(identificador, salt=SELLO_DISPOSITIVO),
        max_age=365 * 86400, httponly=True, secure=settings.SESSION_COOKIE_SECURE,
        samesite="Lax", path="/")
    return respuesta
