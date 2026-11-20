from django.conf import settings
from django.db import migrations, models
from django.db.models import Q
import django.db.models.deletion
from django.utils import timezone


def conservar_ultimo_codigo(apps, schema_editor):
    Codigo = apps.get_model("clientes", "CodigoActivacion")
    vistos = set()
    ahora = timezone.now()
    for registro in Codigo.objects.using(schema_editor.connection.alias).filter(
            usado_en__isnull=True).order_by("tarjeta_id", "-id"):
        if registro.tarjeta_id in vistos:
            Codigo.objects.using(schema_editor.connection.alias).filter(pk=registro.pk).update(
                anulado_en=ahora)
        vistos.add(registro.tarjeta_id)


class Migration(migrations.Migration):
    dependencies = [
        ("clientes", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(
            model_name="codigoactivacion", name="anulado_en",
            field=models.DateTimeField(null=True, blank=True)),
        migrations.AddField(
            model_name="codigoactivacion", name="emitido_por",
            field=models.ForeignKey(to=settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=django.db.models.deletion.PROTECT,
                                    related_name="codigos_emitidos")),
        migrations.RunPython(conservar_ultimo_codigo, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="codigoactivacion",
            constraint=models.UniqueConstraint(
                fields=["tarjeta"], condition=Q(usado_en__isnull=True, anulado_en__isnull=True),
                name="codigo_activacion_activo_unico")),
    ]
