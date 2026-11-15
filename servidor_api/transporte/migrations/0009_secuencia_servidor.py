# Secuencia de los paquetes que firma el servidor (C1 5.1: creciente por emisor
# y nunca se repite, aunque se reinicie el servidor). Es una SEQUENCE de
# PostgreSQL: nextval() no se deshace con un rollback, así que nunca se repite.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("transporte", "0008_cambios_lista_iniciales"),
    ]

    operations = [
        migrations.RunSQL(
            "CREATE SEQUENCE transporte_secuencia_servidor AS bigint "
            "MINVALUE 1 MAXVALUE 4294967295 START 1",
            "DROP SEQUENCE transporte_secuencia_servidor",
        ),
    ]
