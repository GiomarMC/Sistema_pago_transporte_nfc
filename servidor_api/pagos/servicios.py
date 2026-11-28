from datetime import timedelta
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from transporte import servicios as transporte
from transporte.models import Alerta, Cuenta

from .models import HistorialOrden, OrdenRecarga
from .pasarelas import obtener_pasarela
from .pasarelas.base import ErrorPasarela


class ErrorPago(ValueError):
    codigo = "validacion"


class TransicionInvalida(ErrorPago):
    pass


class IdempotenciaConflicto(ErrorPago):
    codigo = "idempotencia_conflicto"


class LimitePago(ErrorPago):
    def __init__(self, codigo, mensaje):
        self.codigo = codigo
        super().__init__(mensaje)


PERMITIDAS = {
    "creada": {"pendiente"},
    "pendiente": {"pagada", "fallida", "expirada"},
    "pagada": {"acreditada"},
    # Sin reembolsos (#102): una orden acreditada ya no cambia. Las recargas no se devuelven;
    # un error se corrige con un ajuste de saldo hecho por un operador.
}


@transaction.atomic
def transicion(orden, a, motivo):
    bloqueada = OrdenRecarga.objects.select_for_update().get(pk=orden.pk)
    if a not in PERMITIDAS.get(bloqueada.estado, set()):
        raise TransicionInvalida(f"transición {bloqueada.estado} → {a} no permitida")
    anterior = bloqueada.estado
    bloqueada.estado = a
    bloqueada.save(update_fields=["estado"])
    HistorialOrden.objects.create(orden=bloqueada, de=anterior, a=a, motivo=motivo)
    orden.estado = a
    return bloqueada


def _validar_clave(clave):
    try:
        clave = UUID(str(clave))
    except (ValueError, AttributeError) as exc:
        raise ErrorPago("Idempotency-Key debe ser UUID v4") from exc
    if clave.version != 4:
        raise ErrorPago("Idempotency-Key debe ser UUID v4")
    return clave


def _misma_orden(orden, cuenta, monto):
    if orden.cuenta_id != cuenta.pk or orden.monto != monto:
        raise IdempotenciaConflicto("clave reutilizada con cuenta o monto diferente")
    return orden


def crear_orden(usuario, cuenta, monto, clave, *, url_retorno=""):
    clave = _validar_clave(clave)
    if not usuario or not usuario.is_authenticated:
        raise ErrorPago("se requiere usuario cliente")
    if not isinstance(monto, int) or isinstance(monto, bool) or not 100 <= monto <= 20000:
        raise LimitePago("monto_fuera_de_limite", "monto entre 100 y 20000 céntimos")
    with transaction.atomic():
        Cuenta.objects.select_for_update().get(pk=cuenta.pk)
        previa = OrdenRecarga.objects.filter(usuario=usuario, clave_idempotencia=clave).first()
        if previa:
            _misma_orden(previa, cuenta, monto)
            return iniciar_cobro(previa, url_retorno=url_retorno) if previa.estado == "creada" else previa
        hoy = timezone.localdate()
        total = (OrdenRecarga.objects.filter(
            cuenta=cuenta, estado__in=["pagada", "acreditada"], pagada_en__date=hoy)
            .aggregate(s=Sum("monto"))["s"] or 0)
        reservado = (OrdenRecarga.objects.filter(
            cuenta=cuenta, estado__in=["creada", "pendiente"])
            .aggregate(s=Sum("monto"))["s"] or 0)
        if total + reservado + monto > 50000:
            raise LimitePago("limite_diario", "límite diario de S/ 500 excedido")
        pendientes = OrdenRecarga.objects.filter(cuenta=cuenta, estado__in=["creada", "pendiente"]).count()
        if pendientes >= 3:
            raise LimitePago("limite_diario", "máximo tres órdenes pendientes")
        try:
            with transaction.atomic():
                orden = OrdenRecarga.objects.create(
                    cuenta=cuenta, usuario=usuario, monto=monto, clave_idempotencia=clave,
                    pasarela=obtener_pasarela().nombre, expira=timezone.now() + timedelta(minutes=30))
        except IntegrityError:
            previa = OrdenRecarga.objects.get(usuario=usuario, clave_idempotencia=clave)
            return _misma_orden(previa, cuenta, monto)
    return iniciar_cobro(orden, url_retorno=url_retorno)


