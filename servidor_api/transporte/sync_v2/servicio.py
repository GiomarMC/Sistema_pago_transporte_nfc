"""
Procesamiento de los paquetes C1 que suben los validadores (M9-03).

procesar() es la única entrada: la usan el endpoint HTTP (M9-04), el puente MQTT
(M9-06) y el relé del conductor (M16). Recibe los bytes de un paquete, sin importar
quién lo trajo, y devuelve los paquetes de respuesta ya firmados por el servidor.

- La firma se verifica con la clave del validador de ORIGEN, no con la del
  portador: un bus vecino (ESP-NOW) o el celular del conductor (BLE) solo
  transportan bytes y no pueden alterarlos sin que la firma falle.
- EVENTOS → cada evento nuevo pasa por servicios.procesar_evento() (la misma
  lógica de cobro, fraude y recargas que la v1) → ACUSE.
- HOLA → ACUSE + LISTAS pendientes (o foto completa) + TARIFAS si cambiaron.
- Un paquete inválido no lanza excepción: deja una Alerta y no responde nada.
"""

from datetime import datetime, timezone as tz

from django.db import IntegrityError, connection, transaction
from django.db.models import Max
from django.utils import timezone

from transporte import servicios
from transporte.models import CambioLista, EventoRecibido, Recarga, Tarifa, Tarjeta, Validador

from . import claves
from .paquetes import (ACUSE, AGREGAR, BLOQUEO, EVENTOS, FRAUDE, HOLA, INCIDENCIA, LISTAS,
                       MAX_ENTRADAS, PENDIENTE, RECARGA, SERVIDOR, SIN_CONTADOR, TARIFAS, VIAJE,
                       Acuse, Cabecera, EntradaLista, Listas, PaqueteInvalido, Tarifa as TarifaC1,
                       Tarifas, desempaquetar, empaquetar)

VIAS = {v.value for v in EventoRecibido.Via}

# Con más de esto de atraso se manda la foto completa en vez de los cambios (C1 5.5)
ATRASO_FOTO = 2000

# Versiones intermedias de una foto completa en varios paquetes: el primero va
# con version_desde = 0 y los siguientes encadenados. Las intermedias son
# mayores que 2^31 (nunca una versión real): si el validador se queda a medias,
# su próximo HOLA trae una versión "del futuro" y recibe otra foto completa.
_FOTO_INTERMEDIA = 0x8000_0000

_TIPOS_V1 = {VIAJE: "viaje", INCIDENCIA: "incidencia", FRAUDE: "fraude"}


def procesar(datos: bytes, via: str, portador: int | None = None) -> list[bytes]:
    """Procesa un paquete de un validador y devuelve las respuestas para su origen.

    via: mqtt, http, ble o espnow. portador: n.º del validador que lo subió, si
    no es el origen (paquete ajeno); None si lo trajo el propio origen o un celular.
    """
    if via not in VIAS:
        raise ValueError(f"via desconocida: {via!r}")
    try:
        paquete = desempaquetar(datos, claves.clave_hmac_de, clave_publica=None)
        if paquete.cabecera.destino != SERVIDOR:
            raise PaqueteInvalido("destino", f"{paquete.cabecera.destino} (debe ser 0)")
    except PaqueteInvalido as e:
        _alertar_invalido(e, datos, via, portador)
        return []

    cab = paquete.cabecera
    validador = Validador.objects.get(numero=cab.origen, activo=True)
    if cab.tipo == EVENTOS:
        return _eventos(validador, paquete.cuerpo.eventos, via)
    if cab.tipo == HOLA:
        return _hola(validador, paquete.cuerpo, via, portador)
    raise AssertionError(f"tipo {cab.tipo:#04x} aceptado por desempaquetar")  # pragma: no cover


def _alertar_invalido(error, datos, via, portador):
    # Cabecera C1: magia(2) versión(1) tipo(1) origen(2) → bytes 4-5
    origen = int.from_bytes(datos[4:6], "little") if len(datos) >= 6 else None
    servicios.alertar(None, "paquete inválido",
                      f"{error} · origen {origen} · vía {via}"
                      + (f" · portador {portador}" if portador is not None else "")
                      + f" · {len(datos)} B")


