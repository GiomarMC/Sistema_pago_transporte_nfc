from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("notificaciones", "0002_proximo_intento")]

    operations = [
        migrations.AddField(
            model_name="notificacion", name="clave",
            field=models.CharField(blank=True, help_text="Clave estable para evitar avisos duplicados.",
                                   max_length=120, null=True, unique=True),
        ),
    ]
