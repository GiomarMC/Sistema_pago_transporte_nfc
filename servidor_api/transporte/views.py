from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from . import servicios
from .models import Cuenta, Recarga, Tarjeta
from .permisos import EsOperador, EsValidador
from .serializers import (BloqueoSerializer, ConfirmarEmisionSerializer, CuentaSalida,
                          EmisionSerializer, EntregaSerializer, EventoSerializer,
                          MovimientoSalida, RecargaSalida, RecargaSerializer, SyncSerializer,
                          TarjetaSalida)


def _conflicto(e):
    return Response({"error": str(e)}, status=status.HTTP_409_CONFLICT)


# --- Validadores ----------------------------------------------------------------

@api_view(["POST"])
@permission_classes([EsValidador])
def sync(request):
    """Sube eventos del validador; devuelve lista negra, recargas pendientes y tarifas.
    El número de validador sale del token, no del cuerpo de la petición."""
    entrada = SyncSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    eventos = []
    for ev in entrada.validated_data["eventos"]:
        s = EventoSerializer(data=ev)
        if s.is_valid():
            datos = dict(s.validated_data)
            datos["fecha"] = datos["fecha"].isoformat()
            eventos.append(datos)
        else:  # se procesa igual para dejar alerta y confirmarlo
            eventos.append({**ev, "tipo": "invalido"})
    return Response(servicios.sincronizar(request.user.validador, eventos))


# --- Emisión -----------------------------------------------------------------

