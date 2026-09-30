# Tarifas iniciales y grupo de operadores

from django.db import migrations

TARIFAS = [(1, "general", 130), (2, "estudiante", 80)]


def crear(apps, schema_editor):
    Tarifa = apps.get_model("transporte", "Tarifa")
    for codigo, nombre, precio in TARIFAS:
        Tarifa.objects.get_or_create(codigo=codigo, defaults={"nombre": nombre, "precio": precio})
    apps.get_model("auth", "Group").objects.get_or_create(name="operadores")


def borrar(apps, schema_editor):
    apps.get_model("transporte", "Tarifa").objects.filter(
        codigo__in=[c for c, _, _ in TARIFAS]).delete()
    apps.get_model("auth", "Group").objects.filter(name="operadores").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("transporte", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(crear, borrar)]
