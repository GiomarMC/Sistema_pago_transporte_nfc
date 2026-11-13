from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('transporte', '0011_recarga_reembolsada_en'),
    ]

    operations = [
        migrations.AddField(
            model_name='validador',
            name='conexion_activa',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='validador',
            name='conexion_en',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
