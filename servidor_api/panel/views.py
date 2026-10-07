from datetime import datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.dateparse import parse_date

from transporte import servicios
from transporte.models import Alerta, Cuenta, Movimiento, Recarga, Tarjeta, Validador
from transporte.permisos import GRUPO_OPERADORES, GRUPO_SOLICITUDES

from .auth import operador_requerido, superusuario_requerido
from .forms import SolicitudAccesoForm

TAM_PAGINA = 30
MINUTOS_SIN_SYNC = 5
# Tope de solicitudes sin revisar: el registro es público y así no se llena de cuentas basura
MAX_SOLICITUDES_PENDIENTES = 50


def _cursor(request):
    valor = request.GET.get("antes", "")
    if not valor:
        return None
    if not valor.isdecimal() or len(valor) > 18:
        return None
    return int(valor)


def _pagina(queryset, limite=TAM_PAGINA):
    elementos = list(queryset[: limite + 1])
    hay_mas = len(elementos) > limite
    elementos = elementos[:limite]
    return elementos, elementos[-1].pk if hay_mas and elementos else None


def _estado_validador(validador, ahora):
    if not validador.activo:
        return "desactivado", "neutral"
    if validador.ultima_sync is None:
        return "sin primera conexión", "warning"
    if validador.ultima_sync < ahora - timedelta(minutes=MINUTOS_SIN_SYNC):
        return "sin sincronizar", "danger"
    return "en línea", "success"


@operador_requerido
def inicio(request):
    ahora = timezone.now()
    inicio_dia = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    viajes_hoy = Movimiento.objects.filter(tipo="viaje", fecha__gte=inicio_dia)
    resumen_viajes = viajes_hoy.aggregate(total=Count("id"), cobrado=Sum("monto"))
    corte = ahora - timedelta(minutes=MINUTOS_SIN_SYNC)

    desde = inicio_dia - timedelta(days=6)
    por_dia = {
        fila["dia"]: fila["total"]
        for fila in Movimiento.objects.filter(tipo="viaje", fecha__gte=desde)
        .order_by()
        .annotate(dia=TruncDate("fecha", tzinfo=timezone.get_current_timezone()))
        .values("dia")
        .annotate(total=Count("id"))
    }
    dias = [timezone.localdate() - timedelta(days=n) for n in range(6, -1, -1)]
    maximo = max([por_dia.get(d, 0) for d in dias] + [1])
    grafico = [
        {"fecha": d, "total": por_dia.get(d, 0), "alto": max(5, round(por_dia.get(d, 0) * 100 / maximo))}
        for d in dias
    ]

    contexto = {
        "titulo": "Centro de operaciones",
        "actualizado": ahora,
        "cuentas": Cuenta.objects.count(),
        "tarjetas_activas": Tarjeta.objects.filter(estado=Tarjeta.Estado.ACTIVA).count(),
        "viajes_hoy": resumen_viajes["total"],
        "cobrado_hoy": -(resumen_viajes["cobrado"] or 0),
        "recargas_pendientes": Recarga.objects.filter(aplicada_en__isnull=True).count(),
        "alertas_abiertas": Alerta.objects.filter(revisada=False).count(),
        "validadores_atrasados": Validador.objects.filter(activo=True).filter(
            Q(ultima_sync__lt=corte) | Q(ultima_sync__isnull=True)
        ).count(),
        "viajes_recientes": Movimiento.objects.filter(tipo="viaje")
        .select_related("cuenta", "tarjeta")[:6],
        "alertas_recientes": Alerta.objects.filter(revisada=False)
        .select_related("tarjeta")[:4],
        "grafico": grafico,
    }
    return render(request, "panel/inicio.html", contexto)


@operador_requerido
def cuentas(request):
    consulta = request.GET.get("q", "").strip()[:64]
    resultados = []
    if consulta:
        filtro = Q(documento=consulta) | Q(tarjetas__uid=consulta.upper())
        if consulta.isdecimal() and len(consulta) <= 18:
            filtro |= Q(pk=int(consulta))
        resultados = list(Cuenta.objects.filter(filtro).select_related("tarifa").distinct()[:20])
    return render(request, "panel/cuentas.html", {
        "titulo": "Buscar cuentas", "consulta": consulta, "resultados": resultados,
    })


@operador_requerido
def cuenta_detalle(request, cuenta_id):
    cuenta = get_object_or_404(Cuenta.objects.select_related("tarifa"), pk=cuenta_id)
    return render(request, "panel/cuenta_detalle.html", {
        "titulo": f"Cuenta {cuenta.pk}",
        "cuenta": cuenta,
        "tarjetas": cuenta.tarjetas.order_by("-id"),
        "movimientos": cuenta.movimientos.select_related("tarjeta")[:20],
        "recargas_pendientes": cuenta.recargas.filter(aplicada_en__isnull=True).order_by("seq")[:10],
    })


@operador_requerido
@require_POST
def bloquear_tarjeta(request, tarjeta_id):
    motivo = request.POST.get("motivo", "").strip()[:200]
    if not motivo:
        return HttpResponseBadRequest("Indica el motivo del bloqueo.")
    with transaction.atomic():
        tarjeta = get_object_or_404(Tarjeta.objects.select_for_update(), pk=tarjeta_id)
        if tarjeta.estado != Tarjeta.Estado.ACTIVA:
            messages.warning(request, f"La tarjeta {tarjeta.pk} ya no está activa.")
        else:
            servicios.bloquear(tarjeta, motivo, tipo="bloqueo manual desde el panel")
            messages.success(request, f"Tarjeta {tarjeta.pk} bloqueada. Los validadores la recibirán al sincronizar.")
    return redirect("panel:cuenta_detalle", cuenta_id=tarjeta.cuenta_id)


