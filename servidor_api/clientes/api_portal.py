"""Operaciones de sesión y perfil ya respaldadas por Django."""
from django.contrib.auth import logout
from django.http import HttpResponse, JsonResponse
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny

from pagos.api_cliente import titular

from .api_identidad import csrf_exigido, iniciar_sesion
from .api_resumen import datos_cliente


@csrf_exigido
@api_view(["GET", "POST", "DELETE"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def sesion(request):
    if request.method == "POST":
        return iniciar_sesion(request._request)
    enlace, fallo = titular(request)
    if fallo:
        return fallo
    if request.method == "DELETE":
        logout(request)
        return HttpResponse(status=204)
    return JsonResponse({"activa": True})


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def perfil(request):
    enlace, fallo = titular(request)
    if fallo:
        return fallo
    cuenta = enlace.cuenta
    datos, cliente = datos_cliente(cuenta)
    return JsonResponse({**datos,
                         "creado": (cliente.creado if cliente else cuenta.creada).isoformat()})
