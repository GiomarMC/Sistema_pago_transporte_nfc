from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Count, Sum

from libro.libro import saldo_cliente
from libro.models import Asiento, CorteHistorico, Partida
from transporte.models import Alerta, Cuenta, Movimiento


class Command(BaseCommand):
    help = "Verifica doble partida, saldo por cliente y referencias monetarias"

    def handle(self, **options):
        # En producción, una foto coherente evita falsos descuadres si llega
        # un viaje mientras se recorre el libro. TestCase ya abre transacción.
        if connection.in_atomic_block:
            errores = self._verificar()
        else:
            with transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                errores = self._verificar()
        for detalle in errores:
            self.stderr.write(detalle)
            Alerta.objects.get_or_create(tipo="descuadre contable", detalle=detalle, revisada=False)
        if errores:
            raise CommandError(f"libro con {len(errores)} discrepancia(s)")
        self.stdout.write(self.style.SUCCESS(
            f"Libro correcto: {Cuenta.objects.count()} cuentas, {Asiento.objects.count()} asientos"))

    def _verificar(self):
        errores = []
        global_total = Partida.objects.aggregate(s=Sum("monto"))["s"] or 0
        if global_total:
            errores.append(f"suma global de partidas: {global_total} céntimos")
        for asiento in Asiento.objects.annotate(n=Count("partidas"), suma=Sum("partidas__monto")):
            if not asiento.confirmado or asiento.n < 2 or (asiento.suma or 0) != 0:
                errores.append(f"asiento {asiento.ref}: {asiento.n} partidas, suma {asiento.suma}")
        for cuenta in Cuenta.objects.only("id", "saldo").iterator(chunk_size=500):
            saldo_libro = saldo_cliente(cuenta)
            if cuenta.saldo != saldo_libro:
                errores.append(f"cuenta {cuenta.pk}: operativo {cuenta.saldo}, libro {saldo_libro}")
        cortes = dict(CorteHistorico.objects.values_list("cuenta_id", "ultimo_movimiento_id"))
        for mov in Movimiento.objects.exclude(monto=0).only("id", "cuenta_id", "ref", "monto") \
                .iterator(chunk_size=500):
            if mov.pk <= cortes.get(mov.cuenta_id, 0):
                continue  # ya representado por apertura; jamás se contabiliza otra vez
            if not mov.ref:
                errores.append(f"movimiento {mov.pk}: sin referencia")
                continue
            asiento = Asiento.objects.filter(ref=mov.ref).first()
            if not asiento:
                errores.append(f"movimiento {mov.pk}: falta asiento {mov.ref}")
                continue
            cliente_delta = Partida.objects.filter(asiento=asiento,
                cuenta_contable__cuenta_cliente_id=mov.cuenta_id).aggregate(s=Sum("monto"))["s"] or 0
            if cliente_delta != -mov.monto:
                errores.append(f"movimiento {mov.pk}: partida cliente {cliente_delta}, esperado {-mov.monto}")
        for asiento in Asiento.objects.filter(tipo__in=["recarga", "viaje", "reverso"]).iterator():
            if not Movimiento.objects.filter(ref=asiento.ref).exists():
                errores.append(f"asiento {asiento.ref}: falta movimiento")
        return errores
