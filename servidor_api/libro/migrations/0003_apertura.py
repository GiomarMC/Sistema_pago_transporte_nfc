# Saldos de apertura (M10-04): los clientes que ya tenían saldo antes del libro
# mayor reciben un asiento "apertura-<id>" que lo explica, con AJUSTES como
# contrapartida:
#
#     Cliente 7 con S/ 9.20     SALDO_CLIENTE_7  -920    AJUSTES  +920
#
# Se asienta la diferencia entre Cuenta.saldo y lo que el libro ya dice del
# cliente (en una base sin asientos, el saldo entero). Así vale aunque algún
# movimiento ya se haya asentado antes, y volver a ejecutarla no duplica nada:
# un cliente que ya cuadra se salta.
#
# Sin vuelta atrás que borre: el libro es inmutable (lo impiden los triggers de
# 0002). Deshacerla no toca nada; rehacerla ve que ya cuadra.

from django.db import migrations
from django.db.models import Sum
from django.utils import timezone

PREFIJO_CLIENTE = "SALDO_CLIENTE_"


def abrir_saldos(apps, schema_editor):
    Cuenta = apps.get_model("transporte", "Cuenta")
    CuentaContable = apps.get_model("libro", "CuentaContable")
    Asiento = apps.get_model("libro", "Asiento")
    Partida = apps.get_model("libro", "Partida")

    ajustes = CuentaContable.objects.get(codigo="AJUSTES")
    cliente = "cuenta_contable__cuenta_cliente_id"
    en_libro = dict(Partida.objects.filter(cuenta_contable__cuenta_cliente__isnull=False)
                    .values(cliente).annotate(s=Sum("monto")).values_list(cliente, "s"))
    ahora = timezone.now()
    # select_for_update: nadie mueve un saldo mientras se abre (la migración
    # corre en una transacción)
    for id_cuenta, saldo in (Cuenta.objects.select_for_update().order_by("id")
                             .values_list("id", "saldo")):
        diferencia = saldo + en_libro.get(id_cuenta, 0)   # el libro guarda el saldo en negativo
        if diferencia == 0:
            continue
        saldo_cliente, _ = CuentaContable.objects.get_or_create(
            cuenta_cliente_id=id_cuenta,
            defaults={"codigo": f"{PREFIJO_CLIENTE}{id_cuenta}",
                      "nombre": f"Saldo del cliente {id_cuenta}", "naturaleza": "pasivo"})
        ref = f"apertura-{id_cuenta}"
        if Asiento.objects.filter(ref=ref).exists():
            # Ya se abrió y después el saldo se movió fuera del libro: no se
            # tapa con otra apertura, lo tiene que ver verificar_libro (M10-05)
            continue
        asiento = Asiento.objects.create(
            fecha=ahora, tipo="apertura", ref=ref,
            descripcion=f"Saldo de apertura del cliente {id_cuenta}")
        Partida.objects.bulk_create([
            Partida(asiento=asiento, cuenta_contable=saldo_cliente, monto=-diferencia),
            Partida(asiento=asiento, cuenta_contable=ajustes, monto=diferencia),
        ])


class Migration(migrations.Migration):

    dependencies = [
        ("libro", "0002_cuentas_e_inmutabilidad"),
    ]

    operations = [
        migrations.RunPython(abrir_saldos, migrations.RunPython.noop),
    ]
