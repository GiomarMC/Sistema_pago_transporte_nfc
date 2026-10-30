"""API interna del libro. Ninguna función acepta montos en coma flotante."""

from collections import Counter

from django.db import IntegrityError, connection, transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Asiento, CuentaContable, Partida


class ConflictoAsiento(ValueError):
    pass


def guardar_saldo_contabilizado(cuenta):
    """Permite exactamente esta escritura de caché dentro de la transacción."""
    if not connection.in_atomic_block:
        raise RuntimeError("el saldo contable exige una transacción")
    with connection.cursor() as cur:
        cur.execute("SET LOCAL subepe.libro_escritura = 'on'")
    try:
        cuenta.save(update_fields=["saldo"])
    finally:
        if not connection.needs_rollback:
            with connection.cursor() as cur:
                cur.execute("SET LOCAL subepe.libro_escritura = 'off'")


def cuenta_cliente(cuenta):
    pk = cuenta.pk if hasattr(cuenta, "pk") else cuenta
    try:
        return CuentaContable.objects.get(cuenta_cliente_id=pk)
    except CuentaContable.DoesNotExist:
        try:
            with transaction.atomic():
                return CuentaContable.objects.create(
                    codigo=f"SALDO_CLIENTE_{pk}", nombre=f"Saldo de cuenta {pk}",
                    naturaleza=CuentaContable.Naturaleza.PASIVO, cuenta_cliente_id=pk)
        except IntegrityError:
            return CuentaContable.objects.get(cuenta_cliente_id=pk)


def _normalizar(partidas):
    if len(partidas) < 2:
        raise ValueError("un asiento necesita al menos dos partidas")
    normalizadas = []
    for codigo, monto in partidas:
        if not isinstance(codigo, str) or not isinstance(monto, int) or isinstance(monto, bool):
            raise ValueError("cada partida requiere código y céntimos enteros")
        if monto == 0:
            raise ValueError("una partida no puede tener monto cero")
        normalizadas.append((codigo, monto))
    if sum(monto for _, monto in normalizadas) != 0:
        raise ValueError("el asiento no está equilibrado")
    return normalizadas


def _comprobar_existente(asiento, tipo, partidas, fecha=None, descripcion=""):
    existentes = Counter(asiento.partidas.values_list("cuenta_contable__codigo", "monto"))
    if (asiento.tipo != tipo or existentes != Counter(partidas)
            or asiento.descripcion != descripcion
            or (fecha is not None and asiento.fecha != fecha)):
        raise ConflictoAsiento(f"referencia {asiento.ref} reutilizada con datos incompatibles")
    return asiento


def registrar(tipo, ref, partidas, fecha=None, descripcion="") -> Asiento:
    """Registra doble partida; la referencia única es la clave de idempotencia."""
    partidas = _normalizar(list(partidas))
    max_ref = 80 if tipo == "reverso" else 76  # reserva "rev-" para el asiento espejo
    if not ref or len(ref) > max_ref or not tipo or len(tipo) > 20:
        raise ValueError("tipo o referencia inválidos")
    existente = Asiento.objects.filter(ref=ref).first()
    if existente:
        return _comprobar_existente(existente, tipo, partidas, fecha, descripcion)
    try:
        with transaction.atomic():
            asiento = Asiento.objects.create(fecha=fecha or timezone.now(), tipo=tipo,
                                             ref=ref, descripcion=descripcion)
            codigos = {codigo for codigo, _ in partidas}
            cuentas = {c.codigo: c for c in CuentaContable.objects.filter(codigo__in=codigos)}
            if len(cuentas) != len(codigos):
                raise ValueError(f"cuentas contables desconocidas: {sorted(codigos - cuentas.keys())}")
            Partida.objects.bulk_create([
                Partida(asiento=asiento, cuenta_contable=cuentas[codigo], monto=monto)
                for codigo, monto in partidas
            ])
            Asiento.objects.filter(pk=asiento.pk).update(confirmado=True)
            asiento.confirmado = True
            return asiento
    except IntegrityError:
        existente = Asiento.objects.filter(ref=ref).first()
        if existente:
            return _comprobar_existente(existente, tipo, partidas, fecha, descripcion)
        raise


def saldo_cliente(cuenta) -> int:
    pk = cuenta.pk if hasattr(cuenta, "pk") else cuenta
    total = Partida.objects.filter(cuenta_contable__cuenta_cliente_id=pk).aggregate(
        total=Sum("monto"))["total"] or 0
    return -int(total)


@transaction.atomic
def abrir_saldo_existente(cuenta, saldo):
    """Solo para importar una base histórica; no recontabiliza sus movimientos."""
    from transporte.models import Cuenta

    if not isinstance(saldo, int) or isinstance(saldo, bool):
        raise ValueError("saldo de apertura inválido")
    bloqueada = Cuenta.objects.select_for_update().get(pk=cuenta.pk)
    ref = f"apertura-{cuenta.pk}"
    if bloqueada.saldo != 0 or saldo_cliente(bloqueada) != 0 or Asiento.objects.filter(ref=ref).exists():
        raise ConflictoAsiento("la cuenta ya tiene saldo o apertura")
    if saldo:
        cliente = cuenta_cliente(bloqueada)
        registrar("apertura", ref, [("AJUSTES", saldo), (cliente.codigo, -saldo)],
                  descripcion="Saldo histórico importado")
        bloqueada.saldo = saldo
        guardar_saldo_contabilizado(bloqueada)
    cuenta.saldo = saldo


@transaction.atomic
def revertir(asiento, motivo):
    """Asiento espejo; si afecta saldo de cliente, actualiza caché y movimiento."""
    from transporte.models import Cuenta, Movimiento

    if not motivo or not motivo.strip():
        raise ValueError("el reverso necesita motivo")
    original = Asiento.objects.get(pk=asiento.pk)
    if original.tipo == "reverso":
        raise ValueError("no se revierte un reverso")
    ref = "rev-" + original.ref
    partidas = list(original.partidas.select_related("cuenta_contable"))
    espejo = [(p.cuenta_contable.codigo, -p.monto) for p in partidas]
    clientes = [p for p in partidas if p.cuenta_contable.cuenta_cliente_id is not None]
    if len({p.cuenta_contable.cuenta_cliente_id for p in clientes}) > 1:
        raise ValueError("reverso de varias cuentas de cliente no admitido")
    cuenta = None
    if clientes:
        cuenta = Cuenta.objects.select_for_update().get(pk=clientes[0].cuenta_contable.cuenta_cliente_id)
    previo = Asiento.objects.filter(ref=ref).first()
    if previo:
        return _comprobar_existente(previo, "reverso", espejo, descripcion=motivo.strip())
    nuevo = registrar("reverso", ref, espejo, descripcion=motivo.strip())
    if cuenta:
        variacion = sum(p.monto for p in clientes)
        cuenta.saldo += variacion
        guardar_saldo_contabilizado(cuenta)
        Movimiento.objects.create(fecha=timezone.now(), cuenta=cuenta, tipo="reverso",
                                  monto=variacion, saldo_final=cuenta.saldo, ref=ref)
    return nuevo
