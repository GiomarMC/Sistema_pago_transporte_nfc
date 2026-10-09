"""
Lógica de negocio del servidor. Las vistas solo validan la entrada y llaman aquí.

Toda modificación de saldo bloquea la fila de la cuenta (select_for_update):
varios validadores pueden sincronizar a la vez sin pisarse.
"""

from datetime import datetime

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from .models import Alerta, Cuenta, EstadoSync, Movimiento, Recarga, Tarifa, Tarjeta, soles
from .sync_cambios import pagina, registrar_recarga, registrar_tarjeta

VALIDADOR_EMISOR = 0
PUNTO_RECARGA = 900
RECARGA_MINIMA = 100      # S/ 1.00
RECARGA_MAXIMA = 20000    # S/ 200.00


class ErrorNegocio(Exception):
    """Operación rechazada por una regla del sistema (se responde con 409)."""


# --- Utilidades ---------------------------------------------------------------

def _cuenta_bloqueada(id_cuenta) -> Cuenta:
    return Cuenta.objects.select_for_update().get(pk=id_cuenta)


def registrar_movimiento(cuenta, tarjeta, tipo, monto, *, operacion=None, validador=None,
                         fecha=None, ref=None, contador=None) -> Movimiento:
    """Aplica el monto al saldo de la cuenta (que debe estar bloqueada) y lo registra."""
    cuenta.saldo += monto
    cuenta.save(update_fields=["saldo"])
    return Movimiento.objects.create(
        fecha=fecha or timezone.now(), cuenta=cuenta, tarjeta=tarjeta, tipo=tipo, monto=monto,
        saldo_final=cuenta.saldo, operacion=operacion, validador=validador, ref=ref,
        contador=contador)


def alertar(tarjeta, tipo, detalle=""):
    Alerta.objects.create(tarjeta=tarjeta, tipo=tipo, detalle=detalle)


@transaction.atomic
def bloquear(tarjeta, motivo, tipo="bloqueo automático"):
    if tarjeta.estado == Tarjeta.Estado.ACTIVA:
        tarjeta.estado = Tarjeta.Estado.BLOQUEADA
        tarjeta.bloqueada_en = timezone.now()
        tarjeta.save(update_fields=["estado", "bloqueada_en"])
        registrar_tarjeta(tarjeta.id)
    alertar(tarjeta, tipo, motivo)


# --- Recargas -----------------------------------------------------------------

@transaction.atomic
def crear_recarga(id_cuenta, monto, origen) -> Recarga:
    """Abona el monto a la cuenta al instante; queda pendiente de llegar a la tarjeta."""
    if not RECARGA_MINIMA <= monto <= RECARGA_MAXIMA:
        raise ErrorNegocio(f"el monto debe estar entre {soles(RECARGA_MINIMA)} y "
                           f"{soles(RECARGA_MAXIMA)}")
    cuenta = _cuenta_bloqueada(id_cuenta)
    seq = (cuenta.recargas.aggregate(m=Max("seq"))["m"] or 0) + 1
    recarga = Recarga.objects.create(cuenta=cuenta, seq=seq, monto=monto, origen=origen)
    registrar_recarga(recarga, pendiente=True)
    registrar_movimiento(cuenta, None, "recarga", monto, ref=f"R{recarga.id}")
    return recarga


def recargas_para_tarjeta(id_cuenta, ultima_en_tarjeta):
    """Recargas correlativas posteriores a la última que tiene la tarjeta.
    Devuelve (monto total, hasta_seq)."""
    total, hasta = 0, ultima_en_tarjeta
    for r in Recarga.objects.filter(cuenta_id=id_cuenta, seq__gt=ultima_en_tarjeta).order_by("seq"):
        if r.seq != hasta + 1:
            break
        total, hasta = total + r.monto, r.seq
    return total, hasta


@transaction.atomic
def entregar_recargas(tarjeta, hasta_seq, por, fecha=None):
    """Marca como entregadas las recargas hasta hasta_seq, solo si la tarjeta es
    la activa de la cuenta (si un bus desactualizado las grabó en una tarjeta ya
    bloqueada, siguen pendientes para la tarjeta nueva)."""
    if tarjeta.estado != Tarjeta.Estado.ACTIVA:
        return 0
    pendientes = list(Recarga.objects.select_for_update().filter(
        cuenta_id=tarjeta.cuenta_id, seq__lte=hasta_seq, aplicada_en__isnull=True))
    if not pendientes:
        return 0
    Recarga.objects.filter(pk__in=[r.pk for r in pendientes]).update(
        aplicada_en=fecha or timezone.now(), aplicada_por=por, tarjeta=tarjeta)
    for recarga in pendientes:
        registrar_recarga(recarga, pendiente=False)
    return len(pendientes)


