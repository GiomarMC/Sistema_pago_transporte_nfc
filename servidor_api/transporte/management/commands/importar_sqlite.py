import sqlite3
from datetime import datetime
from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from transporte.models import Alerta, Cuenta, Movimiento, Recarga, Tarifa, Tarjeta


def _fecha(texto):
    return datetime.fromisoformat(texto) if texto else None


class Command(BaseCommand):
    help = "Importa la base SQLite de la versión local (transporte/datos/transporte.db)"

    def add_arguments(self, parser):
        parser.add_argument("ruta", help="ruta al archivo transporte.db")

    @transaction.atomic
    def handle(self, ruta, **_):
        if Cuenta.objects.exists():
            raise CommandError("la base de datos ya tiene cuentas: la importación es solo "
                               "para una base vacía")
        bd = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
        bd.row_factory = sqlite3.Row
        tablas = {f[0] for f in bd.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        for c in bd.execute("SELECT * FROM cuentas"):
            Cuenta.objects.create(id=c["id"], nombre=c["nombre"], documento=c["documento"],
                                  tarifa=Tarifa.objects.get(codigo=c["tarifa"]), saldo=c["saldo"])
            Cuenta.objects.filter(id=c["id"]).update(creada=_fecha(c["creada"]))

        recargas = list(bd.execute("SELECT * FROM recargas")) if "recargas" in tablas else []
        for t in bd.execute("SELECT * FROM tarjetas"):
            emitida = _fecha(t["emitida"])
            # Última recarga de la cuenta que ya existía cuando se emitió la tarjeta
            previas = [r["seq"] for r in recargas
                       if r["cuenta_id"] == t["cuenta_id"] and _fecha(r["creada"]) <= emitida]
            Tarjeta.objects.create(id=t["id"], uid=t["uid"], cuenta_id=t["cuenta_id"],
                                   estado=t["estado"], bloqueada_en=_fecha(t["bloqueada_en"]),
                                   recarga_inicial=max(previas, default=0))
            Tarjeta.objects.filter(id=t["id"]).update(emitida=emitida)

        for r in recargas:
            Recarga.objects.create(id=r["id"], cuenta_id=r["cuenta_id"], seq=r["seq"],
                                   monto=r["monto"], origen=r["origen"],
                                   aplicada_en=_fecha(r["aplicada_en"]),
                                   aplicada_por=r["aplicada_por"], tarjeta_id=r["tarjeta_id"])
            Recarga.objects.filter(id=r["id"]).update(creada=_fecha(r["creada"]))

        for m in bd.execute("SELECT * FROM movimientos"):
            Movimiento.objects.create(
                id=m["id"], fecha=_fecha(m["fecha"]), cuenta_id=m["cuenta_id"],
                tarjeta_id=m["tarjeta_id"], tipo=m["tipo"], monto=m["monto"],
                saldo_final=m["saldo_final"], operacion=m["operacion"], validador=m["validador"],
                ref=m["ref"], contador=m["contador"])

        existentes = set(Tarjeta.objects.values_list("id", flat=True))
        for a in bd.execute("SELECT * FROM alertas"):
            tarjeta = a["tarjeta_id"] if a["tarjeta_id"] in existentes else None
            alerta = Alerta.objects.create(tarjeta_id=tarjeta, tipo=a["tipo"],
                                           detalle=a["detalle"] or "")
            Alerta.objects.filter(id=alerta.id).update(fecha=_fecha(a["fecha"]))

        # Los ids se copiaron tal cual: ajustar las secuencias de PostgreSQL
        sql = StringIO()
        call_command("sqlsequencereset", "transporte", stdout=sql, no_color=True)
        with connection.cursor() as cur:
            cur.execute(sql.getvalue())

        self.stdout.write(self.style.SUCCESS(
            f"Importado: {Cuenta.objects.count()} cuentas, {Tarjeta.objects.count()} tarjetas, "
            f"{Recarga.objects.count()} recargas, {Movimiento.objects.count()} movimientos, "
            f"{Alerta.objects.count()} alertas"))