def iniciar_cobro(orden, *, url_retorno=""):
    """Reintento seguro de una orden creada si la pasarela falló."""
    orden.refresh_from_db()
    if orden.estado != "creada":
        return orden
    pasarela = obtener_pasarela(orden.pasarela)
    try:
        cobro = pasarela.crear_cobro(
            orden_id=orden.pk, monto=orden.monto, descripcion=f"Recarga {orden.pk}",
            correo=orden.usuario.email if orden.usuario else "", url_retorno=url_retorno)
    except ErrorPasarela:
        return orden
    with transaction.atomic():
        bloqueada = OrdenRecarga.objects.select_for_update().get(pk=orden.pk)
        if bloqueada.estado == "creada":
            bloqueada.id_externo = cobro.id_externo
            bloqueada.url_pago = cobro.url_pago
            bloqueada.expira = cobro.expira
            bloqueada.save(update_fields=["id_externo", "url_pago", "expira"])
            transicion(bloqueada, "pendiente", "cobro creado")
        return bloqueada


@transaction.atomic
def acreditar(orden):
    Cuenta.objects.select_for_update().get(pk=orden.cuenta_id)
    bloqueada = OrdenRecarga.objects.select_for_update().get(pk=orden.pk)
    if bloqueada.estado == "acreditada":
        return bloqueada
    if bloqueada.estado != "pagada":
        raise TransicionInvalida("solo se acredita un pago confirmado")
    recarga = transporte.crear_recarga(bloqueada.cuenta_id, bloqueada.monto, "web")
    bloqueada.recarga = recarga
    bloqueada.acreditada_en = timezone.now()
    bloqueada.save(update_fields=["recarga", "acreditada_en"])
    resultado = transicion(bloqueada, "acreditada", "recarga acreditada en cuenta")
    from notificaciones.disparadores import al_acreditar, intentar_sin_interrumpir
    intentar_sin_interrumpir(al_acreditar, resultado)
    return resultado


def confirmar_cobro(orden):
    """La consulta al proveedor es obligatoria, incluso tras un webhook firmado."""
    pasarela = obtener_pasarela(orden.pasarela)
    estado = pasarela.consultar(orden.id_externo)
    if estado.id_externo != orden.id_externo or estado.monto != orden.monto:
        Alerta.objects.create(tipo="pago inconsistente", detalle=f"Orden {orden.pk}: monto o ID externo distinto")
        return False
    if estado.estado != "pagado":
        return False
    with transaction.atomic():
        Cuenta.objects.select_for_update().get(pk=orden.cuenta_id)
        actual = OrdenRecarga.objects.select_for_update().get(pk=orden.pk)
        if actual.estado == "acreditada":
            return True
        if actual.estado != "pendiente":
            return False
        actual.pagada_en = estado.pagado_en or timezone.now()
        actual.save(update_fields=["pagada_en"])
        transicion(actual, "pagada", "cobro verificado en pasarela")
        acreditar(actual)
        return True


def revisar_orden(orden):
    actual = OrdenRecarga.objects.get(pk=orden.pk)
    if actual.estado == "creada":
        return iniciar_cobro(actual)
    if actual.estado != "pendiente":
        return actual
    estado = obtener_pasarela(actual.pasarela).consultar(actual.id_externo)
    if estado.estado == "pagado":
        confirmar_cobro(actual)
    elif estado.estado in ("fallido", "expirado") or actual.expira <= timezone.now():
        with transaction.atomic():
            bloqueada = OrdenRecarga.objects.select_for_update().get(pk=actual.pk)
            if bloqueada.estado == "pendiente":
                transicion(bloqueada, "fallida" if estado.estado == "fallido" else "expirada",
                           "reconsulta de pasarela")
    return OrdenRecarga.objects.get(pk=actual.pk)