@transaction.atomic
def registrar_entrega_en_punto(tarjeta, hasta_seq, operacion, contador, recarga_id=None):
    entregar_recargas(tarjeta, hasta_seq, PUNTO_RECARGA)
    if recarga_id is not None:
        # Deja constancia de la operación de tarjeta (sirve para detectar clones)
        Movimiento.objects.filter(ref=f"R{recarga_id}", cuenta_id=tarjeta.cuenta_id).update(
            tarjeta=tarjeta, operacion=operacion, validador=PUNTO_RECARGA, contador=contador)


# --- Emisión ------------------------------------------------------------------

@transaction.atomic
def reservar_emision(uid, *, id_cuenta=None, nombre=None, documento=None, codigo_tarifa=None):
    """Paso 1: crea la cuenta si es nueva y reserva el n.º de tarjeta. Devuelve
    la tarjeta pendiente; el cliente graba con el saldo actual de la cuenta."""
    if id_cuenta is not None:
        cuenta = _cuenta_bloqueada(id_cuenta)
        creo = False
    else:
        if Cuenta.objects.filter(documento=documento).exists():
            existente = Cuenta.objects.get(documento=documento)
            raise ErrorNegocio(f"ya existe la cuenta {existente.id} con documento {documento}")
        tarifa = Tarifa.objects.filter(codigo=codigo_tarifa).first()
        if tarifa is None:
            raise ErrorNegocio(f"tarifa desconocida: {codigo_tarifa}")
        cuenta = Cuenta.objects.create(nombre=nombre, documento=documento, tarifa=tarifa)
        creo = True
    ultima = cuenta.recargas.aggregate(m=Max("seq"))["m"] or 0
    return Tarjeta.objects.create(uid=uid, cuenta=cuenta, recarga_inicial=ultima, creo_cuenta=creo)


@transaction.atomic
def confirmar_emision(id_tarjeta, contador=None):
    """Paso 2 (tras grabar y verificar la tarjeta): la activa, anula emisiones
    anteriores de la misma tarjeta física y bloquea las otras tarjetas de la cuenta."""
    tarjeta = Tarjeta.objects.select_for_update().filter(pk=id_tarjeta).first()
    if tarjeta is None or tarjeta.estado != Tarjeta.Estado.PENDIENTE:
        raise ErrorNegocio("la tarjeta no está pendiente de emisión")
    cuenta = _cuenta_bloqueada(tarjeta.cuenta_id)
    ahora = timezone.now()

    anteriores = list(Tarjeta.objects.select_for_update().filter(
        uid=tarjeta.uid, estado__in=[Tarjeta.Estado.ACTIVA, Tarjeta.Estado.PENDIENTE])
        .exclude(pk=tarjeta.pk).values_list("pk", flat=True))
    if anteriores:
        Tarjeta.objects.filter(pk__in=anteriores).update(
            estado=Tarjeta.Estado.ANULADA, bloqueada_en=ahora)
        for id_anterior in anteriores:
            registrar_tarjeta(id_anterior)
    reemplazadas = list(cuenta.tarjetas.filter(estado=Tarjeta.Estado.ACTIVA).exclude(pk=tarjeta.pk))
    for t in reemplazadas:
        bloquear(t, f"reemplazada por la tarjeta {tarjeta.id}", tipo="reemplazo")

    tarjeta.estado = Tarjeta.Estado.ACTIVA
    tarjeta.save(update_fields=["estado"])
    entregar_recargas(tarjeta, tarjeta.recarga_inicial, VALIDADOR_EMISOR)
    registrar_movimiento(cuenta, tarjeta, "emision", 0, operacion=1,
                         validador=VALIDADOR_EMISOR, contador=contador)
    return tarjeta, reemplazadas


@transaction.atomic
def cancelar_emision(id_tarjeta):
    """Si la grabación falló: libera la reserva (y la cuenta, si se creó vacía)."""
    tarjeta = Tarjeta.objects.select_for_update().filter(
        pk=id_tarjeta, estado=Tarjeta.Estado.PENDIENTE).first()
    if tarjeta is None:
        raise ErrorNegocio("la tarjeta no está pendiente de emisión")
    cuenta = tarjeta.cuenta
    tarjeta.delete()
    if (tarjeta.creo_cuenta and not cuenta.tarjetas.exists()
            and not cuenta.movimientos.exists() and not cuenta.recargas.exists()):
        cuenta.delete()


@transaction.atomic
def anular_por_uid(uid):
    """Tarjeta física restablecida a fábrica: sus emisiones dejan de ser válidas."""
    ids = list(Tarjeta.objects.select_for_update().filter(
        uid=uid, estado__in=[Tarjeta.Estado.ACTIVA, Tarjeta.Estado.PENDIENTE])
        .values_list("pk", flat=True))
    if ids:
        Tarjeta.objects.filter(pk__in=ids).update(
            estado=Tarjeta.Estado.ANULADA, bloqueada_en=timezone.now())
        for tarjeta_id in ids:
            registrar_tarjeta(tarjeta_id)
    return len(ids)


# --- Sincronización con validadores -------------------------------------------

