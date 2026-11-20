"""
Lógica de negocio del servidor. Las vistas solo validan la entrada y llaman aquí.

Toda modificación de saldo bloquea la fila de la cuenta (select_for_update):
varios validadores pueden sincronizar a la vez sin pisarse.

Cada cambio de lista negra o de recargas pendientes se registra en CambioLista
dentro de la misma transacción (sincronización v2, C1 5.5): si la operación
falla, el cambio tampoco queda.
"""

from datetime import datetime

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from libro import libro
from libro.models import PREFIJO_CLIENTE, Asiento

from .models import Alerta, CambioLista, Cuenta, Movimiento, Recarga, Tarifa, Tarjeta, Validador, soles

VALIDADOR_EMISOR = 0
PUNTO_RECARGA = 900
RECARGA_MINIMA = 100      # S/ 1.00
RECARGA_MAXIMA = 20000    # S/ 200.00

# Libro mayor: a qué caja entra el dinero de una recarga según su origen
CAJA_DE_ORIGEN = {
    "web": "CAJA_PASARELA",
    "yape": "CAJA_PASARELA",
    "punto": "CAJA_VENTANILLA",
    "ventanilla": "CAJA_VENTANILLA",
    "emision": "CAJA_VENTANILLA",   # saldo inicial pagado al emitir la tarjeta
}
TIPO_ASIENTO = {"viaje": Asiento.Tipo.VIAJE, "recarga": Asiento.Tipo.RECARGA,
                "emision": Asiento.Tipo.EMISION}


def registrar_conexion(numero, activo):
    """Punto de enlace para el futuro puente MQTT sp/v/{n}/conexion."""
    Validador.objects.filter(numero=numero).update(
        conexion_activa=activo, conexion_en=timezone.now())


class ErrorNegocio(Exception):
    """Operación rechazada por una regla del sistema (se responde con 409)."""


# --- Utilidades ---------------------------------------------------------------

def _cuenta_bloqueada(id_cuenta) -> Cuenta:
    return Cuenta.objects.select_for_update().get(pk=id_cuenta)


def _contrapartida(tipo, origen):
    """Cuenta contable del otro lado del SALDO_CLIENTE en un movimiento."""
    if tipo == "viaje":
        return "INGRESOS_PASAJES"
    if tipo == "emision":
        return "CAJA_VENTANILLA"    # saldo inicial cobrado al emitir
    if tipo == "recarga" and origen in CAJA_DE_ORIGEN:
        return CAJA_DE_ORIGEN[origen]
    raise ErrorNegocio(f"no se sabe a qué caja va un movimiento {tipo!r} de origen {origen!r}")


@transaction.atomic
def registrar_movimiento(cuenta, tarjeta, tipo, monto, *, operacion=None, validador=None,
                         fecha=None, ref=None, contador=None, origen=None) -> Movimiento:
    """Aplica el monto al saldo de la cuenta (que debe estar bloqueada), lo
    registra y asienta en el libro mayor, todo o nada.

    Si mueve dinero, el asiento lleva la ref del movimiento (o "mov-<id>") y
    la contrapartida según el tipo y, en las recargas, el origen del dinero.
    """
    cuenta.saldo += monto
    cuenta.save(update_fields=["saldo"])
    movimiento = Movimiento.objects.create(
        fecha=fecha or timezone.now(), cuenta=cuenta, tarjeta=tarjeta, tipo=tipo, monto=monto,
        saldo_final=cuenta.saldo, operacion=operacion, validador=validador, ref=ref,
        contador=contador)
    if monto:
        libro.registrar(TIPO_ASIENTO[tipo], ref or f"mov-{movimiento.id}",
                        [(f"{PREFIJO_CLIENTE}{cuenta.id}", -monto),
                         (_contrapartida(tipo, origen), monto)],
                        fecha=movimiento.fecha, descripcion=str(movimiento))
    if tipo == "viaje":
        from notificaciones.disparadores import al_registrar_viaje, intentar_sin_interrumpir
        intentar_sin_interrumpir(al_registrar_viaje, movimiento)
    return movimiento


def alertar(tarjeta, tipo, detalle=""):
    Alerta.objects.create(tarjeta=tarjeta, tipo=tipo, detalle=detalle)


# --- Cambios de lista (sincronización v2) --------------------------------------

def _bloqueos(ids_tarjeta, urgente):
    """Agrega las tarjetas a la lista negra de los validadores."""
    CambioLista.objects.bulk_create([
        CambioLista(tipo=CambioLista.Tipo.BLOQUEO, op=CambioLista.AGREGAR, a=i, urgente=urgente)
        for i in ids_tarjeta])


def _recargas(recargas, op):
    CambioLista.objects.bulk_create([
        CambioLista(tipo=CambioLista.Tipo.RECARGA, op=op, a=r.cuenta_id, b=r.seq, c=r.monto)
        for r in recargas])


