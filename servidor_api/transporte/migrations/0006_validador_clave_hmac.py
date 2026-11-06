# Clave HMAC propia de cada validador (M0-06). En tres pasos: con un default calculado,
# AddField daría la MISMA clave a todos los validadores existentes.

import django.core.validators
from django.db import migrations, models

import transporte.models


def dar_claves(apps, schema_editor):
    Validador = apps.get_model("transporte", "Validador")
    for v in Validador.objects.filter(clave_hmac__isnull=True):
        v.clave_hmac = transporte.models.nueva_clave_hmac()
        v.save(update_fields=["clave_hmac"])


class Migration(migrations.Migration):

    dependencies = [
        ("transporte", "0005_grupo_solicitudes"),
    ]

    operations = [
        migrations.AddField(
            model_name="validador",
            name="clave_hmac",
            field=models.CharField(editable=False, max_length=64, null=True),
        ),
        migrations.RunPython(dar_claves, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="validador",
            name="clave_hmac",
            field=models.CharField(
                default=transporte.models.nueva_clave_hmac, editable=False,
                help_text=("Clave HMAC-SHA256 de sus paquetes. Ver y rotar: "
                           "manage.py clave_validador"),
                max_length=64,
                validators=[django.core.validators.RegexValidator(
                    "^[0-9a-f]{64}$", "64 caracteres hexadecimales")],
            ),
        ),
    ]
