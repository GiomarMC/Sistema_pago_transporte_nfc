"""Cambios que necesitan los validadores para operar sin conexión.

Todas las escrituras de estado deben pasar por servicios.py (o registrar aquí
el cambio en la misma transacción). La fila EstadoSync bloqueada hace que la
versión siga el orden de confirmación, incluso con escritores concurrentes.
"""

from django.db import transaction

from .models import CambioSync, EstadoSync


def registrar_cambio(tipo, accion, datos):
    with transaction.atomic():
        estado = EstadoSync.objects.select_for_update().get(pk=1)
        version = estado.ultima_version + 1
        CambioSync.objects.create(version=version, tipo=tipo, accion=accion, datos=datos)
        estado.ultima_version = version
        estado.save(update_fields=["ultima_version"])
    return version


def registrar_tarjeta(tarjeta_id, bloqueada=True):
    return registrar_cambio("tarjeta", "poner" if bloqueada else "quitar",
                           {"tarjeta_id": tarjeta_id})


def registrar_recarga(recarga, pendiente):
    datos = {"cuenta_id": recarga.cuenta_id, "seq": recarga.seq}
    if pendiente:
        datos["monto"] = recarga.monto
    return registrar_cambio("recarga", "poner" if pendiente else "quitar", datos)


def registrar_tarifa(tarifa, activa=True):
    datos = {"codigo": tarifa.codigo}
    if activa:
        datos.update(nombre=tarifa.nombre, precio=tarifa.precio)
    return registrar_cambio("tarifa", "poner" if activa else "quitar", datos)


def pagina(cursor, hasta_version, limite):
    """Devuelve una página de un corte estable del registro de cambios."""
    filas = list(CambioSync.objects.filter(version__gt=cursor, version__lte=hasta_version)
                 .order_by("version")[:limite + 1])
    hay_mas = len(filas) > limite
    filas = filas[:limite]
    siguiente = filas[-1].version if hay_mas else hasta_version
    cambios = [
        {"version": fila.version, "tipo": fila.tipo, "accion": fila.accion, **fila.datos}
        for fila in filas
    ]
    return cambios, siguiente, hay_mas
