from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("transporte", "0002_datos_iniciales")]

    operations = [
        migrations.AddIndex(
            model_name="tarjeta",
            index=models.Index(fields=["estado"], name="tarjeta_estado_idx"),
        ),
        migrations.AddIndex(
            model_name="recarga",
            index=models.Index(fields=["aplicada_en"], name="recarga_aplicada_idx"),
        ),
        migrations.AddIndex(
            model_name="movimiento",
            index=models.Index(fields=["tipo", "-fecha"], name="mov_tipo_fecha_idx"),
        ),
        migrations.AddIndex(
            model_name="movimiento",
            index=models.Index(fields=["validador", "-fecha"], name="mov_val_fecha_idx"),
        ),
        migrations.AddIndex(
            model_name="alerta",
            index=models.Index(fields=["revisada", "-fecha"], name="alerta_rev_fecha_idx"),
        ),
    ]
