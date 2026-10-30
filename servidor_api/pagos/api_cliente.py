from django.http import JsonResponse
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny

from .models import ClienteCuenta, OrdenRecarga
from .servicios import ErrorPago, crear_orden


def error(codigo, mensaje, estado, campos=None):
    return JsonResponse({"error": {"codigo": codigo, "mensaje": mensaje,
                                   "campos": campos or {}}}, status=estado)


def salida(orden, detalle=False):
    if detalle:
        return {"id": str(orden.pk), "estado": orden.estado, "monto": orden.monto,
                "creada": orden.creada.isoformat(),
                "pagada_en": orden.pagada_en.isoformat() if orden.pagada_en else None,
                "acreditada_en": orden.acreditada_en.isoformat() if orden.acreditada_en else None}
    return {"id": str(orden.pk), "estado": orden.estado, "monto": orden.monto,
            "url_pago": orden.url_pago, "expira": orden.expira.isoformat()}


def titular(request):
    if not request.user.is_authenticated:
        return None, error("no_autenticado", "Inicia sesión", 401)
    try:
        return ClienteCuenta.objects.select_related("cuenta").get(usuario=request.user), None
    except ClienteCuenta.DoesNotExist:
        return None, error("prohibido", "No hay cuenta de cliente asociada", 403)


@api_view(["GET", "POST"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def ordenes(request):
    cliente, fallo = titular(request)
    if fallo:
        return fallo
    if request.method == "GET":
        return JsonResponse([salida(o, True) for o in OrdenRecarga.objects.filter(
            usuario=request.user, cuenta=cliente.cuenta).order_by("-creada")], safe=False)
    clave = request.headers.get("Idempotency-Key")
    if not clave:
        return error("validacion", "Falta Idempotency-Key", 400, {"Idempotency-Key": ["Obligatoria"]})
    monto = request.data.get("monto") if isinstance(request.data, dict) else None
    try:
        orden = crear_orden(request.user, cliente.cuenta, monto, clave,
                            url_retorno=request.build_absolute_uri("/api/v1/recargas/ordenes/"))
    except ErrorPago as exc:
        return error(exc.codigo, str(exc), 409 if exc.codigo == "idempotencia_conflicto" else 400)
    return JsonResponse(salida(orden), status=201)


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def detalle(request, orden_id):
    cliente, fallo = titular(request)
    if fallo:
        return fallo
    orden = OrdenRecarga.objects.filter(pk=orden_id, usuario=request.user,
                                        cuenta=cliente.cuenta).first()
    if not orden:
        return error("no_encontrado", "Orden no encontrada", 404)
    return JsonResponse(salida(orden, True))