# --- EVENTOS ----------------------------------------------------------------------------

def evento_a_v1(e) -> dict:
    """Evento de C1 → el dict que acepta servicios.procesar_evento() (API v1)."""
    return {
        "id": e.id_evento,
        "tipo": _TIPOS_V1.get(e.tipo, f"desconocido ({e.tipo})"),
        "fecha": datetime.fromtimestamp(e.fecha, tz.utc).isoformat(),
        "tarjeta_id": e.id_tarjeta,
        "uid": e.uid.hex().upper(),
        # En C1 un cobro va en negativo (vector eventos_1: -80); la v1 lo quiere
        # positivo y lo resta ella. abs(): un viaje nunca puede abonar saldo.
        "monto": abs(e.monto),
        "saldo_final": e.saldo_final,
        "operacion": e.operacion,
        "contador": None if e.contador == SIN_CONTADOR else e.contador,
        "recarga_hasta": e.recarga_hasta or None,
        "detalle": e.detalle,
    }


def _eventos(validador, eventos, via) -> list[bytes]:
    ya = set(EventoRecibido.objects.filter(
        validador=validador, id_evento__in=[e.id_evento for e in eventos])
        .values_list("id_evento", flat=True))
    for e in eventos:
        if e.id_evento in ya:
            continue
        ya.add(e.id_evento)
        try:
            with transaction.atomic():
                EventoRecibido.objects.create(validador=validador, id_evento=e.id_evento, via=via)
                servicios.procesar_evento(validador.numero, evento_a_v1(e))
        except IntegrityError:
            # Otro portador subió el mismo evento a la vez: ya está registrado
            if EventoRecibido.objects.filter(validador=validador, id_evento=e.id_evento).exists():
                continue
            _evento_fallido(validador, e, via, "conflicto al guardar")
        except Exception as error:  # dato corrupto: se confirma para que no se reintente siempre
            _evento_fallido(validador, e, via, repr(error))
    return [_acuse(validador, _avanzar_contiguo(validador))]


def _evento_fallido(validador, e, via, motivo):
    servicios.alertar(None, "evento inválido", f"V{validador.numero}-{e.id_evento}: {motivo}")
    EventoRecibido.objects.get_or_create(validador=validador, id_evento=e.id_evento,
                                         defaults={"via": via})


@transaction.atomic
def _avanzar_contiguo(validador) -> int:
    """Sube ultimo_evento_contiguo mientras estén todos los ids siguientes (C1 5.7)."""
    v = Validador.objects.select_for_update().get(pk=validador.pk)
    contiguo = v.ultimo_evento_contiguo
    for id_evento in EventoRecibido.objects.filter(
            validador=v, id_evento__gt=contiguo).order_by("id_evento") \
            .values_list("id_evento", flat=True).iterator():
        if id_evento != contiguo + 1:
            break
        contiguo = id_evento
    if contiguo != v.ultimo_evento_contiguo:
        v.ultimo_evento_contiguo = contiguo
        v.save(update_fields=["ultimo_evento_contiguo"])
    validador.ultimo_evento_contiguo = contiguo
    return contiguo


# --- HOLA -------------------------------------------------------------------------------

def _hola(validador, hola, via, portador) -> list[bytes]:
    ahora = timezone.now()
    validador.ultimo_hola = {
        "version_listas": hola.version_listas, "version_tarifas": hola.version_tarifas,
        "ultimo_evento": hola.ultimo_evento, "pendientes": hola.pendientes,
        "ajenos": hola.ajenos, "enlace": hola.enlace, "senal": hola.senal,
        "bytes_tx_hoy": hola.bytes_tx_hoy, "bytes_rx_hoy": hola.bytes_rx_hoy,
        "version_firmware": hola.version_firmware,
        "via": via, "portador": portador, "recibido": ahora.isoformat(timespec="seconds"),
    }
    validador.version_listas = hola.version_listas
    validador.ultimo_enlace = hola.enlace if hola.enlace in Validador.Enlace.values else None
    validador.ultima_sync = ahora
    validador.save(update_fields=["ultimo_hola", "version_listas", "ultimo_enlace",
                                  "ultima_sync"])

    respuestas = [_acuse(validador, _avanzar_contiguo(validador))]
    respuestas += [firmar(LISTAS, validador.numero, cuerpo)
                   for cuerpo in listas_para(hola.version_listas)]
    tarifas = tarifas_actuales()
    if tarifas.version != hola.version_tarifas:
        respuestas.append(firmar(TARIFAS, validador.numero, tarifas))
    return respuestas


