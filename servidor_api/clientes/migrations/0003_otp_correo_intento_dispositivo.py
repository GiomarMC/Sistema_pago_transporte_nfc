from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("clientes", "0002_codigo_activacion_auditoria")]

    operations = [
        migrations.AddField(
            model_name="codigootp", name="correo_destino",
            field=models.EmailField(blank=True, max_length=254)),
        migrations.AddField(
            model_name="codigootp", name="creado",
            field=models.DateTimeField(default=django.utils.timezone.now, editable=False)),
        migrations.AddField(
            model_name="intentoacceso", name="dispositivo",
            field=models.CharField(blank=True, max_length=120)),
    ]
