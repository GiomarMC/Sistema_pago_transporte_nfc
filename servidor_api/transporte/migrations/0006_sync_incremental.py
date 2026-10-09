"""Registro de cambios y carga inicial para validadores que empiezan con cursor 0."""

from django.db import migrations, models


def cargar_estado_actual(apps, schema_editor):
    alias = schema_editor.connection.alias
    Cambio = apps.get_model("transporte", "CambioSync")
    Estado = apps.get_model("transporte", "EstadoSync")
    Tarjeta = apps.get_model("transporte", "Tarjeta")
    Recarga = apps.get_model("transporte", "Recarga")
    Tarifa = apps.get_model("transporte", "Tarifa")
    version = 0
    lote = []

    def agregar(tipo, accion, datos):
        nonlocal version
        version += 1
        lote.append(Cambio(version=version, tipo=tipo, accion=accion, datos=datos))
        if len(lote) >= 500:
            Cambio.objects.using(alias).bulk_create(lote)
            lote.clear()

    for tarjeta_id in (Tarjeta.objects.using(alias).filter(estado__in=["bloqueada", "anulada"])
                       .order_by("id").values_list("id", flat=True).iterator(chunk_size=500)):
        agregar("tarjeta", "poner", {"tarjeta_id": tarjeta_id})

    for cuenta_id, seq, monto in (Recarga.objects.using(alias).filter(aplicada_en__isnull=True)
                                  .order_by("cuenta_id", "seq")
                                  .values_list("cuenta_id", "seq", "monto")
                                  .iterator(chunk_size=500)):
        agregar("recarga", "poner", {"cuenta_id": cuenta_id, "seq": seq, "monto": monto})

    for codigo, nombre, precio in (Tarifa.objects.using(alias).order_by("codigo")
                                   .values_list("codigo", "nombre", "precio")
                                   .iterator(chunk_size=500)):
        agregar("tarifa", "poner", {"codigo": codigo, "nombre": nombre, "precio": precio})

    if lote:
        Cambio.objects.using(alias).bulk_create(lote)
    Estado.objects.using(alias).create(id=1, ultima_version=version)


class Migration(migrations.Migration):
    dependencies = [("transporte", "0005_grupo_solicitudes")]

    operations = [
        migrations.CreateModel(
            name="EstadoSync",
            fields=[
                ("id", models.PositiveSmallIntegerField(default=1, primary_key=True, serialize=False)),
                ("ultima_version", models.BigIntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name="CambioSync",
            fields=[
                ("version", models.BigIntegerField(primary_key=True, serialize=False)),
                ("tipo", models.CharField(max_length=10)),
                ("accion", models.CharField(max_length=10)),
                ("datos", models.JSONField()),
            ],
        ),
        migrations.RunPython(cargar_estado_actual, migrations.RunPython.noop),
    ]