def listas_para(version: int) -> list[Listas]:
    """Los LISTAS que necesita un validador que va por `version` (C1 5.5)."""
    # La versión actual se lee ANTES que el estado: si entra un cambio en medio,
    # la foto ya lo incluye y el validador lo vuelve a aplicar sin efecto.
    actual = CambioLista.objects.aggregate(m=Max("version"))["m"] or 0
    if version == actual:
        return []
    if version > actual or actual - version > ATRASO_FOTO:
        return _foto_completa(actual)

    paquetes, desde = [], version
    while desde < actual:
        cambios = servicios.cambios_desde(desde, limite=MAX_ENTRADAS)
        # Sin cambios = huecos de la secuencia (transacciones deshechas): se avanza igual
        hasta = cambios[-1].version if cambios else actual
        paquetes.append(Listas(desde, hasta, tuple(
            EntradaLista(c.tipo, c.op, c.a, c.b, c.c) for c in cambios)))
        desde = hasta
    return paquetes


def _foto_completa(actual: int) -> list[Listas]:
    """Estado de hoy, solo con op = 1: tarjetas en la lista negra y recargas pendientes."""
    entradas = [EntradaLista(BLOQUEO, AGREGAR, i) for i in Tarjeta.objects.filter(
        estado__in=[Tarjeta.Estado.BLOQUEADA, Tarjeta.Estado.ANULADA])
        .order_by("id").values_list("id", flat=True)]
    entradas += [EntradaLista(RECARGA, PENDIENTE, c, s, m) for c, s, m in Recarga.objects.filter(
        aplicada_en__isnull=True, reembolsada_en__isnull=True).order_by("cuenta_id", "seq")
        .values_list("cuenta_id", "seq", "monto")]
    trozos = [entradas[i:i + MAX_ENTRADAS] for i in range(0, len(entradas), MAX_ENTRADAS)] or [[]]
    base = _FOTO_INTERMEDIA | ((actual & 0xFFFF) << 15)   # hasta 32767 paquetes
    paquetes = []
    for i, trozo in enumerate(trozos):
        desde = 0 if i == 0 else base + i
        hasta = actual if i == len(trozos) - 1 else base + i + 1
        paquetes.append(Listas(desde, hasta, tuple(trozo)))
    return paquetes


def tarifas_actuales() -> Tarifas:
    """TARIFAS completo; su versión es la marca unix de la última modificación (C1 5.6)."""
    lista = list(Tarifa.objects.order_by("codigo"))
    ultima = max((t.modificada for t in lista), default=None)
    return Tarifas(int(ultima.timestamp()) if ultima else 0,
                   tuple(TarifaC1(t.codigo, t.precio, t.nombre) for t in lista))


# --- Respuestas firmadas ----------------------------------------------------------------

def _acuse(validador, hasta_evento) -> bytes:
    return firmar(ACUSE, validador.numero, Acuse(hasta_evento, int(timezone.now().timestamp())))


def firmar(tipo, destino, cuerpo) -> bytes:
    cabecera = Cabecera(tipo, origen=SERVIDOR, destino=destino,
                        secuencia=siguiente_secuencia(), creado=int(timezone.now().timestamp()))
    return empaquetar(cabecera, cuerpo, clave_ecdsa=claves.clave_privada_listas())


def siguiente_secuencia() -> int:
    """Secuencia de los paquetes del servidor: creciente y sin repetirse (C1 5.1)."""
    with connection.cursor() as c:
        c.execute("SELECT nextval('transporte_secuencia_servidor')")
        return c.fetchone()[0]