@api_view(["POST"])
@permission_classes([EsOperador])
def emision_reservar(request):
    entrada = EmisionSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    d = entrada.validated_data
    try:
        tarjeta = servicios.reservar_emision(
            d["uid"], id_cuenta=d.get("cuenta"), nombre=d.get("nombre"),
            documento=d.get("documento"), codigo_tarifa=d.get("tarifa"))
    except Cuenta.DoesNotExist:
        return Response({"error": f"no existe la cuenta {d.get('cuenta')}"}, status=404)
    except servicios.ErrorNegocio as e:
        return _conflicto(e)
    tarjeta.refresh_from_db()
    return Response({
        "tarjeta": tarjeta.id,
        "cuenta": CuentaSalida(tarjeta.cuenta).data,
        # Lo que el cliente debe grabar en la tarjeta:
        "saldo": tarjeta.cuenta.saldo,
        "recarga_inicial": tarjeta.recarga_inicial,
        "tarifa": tarjeta.cuenta.tarifa_id,
    }, status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([EsOperador])
def emision_confirmar(request, id_tarjeta):
    entrada = ConfirmarEmisionSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    try:
        tarjeta, reemplazadas = servicios.confirmar_emision(
            id_tarjeta, entrada.validated_data.get("contador"))
    except servicios.ErrorNegocio as e:
        return _conflicto(e)
    return Response({"tarjeta": TarjetaSalida(tarjeta).data,
                     "bloqueadas": [{"id": t.id, "uid": t.uid} for t in reemplazadas]})


@api_view(["POST"])
@permission_classes([EsOperador])
def emision_cancelar(request, id_tarjeta):
    try:
        servicios.cancelar_emision(id_tarjeta)
    except servicios.ErrorNegocio as e:
        return _conflicto(e)
    return Response(status=status.HTTP_204_NO_CONTENT)


# --- Tarjetas y cuentas ----------------------------------------------------------

@api_view(["GET"])
@permission_classes([EsOperador])
def tarjeta_detalle(request, id_tarjeta):
    tarjeta = get_object_or_404(Tarjeta.objects.select_related("cuenta__tarifa"), pk=id_tarjeta)
    pendientes = Recarga.objects.filter(cuenta_id=tarjeta.cuenta_id, aplicada_en__isnull=True)
    return Response({**TarjetaSalida(tarjeta).data,
                     "recargas_pendientes": RecargaSalida(pendientes, many=True).data})


@api_view(["POST"])
@permission_classes([EsOperador])
def tarjeta_bloquear(request, id_tarjeta):
    entrada = BloqueoSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    tarjeta = get_object_or_404(Tarjeta, pk=id_tarjeta)
    if tarjeta.estado != Tarjeta.Estado.ACTIVA:
        return _conflicto(f"la tarjeta ya está {tarjeta.estado}")
    servicios.bloquear(tarjeta, entrada.validated_data["motivo"], tipo="bloqueo manual")
    return Response(TarjetaSalida(tarjeta).data)


@api_view(["POST"])
@permission_classes([EsOperador])
def tarjeta_anular_uid(request, uid):
    """La tarjeta física se restableció a fábrica."""
    return Response({"anuladas": servicios.anular_por_uid(uid.upper())})


@api_view(["GET"])
@permission_classes([EsOperador])
def cuentas_buscar(request):
    documento = request.query_params.get("documento")
    if not documento:
        return Response({"error": "indica ?documento="}, status=400)
    cuenta = get_object_or_404(Cuenta, documento=documento)
    return Response(_cuenta_con_detalle(cuenta))


@api_view(["GET"])
@permission_classes([EsOperador])
def cuenta_detalle(request, id_cuenta):
    return Response(_cuenta_con_detalle(get_object_or_404(Cuenta, pk=id_cuenta)))


def _cuenta_con_detalle(cuenta):
    return {**CuentaSalida(cuenta).data,
            "tarjetas": [{"id": t.id, "uid": t.uid, "estado": t.estado}
                         for t in cuenta.tarjetas.order_by("id")],
            "movimientos": MovimientoSalida(cuenta.movimientos.all()[:20], many=True).data}


@api_view(["POST"])
@permission_classes([EsOperador])
def cuenta_bloquear(request, id_cuenta):
    entrada = BloqueoSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    cuenta = get_object_or_404(Cuenta, pk=id_cuenta)
    activas = list(cuenta.tarjetas.filter(estado=Tarjeta.Estado.ACTIVA))
    for t in activas:
        servicios.bloquear(t, entrada.validated_data["motivo"], tipo="bloqueo manual")
    return Response({"bloqueadas": [{"id": t.id, "uid": t.uid} for t in activas]})


# --- Recargas ------------------------------------------------------------------

@api_view(["POST"])
@permission_classes([EsOperador])
def recargas(request):
    entrada = RecargaSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    d = entrada.validated_data

    if "documento" in d:  # remota (Yape, web...)
        cuenta = get_object_or_404(Cuenta, documento=d["documento"])
        try:
            recarga = servicios.crear_recarga(cuenta.id, d["monto"], d["origen"])
        except servicios.ErrorNegocio as e:
            return _conflicto(e)
        cuenta.refresh_from_db()
        return Response({"recarga": RecargaSalida(recarga).data,
                         "cuenta": CuentaSalida(cuenta).data,
                         "tiene_tarjeta_activa": cuenta.tarjetas.filter(
                             estado=Tarjeta.Estado.ACTIVA).exists()},
                        status=status.HTTP_201_CREATED)

    # Punto de recarga: la tarjeta está en el lector
    tarjeta = get_object_or_404(Tarjeta, pk=d["tarjeta"])
    if tarjeta.estado != Tarjeta.Estado.ACTIVA or tarjeta.uid != d["uid"].upper():
        return _conflicto("tarjeta no activa: no se recarga (el saldo se recupera emitiendo "
                          "una tarjeta nueva para la cuenta)")
    recarga = None
    if d["monto"]:
        try:
            recarga = servicios.crear_recarga(tarjeta.cuenta_id, d["monto"], "punto")
        except servicios.ErrorNegocio as e:
            return _conflicto(e)
    abono, hasta = servicios.recargas_para_tarjeta(tarjeta.cuenta_id, d["recarga_en_tarjeta"])
    tarjeta.cuenta.refresh_from_db()
    return Response({"recarga": RecargaSalida(recarga).data if recarga else None,
                     "abono": abono, "hasta_seq": hasta,
                     "cuenta": CuentaSalida(tarjeta.cuenta).data},
                    status=status.HTTP_201_CREATED if recarga else status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([EsOperador])
def recargas_entrega(request):
    """El punto de recarga confirma que grabó las recargas en la tarjeta."""
    entrada = EntregaSerializer(data=request.data)
    entrada.is_valid(raise_exception=True)
    d = entrada.validated_data
    tarjeta = get_object_or_404(Tarjeta, pk=d["tarjeta"])
    servicios.registrar_entrega_en_punto(tarjeta, d["hasta_seq"], d["operacion"],
                                         d.get("contador"), d.get("recarga"))
    return Response({"ok": True})