def sincronizar(validador, eventos):
    """Aplica los eventos del validador y devuelve lo que necesita para operar
    sin conexión. Idempotente: los eventos ya recibidos se ignoran."""
    aceptados = procesar_lote(validador, eventos)

    return {
        "validador": validador.numero,
        "aceptados": aceptados,
        "lista_negra": list(Tarjeta.objects.filter(
            estado__in=[Tarjeta.Estado.BLOQUEADA, Tarjeta.Estado.ANULADA])
            .order_by("id").values_list("id", flat=True)),
        "recargas": list(Recarga.objects.filter(aplicada_en__isnull=True)
                         .order_by("cuenta_id", "seq").values("cuenta_id", "seq", "monto")),
        "tarifas": {str(t.codigo): [t.nombre, t.precio] for t in Tarifa.objects.all()},
        "hora_servidor": timezone.now().isoformat(timespec="seconds"),
    }


def procesar_lote(validador, eventos):
    """Procesamiento compartido por v1 y v2; conserva referencias idempotentes."""
    aceptados = []
    for ev in eventos:
        try:
            with transaction.atomic():
                procesar_evento(validador.numero, ev)
        except Exception as e:  # dato corrupto: se registra y se confirma, para no reintentar siempre
            alertar(None, "evento inválido", f"V{validador.numero}-{ev.get('id')}: {e!r}")
        if "id" in ev:
            aceptados.append(ev["id"])

    validador.ultima_sync = timezone.now()
    validador.save(update_fields=["ultima_sync"])
    return aceptados


def sincronizar_v2(validador, eventos, cursor, hasta_version, limite):
    """Confirma eventos y entrega una página acotada de un corte estable."""
    aceptados = procesar_lote(validador, eventos)
    if hasta_version is None:
        hasta_version = EstadoSync.objects.get(pk=1).ultima_version
    cambios, siguiente, hay_mas = pagina(cursor, hasta_version, limite)
    return {
        "version": 2,
        "validador": validador.numero,
        "aceptados": aceptados,
        "cambios": cambios,
        "siguiente_cursor": siguiente,
        "hasta_version": hasta_version,
        "hay_mas": hay_mas,
        "hora_servidor": timezone.now().isoformat(timespec="seconds"),
    }


def procesar_evento(numero, ev):
    ref = f"V{numero}-{ev['id']}"
    if (Movimiento.objects.filter(ref=ref).exists()
            or Alerta.objects.filter(detalle__startswith=f"{ref}:").exists()):
        return  # ya recibido en una sincronización anterior

    tipo = ev["tipo"]
    tarjeta = Tarjeta.objects.filter(pk=ev["tarjeta_id"]).first()

    if tipo in ("incidencia", "fraude"):
        alertar(tarjeta, f"{tipo} (validador {numero})", f"{ref}: {ev.get('detalle', '')}")
        if tipo == "fraude" and tarjeta is not None:
            bloquear(tarjeta, f"{ref}: fraude detectado por el validador")
        return
    if tipo != "viaje":
        raise ValueError(f"tipo de evento desconocido: {tipo}")
    if tarjeta is None:
        alertar(None, "tarjeta desconocida",
                f"{ref}: viaje de la tarjeta {ev['tarjeta_id']}, que no existe en el sistema")
        return

    cuenta = _cuenta_bloqueada(tarjeta.cuenta_id)
    tarjeta = Tarjeta.objects.select_for_update().get(pk=tarjeta.pk)
    fecha = datetime.fromisoformat(ev["fecha"])
    operacion, contador = ev["operacion"], ev.get("contador")

    # Clones / copias restauradas: el mismo n.º de operación o el mismo contador NFC
    # no pueden aparecer en dos usos distintos de una tarjeta (el orden de llegada
    # no importa: los validadores sincronizan cuando pueden).
    condicion = Q(operacion=operacion)
    if contador is not None:
        condicion |= Q(contador=contador)
    repetido = Movimiento.objects.filter(tarjeta=tarjeta, ref__isnull=False) \
        .filter(condicion).first()
    if repetido:
        bloquear(tarjeta, f"{ref} repite operación/contador de {repetido.ref}: "
                          "posible clon o copia restaurada")
    elif tarjeta.estado != Tarjeta.Estado.ACTIVA and (tarjeta.bloqueada_en is None
                                                      or fecha >= tarjeta.bloqueada_en):
        alertar(tarjeta, "uso de tarjeta no activa",
                f"{ref}: tarjeta '{tarjeta.estado}' aceptada por un validador desactualizado")

    # El cobro se registra siempre: el pasajero ya viajó
    registrar_movimiento(cuenta, tarjeta, "viaje", -int(ev["monto"]), operacion=operacion,
                         validador=numero, fecha=fecha, ref=ref, contador=contador)
    if ev.get("recarga_hasta"):
        entregar_recargas(tarjeta, int(ev["recarga_hasta"]), numero, fecha)
    if cuenta.saldo < 0:
        alertar(tarjeta, "saldo negativo",
                f"{ref}: cuenta {cuenta.id} con {soles(cuenta.saldo)} (uso sin conexión de una "
                "tarjeta bloqueada, clon o copia)")
