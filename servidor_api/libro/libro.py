"""
API interna del libro mayor (M10-02). El resto del servidor registra dinero solo
por aquí; nadie crea Asientos ni Partidas a mano.

Convención (céntimos enteros; monto > 0 es debe, monto < 0 es haber; cada
asiento suma cero):

    Recarga web de S/ 10.00     CAJA_PASARELA    +1000   SALDO_CLIENTE_7  -1000
    Viaje de S/ 1.30            SALDO_CLIENTE_7  +130    INGRESOS_PASAJES -130

El saldo de un cliente es el negativo de la suma de su SALDO_CLIENTE_<id>: tras
los dos asientos de arriba, -(-1000 + 130) = 870.

Idempotencia: `ref` identifica la operación. Registrar otra vez la misma ref con
el mismo tipo y las mismas partidas devuelve el asiento que ya existe (reintento
de un validador, webhook repetido); con otras partidas es ConflictoAsiento.

Un error no se borra: `revertir` registra un asiento espejo con ref "rev-<ref>".
"""

from collections import Counter

from django.db import IntegrityError, transaction
from django.db.models import Sum

from .models import Asiento, CuentaContable, Partida

PREFIJO_REVERSO = "rev-"
_MAX_REF = Asiento._meta.get_field("ref").max_length


class ErrorLibro(Exception):
    """Asiento inválido: no se registró nada."""


class AsientoDesbalanceado(ErrorLibro):
    """Las partidas no suman cero."""


class ConflictoAsiento(ErrorLibro):
    """La ref ya existe con otro tipo u otras partidas."""


def _validar(tipo, ref, partidas):
    if tipo not in Asiento.Tipo.values:
        raise ErrorLibro(f"tipo de asiento desconocido: {tipo!r}")
    if not ref or len(ref) > _MAX_REF:
        raise ErrorLibro(f"ref vacía o de más de {_MAX_REF} caracteres: {ref!r}")
    if len(partidas) < 2:
        raise ErrorLibro(f"{ref}: un asiento necesita al menos dos partidas")
    for codigo, monto in partidas:
        # bool es int en Python: True no es un céntimo
        if type(monto) is not int:
            raise ErrorLibro(f"{ref}: monto de {codigo} no es un entero de céntimos: {monto!r}")
        if monto == 0:
            raise ErrorLibro(f"{ref}: partida de {codigo} con monto cero")
    suma = sum(monto for _, monto in partidas)
    if suma != 0:
        raise AsientoDesbalanceado(f"{ref}: las partidas suman {suma:+d}, deben sumar cero")


def _partidas_de(asiento):
    return Counter(asiento.partidas.values_list("cuenta_contable__codigo", "monto"))


def _existente(tipo, ref, partidas):
    asiento = Asiento.objects.get(ref=ref)
    if asiento.tipo != tipo or _partidas_de(asiento) != Counter(partidas):
        raise ConflictoAsiento(f"{ref}: ya está registrado con otro tipo u otras partidas")
    return asiento


def registrar(tipo, ref, partidas, fecha=None, descripcion="", creado_por=None) -> Asiento:
    """Registra un asiento con `partidas` = [(codigo_cuenta, céntimos), ...].

    Idempotente por `ref`. Lanza AsientoDesbalanceado si no suma cero,
    ConflictoAsiento si la ref ya existe distinta y ErrorLibro si es inválido
    por otra razón (menos de dos partidas, monto cero o no entero, cuenta
    inexistente).
    """
    partidas = [(str(codigo), monto) for codigo, monto in partidas]
    _validar(tipo, ref, partidas)

    if Asiento.objects.filter(ref=ref).exists():
        return _existente(tipo, ref, partidas)

    codigos = {codigo for codigo, _ in partidas}
    cuentas = dict(CuentaContable.objects.filter(codigo__in=codigos)
                   .values_list("codigo", "id"))
    if faltan := sorted(codigos - cuentas.keys()):
        raise ErrorLibro(f"{ref}: cuentas contables inexistentes: {', '.join(faltan)}")

    datos = {"tipo": tipo, "ref": ref, "descripcion": descripcion, "creado_por": creado_por}
    if fecha is not None:
        datos["fecha"] = fecha
    try:
        # Savepoint: si otra transacción ganó la carrera por la misma ref, la
        # transacción de quien llama sigue usable para leer el asiento ganador
        with transaction.atomic():
            asiento = Asiento.objects.create(**datos)
            Partida.objects.bulk_create(
                Partida(asiento=asiento, cuenta_contable_id=cuentas[codigo], monto=monto)
                for codigo, monto in partidas)
    except IntegrityError:
        if not Asiento.objects.filter(ref=ref).exists():
            raise
        return _existente(tipo, ref, partidas)
    return asiento


def saldo_cliente(cuenta) -> int:
    """Saldo del cliente (transporte.Cuenta o su id) en céntimos, según el libro."""
    id_cuenta = getattr(cuenta, "pk", cuenta)
    suma = (Partida.objects.filter(cuenta_contable__cuenta_cliente_id=id_cuenta)
            .aggregate(s=Sum("monto"))["s"])
    return -(suma or 0)


def revertir(asiento, motivo, creado_por=None) -> Asiento:
    """Registra el asiento espejo de `asiento` (ref "rev-<ref>"); el original no se toca.

    Idempotente: revertir dos veces el mismo asiento devuelve el mismo reverso.
    Un reverso no se revierte: si hacía falta la operación, se registra de nuevo
    con otra ref.
    """
    if asiento.tipo == Asiento.Tipo.REVERSO:
        raise ErrorLibro(f"{asiento.ref}: un reverso no se revierte; "
                         "registra la operación de nuevo")
    if not motivo:
        raise ErrorLibro(f"{asiento.ref}: el reverso necesita un motivo")
    espejo = [(codigo, -monto) for codigo, monto
              in asiento.partidas.values_list("cuenta_contable__codigo", "monto")]
    return registrar(Asiento.Tipo.REVERSO, PREFIJO_REVERSO + asiento.ref, espejo,
                     descripcion=f"Reverso de {asiento.ref}: {motivo}"[:200],
                     creado_por=creado_por)
