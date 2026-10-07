# Grupo de las cuentas registradas desde el panel que esperan aprobación

from django.db import migrations


def crear(apps, schema_editor):
    apps.get_model("auth", "Group").objects.get_or_create(name="solicitudes")


def borrar(apps, schema_editor):
    apps.get_model("auth", "Group").objects.filter(name="solicitudes").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("transporte", "0004_alerta_revision"),
    ]

    operations = [migrations.RunPython(crear, borrar)]
