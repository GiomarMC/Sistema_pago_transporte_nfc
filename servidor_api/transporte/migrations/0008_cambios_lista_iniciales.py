# Versiones iniciales de las listas (M9-01).
#
# Crea un CambioLista por cada tarjeta bloqueada o anulada y por cada recarga
# pendiente, en orden de fecha, para que las versiones 1..N reproduzcan el
# estado de hoy: un validador que pida LISTAS desde la versión 0 recibe la
# misma lista negra y las mismas recargas pendientes que hoy le da la v1.
#
# No son urgentes y se marcan como ya difundidos: el puente MQTT no debe
# publicar como urgente el historial completo.

from django.db import migrations
from django.utils import timezone

BLOQUEO, RECARGA = 1, 2
AGREGAR, PENDIENTE = 1, 1


def crear_cambios(apps, schema_editor):
    Tarjeta = apps.get_model("transporte", "Tarjeta")
    Recarga = apps.get_model("transporte", "Recarga")
    CambioLista = apps.get_model("transporte", "CambioLista")
    if CambioLista.objects.exists():
        return
    ahora = timezone.now()

    cambios = []
    for t in Tarjeta.objects.filter(estado__in=["bloqueada", "anulada"]).only(
            "id", "bloqueada_en", "emitida"):
        fecha = t.bloqueada_en or t.emitida
        cambios.append((fecha, 0, t.id, dict(tipo=BLOQUEO, op=AGREGAR, a=t.id)))
    for r in Recarga.objects.filter(aplicada_en__isnull=True).only(
            "id", "cuenta_id", "seq", "monto", "creada"):
        cambios.append((r.creada, 1, r.id, dict(tipo=RECARGA, op=PENDIENTE,
                                                a=r.cuenta_id, b=r.seq, c=r.monto)))
    cambios.sort(key=lambda x: x[:3])

    # bulk_create en orden: en PostgreSQL el BigAutoField sale 1..N en ese orden
    CambioLista.objects.bulk_create(
        [CambioLista(**campos, urgente=False, creado=fecha, difundido_en=ahora)
         for fecha, _, _, campos in cambios],
        batch_size=1000,
    )


def borrar_cambios(apps, schema_editor):
    apps.get_model("transporte", "CambioLista").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("transporte", "0007_sync_v2_modelos"),
    ]

    operations = [
        migrations.RunPython(crear_cambios, borrar_cambios),
    ]
