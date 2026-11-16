# Cuentas contables fijas, una SALDO_CLIENTE_<id> por cada cuenta de cliente ya
# existente, y triggers de PostgreSQL que rechazan UPDATE y DELETE de asientos y
# partidas: la inmutabilidad no depende solo del ORM (ni el admin, ni un
# QuerySet.update(), ni SQL a mano pueden reescribir el libro).
#
# Los saldos de apertura NO van aquí: son un asiento por cliente (M10-04).

from django.db import migrations

FIJAS = [
    ("CAJA_PASARELA", "Caja de la pasarela de pagos (recargas web)", "activo"),
    ("CAJA_VENTANILLA", "Caja de ventanilla y puntos de recarga (efectivo)", "activo"),
    ("INGRESOS_PASAJES", "Ingresos por pasajes", "ingreso"),
    # Contrapartida de los saldos de apertura y de las correcciones de operadores
    ("AJUSTES", "Ajustes y saldos de apertura", "activo"),
]

TRIGGERS = """
CREATE FUNCTION libro_inmutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'libro mayor: % en % no permitido (registro inmutable; corrige con un reverso)',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
END $$;
CREATE TRIGGER libro_asiento_inmutable BEFORE UPDATE OR DELETE ON libro_asiento
    FOR EACH ROW EXECUTE FUNCTION libro_inmutable();
CREATE TRIGGER libro_partida_inmutable BEFORE UPDATE OR DELETE ON libro_partida
    FOR EACH ROW EXECUTE FUNCTION libro_inmutable();
"""

SIN_TRIGGERS = """
DROP TRIGGER libro_partida_inmutable ON libro_partida;
DROP TRIGGER libro_asiento_inmutable ON libro_asiento;
DROP FUNCTION libro_inmutable();
"""


def crear_cuentas(apps, schema_editor):
    CuentaContable = apps.get_model("libro", "CuentaContable")
    Cuenta = apps.get_model("transporte", "Cuenta")
    CuentaContable.objects.bulk_create(
        [CuentaContable(codigo=c, nombre=n, naturaleza=t) for c, n, t in FIJAS]
        + [CuentaContable(codigo=f"SALDO_CLIENTE_{i}", nombre=f"Saldo del cliente {i}",
                          naturaleza="pasivo", cuenta_cliente_id=i)
           for i in Cuenta.objects.order_by("id").values_list("id", flat=True)])


def borrar_cuentas(apps, schema_editor):
    apps.get_model("libro", "CuentaContable").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("libro", "0001_modelos"),
    ]

    operations = [
        migrations.RunPython(crear_cuentas, borrar_cuentas),
        migrations.RunSQL(TRIGGERS, SIN_TRIGGERS),
    ]
