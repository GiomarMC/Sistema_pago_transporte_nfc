"""Endpoint HTTP de la sincronización v2 (M9-04): POST /api/v2/paquetes/.

El cuerpo son paquetes C1 concatenados (application/octet-stream, hasta 64 KB);
la respuesta, los paquetes del servidor también concatenados. Lo usa el
validador por WiFi (via = "http").
"""

from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from transporte import servicios
from transporte.permisos import EsValidador

from .paquetes import PaqueteInvalido, separar
from .servicio import procesar

TAM_MAXIMO = 64 * 1024
TIPO_BINARIO = "application/octet-stream"


class LimitePaquetes(UserRateThrottle):
    """60 peticiones por minuto por usuario. El contador vive en la caché por
    defecto (en archivos: la comparten los procesos de gunicorn)."""
    scope = "paquetes_v2"
    rate = "60/min"


def _error(estado, mensaje):
    return Response({"error": mensaje}, status=estado)


class Paquetes(APIView):
    permission_classes = [EsValidador]
    throttle_classes = [LimitePaquetes]

    def post(self, request):
        if request.META.get("CONTENT_TYPE", "").split(";")[0].strip().lower() != TIPO_BINARIO:
            return _error(415, f"el cuerpo debe ser {TIPO_BINARIO}")
        try:
            declarado = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            declarado = 0
        if declarado > TAM_MAXIMO:            # sin leerlo
            return _error(400, f"el cuerpo supera {TAM_MAXIMO // 1024} KB")
        cuerpo = request.body
        if len(cuerpo) > TAM_MAXIMO:
            return _error(400, f"el cuerpo supera {TAM_MAXIMO // 1024} KB")
        if not cuerpo:
            return _error(400, "el cuerpo está vacío")

        via, portador = "http", request.user.validador.numero
        try:
            paquetes = separar(cuerpo)
        except PaqueteInvalido as e:
            servicios.alertar(None, "paquete inválido",
                              f"flujo HTTP de {request.user}: {e} · vía {via} · {len(cuerpo)} B")
            return _error(400, f"flujo de paquetes mal formado: {e}")

        respuestas = []
        for datos in paquetes:
            respuestas += procesar(datos, via, portador)
        return HttpResponse(b"".join(respuestas), content_type=TIPO_BINARIO)