def retirar_recarga(recarga):
    """Quita una recarga de las pendientes de los validadores (se reembolsó antes de
    llegar a la tarjeta): el mismo cambio de lista que cuando se entrega."""
    _recargas([recarga], CambioLista.APLICADA)


def cambios_desde(version, limite=120):
    """Cambios de lista posteriores a version, en orden (como mucho limite: las
    entradas que caben en un paquete LISTAS)."""
    return list(CambioLista.objects.filter(version__gt=version).order_by("version")[:limite])


@transaction.atomic
def bloquear(tarjeta, motivo, tipo="bloqueo automático"):
    # UPDATE condicional: con dos bloqueos a la vez, solo uno cambia la fila
    # (y registra el cambio de lista), aunque los dos vieran la tarjeta activa.
    ahora = timezone.now()
    recien_bloqueada = bool(Tarjeta.objects.filter(
        pk=tarjeta.pk, estado=Tarjeta.Estado.ACTIVA).update(
            estado=Tarjeta.Estado.BLOQUEADA, bloqueada_en=ahora))
    if recien_bloqueada:
        tarjeta.estado, tarjeta.bloqueada_en = Tarjeta.Estado.BLOQUEADA, ahora
        _bloqueos([tarjeta.pk], urgente=True)
    else:
        tarjeta.refresh_from_db(fields=["estado", "bloqueada_en"])
    alertar(tarjeta, tipo, motivo)
    if recien_bloqueada:
        from notificaciones.disparadores import al_bloquear, intentar_sin_interrumpir
        intentar_sin_interrumpir(al_bloquear, tarjeta)


# --- Recargas -----------------------------------------------------------------

@transaction.atomic
def crear_recarga(id_cuenta, monto, origen) -> Recarga:
    """Abona el monto a la cuenta al instante; queda pendiente de llegar a la tarjeta."""
    if not RECARGA_MINIMA <= monto <= RECARGA_MAXIMA:
        raise ErrorNegocio(f"el monto debe estar entre {soles(RECARGA_MINIMA)} y "
                           f"{soles(RECARGA_MAXIMA)}")
    if origen not in CAJA_DE_ORIGEN:
        raise ErrorNegocio(f"origen de recarga desconocido: {origen!r} "
                           f"(válidos: {', '.join(CAJA_DE_ORIGEN)})")
    cuenta = _cuenta_bloqueada(id_cuenta)
    seq = (cuenta.recargas.aggregate(m=Max("seq"))["m"] or 0) + 1
    recarga = Recarga.objects.create(cuenta=cuenta, seq=seq, monto=monto, origen=origen)
    registrar_movimiento(cuenta, None, "recarga", monto, ref=f"R{recarga.id}", origen=origen)
    _recargas([recarga], CambioLista.PENDIENTE)
    return recarga


def recargas_para_tarjeta(id_cuenta, ultima_en_tarjeta):
    """Recargas correlativas posteriores a la última que tiene la tarjeta.
    Devuelve (monto total, hasta_seq)."""
    total, hasta = 0, ultima_en_tarjeta
    for r in Recarga.objects.filter(cuenta_id=id_cuenta, seq__gt=ultima_en_tarjeta).order_by("seq"):
        if r.seq != hasta + 1:
            break
        total, hasta = total + (0 if r.reembolsada_en else r.monto), r.seq
    return total, hasta


