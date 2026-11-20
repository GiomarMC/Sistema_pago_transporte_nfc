"""Consultas de la cuenta propia para el portal."""
from datetime import date

from django.db.models import Case, IntegerField, Sum, When
from django.http import JsonResponse
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny

from pagos.api_cliente import error, titular
from transporte.models import Movimiento, Recarga, Tarjeta

from .models import Cliente, numero_impreso


def datos_cliente(cuenta):
    cliente = Cliente.objects.filter(cuenta_id=cuenta.pk).first()
    return ({"nombre": cuenta.nombre,
             "documento_enmascarado": "****" + cuenta.documento[-4:],
             "correo": cliente.correo if cliente else cuenta.correo}, cliente)


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def resumen(request):
    enlace, fallo = titular(request)
    if fallo:
        return fallo
    cuenta = enlace.cuenta
    identidad, _ = datos_cliente(cuenta)
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
        "cliente": identidad,
        "cuenta": {"id": cuenta.pk, "saldo": cuenta.saldo,
                   "por_cargar_en_tarjeta": pendientes,
                   "tarifa": {"codigo": cuenta.tarifa_id,
                              "nombre": cuenta.tarifa.nombre,
                              "precio": cuenta.tarifa.precio}},
        "tarjetas": salida_tarjetas,
    })


@api_view(["GET"])
@authentication_classes([SessionAuthentication])
@permission_classes([AllowAny])
def movimientos(request):
    enlace, fallo = titular(request)
    if fallo:
        return fallo
    tipo = request.query_params.get("tipo", "")
    if tipo not in ("", "viaje", "recarga", "emision", "ajuste"):
        return error("validacion", "Tipo de movimiento inválido", 400)
    try:
        pagina = int(request.query_params.get("pagina", "1"))
        desde = date.fromisoformat(request.query_params["desde"]) if request.query_params.get("desde") else None
        hasta = date.fromisoformat(request.query_params["hasta"]) if request.query_params.get("hasta") else None
    except (ValueError, TypeError):
        return error("validacion", "Fecha o página inválida", 400)
    if pagina < 1 or (desde and hasta and desde > hasta):
        return error("validacion", "Rango o página inválida", 400)
    consulta = Movimiento.objects.filter(cuenta_id=enlace.cuenta_id)
    if tipo:
        consulta = consulta.filter(tipo=tipo)
    if desde:
        consulta = consulta.filter(fecha__date__gte=desde)
    if hasta:
        consulta = consulta.filter(fecha__date__lte=hasta)
    consulta = consulta.order_by("-fecha", "-pk")
    total = consulta.count()
    paginas = max(1, (total + 19) // 20)
    if pagina > paginas:
        return error("validacion", "Página fuera de rango", 400)
    filas = consulta[(pagina - 1) * 20:pagina * 20]
    nombres = {"recarga": "Recarga", "emision": "Emisión", "ajuste": "Ajuste",
               "reverso": "Reverso"}
    resultados = []
    for fila in filas:
        descripcion = (f"Viaje · bus V{fila.validador}" if fila.validador else "Viaje") \
            if fila.tipo == "viaje" else nombres.get(fila.tipo, fila.tipo.capitalize())
        resultados.append({"id": fila.pk, "fecha": fila.fecha.isoformat(),
                           "tipo": fila.tipo, "monto": fila.monto,
                           "saldo_final": fila.saldo_final, "descripcion": descripcion,
                           "validador": f"V{fila.validador}" if fila.validador else None})
    return JsonResponse({"resultados": resultados, "pagina": pagina, "paginas": paginas})
