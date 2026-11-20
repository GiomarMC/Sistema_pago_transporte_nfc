from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("panel", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="solicitudreposicion",
            name="tarjeta_nueva",
            field=models.ForeignKey(
                to="transporte.tarjeta", null=True, blank=True,
                related_name="reposiciones_entregadas",
                on_delete=django.db.models.deletion.PROTECT,
            ),
        ),
    ]