@transaction.atomic
def entregar_recargas(tarjeta, hasta_seq, por, fecha=None):
    """Marca como entregadas las recargas hasta hasta_seq, solo si la tarjeta es
    la activa de la cuenta (si un bus desactualizado las grabó en una tarjeta ya
    bloqueada, siguen pendientes para la tarjeta nueva)."""
    if tarjeta.estado != Tarjeta.Estado.ACTIVA:
        return 0
    # select_for_update: si otro validador entrega las mismas a la vez, espera y
    # al releer ya no las ve pendientes (un solo cambio "aplicada" por recarga).
    entregadas = list(Recarga.objects.select_for_update().filter(
        cuenta_id=tarjeta.cuenta_id, seq__lte=hasta_seq, aplicada_en__isnull=True,
        reembolsada_en__isnull=True)
        .order_by("seq"))
    if not entregadas:
        return 0
    Recarga.objects.filter(pk__in=[r.pk for r in entregadas]).update(
        aplicada_en=fecha or timezone.now(), aplicada_por=por, tarjeta=tarjeta)
    _recargas(entregadas, CambioLista.APLICADA)
    return len(entregadas)


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
def confirmar_emision(id_tarjeta, contador=None, emitido_por=None):
    """Paso 2 (tras grabar y verificar la tarjeta): la activa, anula emisiones
    anteriores de la misma tarjeta física y bloquea las otras tarjetas de la cuenta."""
    tarjeta = Tarjeta.objects.select_for_update().filter(pk=id_tarjeta).first()
    if tarjeta is None or tarjeta.estado != Tarjeta.Estado.PENDIENTE:
        raise ErrorNegocio("la tarjeta no está pendiente de emisión")
    cuenta = _cuenta_bloqueada(tarjeta.cuenta_id)
    ahora = timezone.now()

    # Emisiones anteriores de la misma tarjeta física: se regraba encima, no
    # quedan en manos de nadie, así que no hace falta difundirlas como urgentes.
    _bloqueos(_anular(Tarjeta.objects.filter(uid=tarjeta.uid).exclude(pk=tarjeta.pk), ahora),
              urgente=False)
    # Las otras tarjetas activas de la cuenta (bloquear() registra su cambio)
    reemplazadas = list(cuenta.tarjetas.filter(estado=Tarjeta.Estado.ACTIVA).exclude(pk=tarjeta.pk))
    for t in reemplazadas:
        bloquear(t, f"reemplazada por la tarjeta {tarjeta.id}", tipo="reemplazo")

    tarjeta.estado = Tarjeta.Estado.ACTIVA
    tarjeta.save(update_fields=["estado"])
    entregar_recargas(tarjeta, tarjeta.recarga_inicial, VALIDADOR_EMISOR)
    registrar_movimiento(cuenta, tarjeta, "emision", 0, operacion=1,
                         validador=VALIDADOR_EMISOR, contador=contador)
    from clientes.activacion import generar_codigo, tiene_cliente
    if not tiene_cliente(cuenta.pk):
        tarjeta.codigo_activacion, registro = generar_codigo(tarjeta, emitido_por)
        tarjeta.codigo_activacion_expira = registro.expira
    # La entrega solo se confirma después de grabar y verificar la tarjeta física.
    # La cuenta ya está bloqueada: serializa emisiones y solicitudes de esta cuenta.
    from panel.models import SolicitudReposicion
    from notificaciones.disparadores import al_reposicion_lista, intentar_sin_interrumpir
    tarjeta.reposiciones_entregadas_ids = []
    solicitudes = SolicitudReposicion.objects.select_for_update().select_related("tarjeta").filter(
        cliente__cuenta_id=cuenta.pk).exclude(estado=SolicitudReposicion.Estado.ENTREGADA)
    for solicitud in solicitudes:
        avisar = solicitud.estado == SolicitudReposicion.Estado.SOLICITADA
        solicitud.estado = SolicitudReposicion.Estado.ENTREGADA
        solicitud.lista_en = solicitud.lista_en or ahora
        solicitud.entregada_en = ahora
        solicitud.tarjeta_nueva = tarjeta
        solicitud.gestionada_por = emitido_por
        solicitud.save(update_fields=["estado", "lista_en", "entregada_en",
                                      "tarjeta_nueva", "gestionada_por"])
        tarjeta.reposiciones_entregadas_ids.append(solicitud.pk)
        if avisar:
            intentar_sin_interrumpir(al_reposicion_lista, solicitud)
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


def _anular(tarjetas, ahora):
    """Anula las tarjetas activas o pendientes del queryset. Devuelve sus ids."""
    ids = list(tarjetas.select_for_update().filter(
        estado__in=[Tarjeta.Estado.ACTIVA, Tarjeta.Estado.PENDIENTE])
        .order_by("pk").values_list("pk", flat=True))
    Tarjeta.objects.filter(pk__in=ids).update(estado=Tarjeta.Estado.ANULADA, bloqueada_en=ahora)
    return ids


@transaction.atomic
def anular_por_uid(uid):
    """Tarjeta física restablecida a fábrica: sus emisiones dejan de ser válidas."""
    ids = _anular(Tarjeta.objects.filter(uid=uid), timezone.now())
    _bloqueos(ids, urgente=True)
    return len(ids)


# --- Sincronización con validadores -------------------------------------------

def sincronizar(validador, eventos):
    """Aplica los eventos del validador y devuelve lo que necesita para operar
    sin conexión. Idempotente: los eventos ya recibidos se ignoran."""
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
    return {
        "validador": validador.numero,
        "aceptados": aceptados,
        "lista_negra": list(Tarjeta.objects.filter(
            estado__in=[Tarjeta.Estado.BLOQUEADA, Tarjeta.Estado.ANULADA])
            .order_by("id").values_list("id", flat=True)),
        "recargas": list(Recarga.objects.filter(aplicada_en__isnull=True, reembolsada_en__isnull=True)
                         .order_by("cuenta_id", "seq").values("cuenta_id", "seq", "monto")),
        "tarifas": {str(t.codigo): [t.nombre, t.precio] for t in Tarifa.objects.all()},
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