@operador_requerido
def viajes(request):
    consulta = request.GET.get("q", "").strip()[:64]
    numero = request.GET.get("validador", "").strip()
    fecha_texto = request.GET.get("fecha", "").strip()
    fecha = parse_date(fecha_texto) if fecha_texto else None
    qs = Movimiento.objects.filter(tipo="viaje").select_related("cuenta", "tarjeta")
    if consulta:
        qs = qs.filter(Q(cuenta__documento=consulta) | Q(tarjeta__uid=consulta.upper()))
    if numero.isdecimal() and len(numero) <= 3:
        qs = qs.filter(validador=int(numero))
    if fecha:
        inicio_fecha = timezone.make_aware(datetime.combine(fecha, time.min))
        qs = qs.filter(fecha__gte=inicio_fecha, fecha__lt=inicio_fecha + timedelta(days=1))
    cursor = _cursor(request)
    if cursor is not None:
        qs = qs.filter(pk__lt=cursor)
    elementos, siguiente = _pagina(qs.order_by("-id"))
    return render(request, "panel/viajes.html", {
        "titulo": "Viajes recibidos", "viajes": elementos, "siguiente": siguiente,
        "consulta": consulta, "numero": numero, "fecha_texto": fecha_texto,
        "fecha_invalida": bool(fecha_texto and not fecha),
    })


@operador_requerido
def validadores(request):
    ahora = timezone.now()
    lista = [
        {"objeto": v, "estado": _estado_validador(v, ahora)}
        for v in Validador.objects.order_by("numero")
    ]
    return render(request, "panel/validadores.html", {
        "titulo": "Validadores", "validadores": lista,
    })


@operador_requerido
def alertas(request):
    estado = request.GET.get("estado", "abiertas")
    if estado not in {"abiertas", "todas"}:
        estado = "abiertas"
    qs = Alerta.objects.select_related("tarjeta", "tarjeta__cuenta", "revisada_por")
    if estado == "abiertas":
        qs = qs.filter(revisada=False)
    cursor = _cursor(request)
    if cursor is not None:
        qs = qs.filter(pk__lt=cursor)
    elementos, siguiente = _pagina(qs.order_by("-id"))
    return render(request, "panel/alertas.html", {
        "titulo": "Alertas", "alertas": elementos, "siguiente": siguiente, "estado": estado,
    })


@operador_requerido
@require_POST
def revisar_alerta(request, alerta_id):
    alerta = get_object_or_404(Alerta, pk=alerta_id)
    if not alerta.revisada:
        Alerta.objects.filter(pk=alerta_id, revisada=False).update(
            revisada=True, revisada_en=timezone.now(), revisada_por=request.user,
        )
        messages.success(request, f"Alerta {alerta_id} marcada como revisada.")
    return redirect("panel:alertas")


def solicitar_acceso(request):
    if request.user.is_authenticated:
        return redirect("panel:inicio")
    pendientes = get_user_model().objects.filter(groups__name=GRUPO_SOLICITUDES).count()
    cerrado = pendientes >= MAX_SOLICITUDES_PENDIENTES
    form = SolicitudAccesoForm(request.POST or None)
    if request.method == "POST" and not cerrado and form.is_valid():
        with transaction.atomic():
            usuario = form.save()
            usuario.groups.add(Group.objects.get(name=GRUPO_SOLICITUDES))
        login(request, usuario)
        return redirect("panel:inicio")
    return render(request, "panel/registro.html", {"form": form, "cerrado": cerrado})


@superusuario_requerido
def usuarios(request):
    User = get_user_model()
    pendientes = User.objects.filter(groups__name=GRUPO_SOLICITUDES).order_by("date_joined")
    con_acceso = (
        User.objects.filter(Q(groups__name=GRUPO_OPERADORES) | Q(is_staff=True))
        .distinct().order_by("-is_superuser", "username")
    )
    return render(request, "panel/usuarios.html", {
        "titulo": "Usuarios", "pendientes": pendientes, "con_acceso": con_acceso,
    })


@superusuario_requerido
@require_POST
def gestionar_usuario(request, usuario_id):
    accion = request.POST.get("accion", "")
    with transaction.atomic():
        usuario = get_object_or_404(get_user_model().objects.select_for_update(), pk=usuario_id)
        pendiente = usuario.groups.filter(name=GRUPO_SOLICITUDES).exists()
        if accion in {"aprobar", "rechazar"} and not pendiente:
            messages.warning(request, f"La solicitud de {usuario.username} ya fue resuelta.")
        elif accion == "aprobar":
            usuario.groups.remove(Group.objects.get(name=GRUPO_SOLICITUDES))
            usuario.groups.add(Group.objects.get(name=GRUPO_OPERADORES))
            messages.success(request, f"{usuario.username} ya puede entrar al panel.")
        elif accion == "rechazar":
            usuario.delete()
            messages.success(request, f"Solicitud de {usuario.username} rechazada y eliminada.")
        elif accion in {"desactivar", "reactivar"}:
            if usuario == request.user or usuario.is_superuser:
                return HttpResponseBadRequest("No se puede desactivar a un administrador ni a uno mismo.")
            usuario.is_active = accion == "reactivar"
            usuario.save(update_fields=["is_active"])
            if usuario.is_active:
                messages.success(request, f"{usuario.username} puede volver a entrar.")
            else:
                messages.success(request, f"{usuario.username} ya no puede entrar al panel ni usar la API.")
        else:
            return HttpResponseBadRequest("Acción desconocida.")
    return redirect("panel:usuarios")
