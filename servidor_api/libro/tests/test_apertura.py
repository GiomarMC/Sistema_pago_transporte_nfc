"""M10-04: migración de apertura de saldos."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Sum
from django.test import TransactionTestCase

from libro import libro
from libro.models import Asiento, CuentaContable, Partida
from transporte.models import Cuenta, Movimiento, Tarifa

ANTES = [("libro", "0002_cuentas_e_inmutabilidad")]
DESPUES = [("libro", "0003_apertura")]


def migrar(destino):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(destino)


class Apertura(TransactionTestCase):
    # El flush de TransactionTestCase borra las cuentas fijas y tarifas de las
    # migraciones; así vuelven para los tests que siguen
    serialized_rollback = True

    def setUp(self):
        migrar(ANTES)
        self.tarifa = Tarifa.objects.order_by("codigo").first()

    def tearDown(self):
        migrar(DESPUES)

    def cuenta(self, documento, saldo):
        """Cliente de antes del libro: con saldo, pero sin asientos."""
        return Cuenta.objects.create(nombre="x", documento=documento, tarifa=self.tarifa,
                                     saldo=saldo)

    def assertCuadra(self):
        self.assertEqual(Partida.objects.aggregate(s=Sum("monto"))["s"] or 0, 0)
        for c in Cuenta.objects.all():
            self.assertEqual(libro.saldo_cliente(c), c.saldo, f"cuenta {c.id}")

    def test_un_asiento_por_cliente_con_saldo(self):
        ana, beto, cero = self.cuenta("1", 920), self.cuenta("2", -30), self.cuenta("3", 0)
        migrar(DESPUES)

        self.assertCuadra()
        self.assertEqual(set(Asiento.objects.values_list("ref", flat=True)),
                         {f"apertura-{ana.id}", f"apertura-{beto.id}"})
        asiento = Asiento.objects.get(ref=f"apertura-{ana.id}")
        self.assertEqual(asiento.tipo, Asiento.Tipo.APERTURA)
        self.assertEqual(sorted(asiento.partidas.values_list("cuenta_contable__codigo", "monto")),
                         [("AJUSTES", 920), (f"SALDO_CLIENTE_{ana.id}", -920)])
        self.assertEqual(libro.saldo_cliente(cero), 0)

    def test_solo_asienta_lo_que_el_libro_aun_no_explica(self):
        ana = self.cuenta("1", 1000)
        # Un viaje ya asentado por registrar_movimiento antes de la migración
        libro.registrar(Asiento.Tipo.VIAJE, "V101-1",
                        [(f"SALDO_CLIENTE_{ana.id}", 130), ("INGRESOS_PASAJES", -130)])
        Cuenta.objects.filter(pk=ana.pk).update(saldo=870)
        migrar(DESPUES)

        self.assertCuadra()
        self.assertEqual(Partida.objects.get(asiento__ref=f"apertura-{ana.id}",
                                             cuenta_contable__codigo="AJUSTES").monto, 1000)

    def test_volver_a_ejecutarla_no_duplica(self):
        self.cuenta("1", 920)
        migrar(DESPUES)
        migrar(ANTES)       # deshacer no borra nada (el libro es inmutable)
        migrar(DESPUES)
        self.assertEqual(Asiento.objects.count(), 1)
        self.assertCuadra()

    def test_cliente_sin_cuenta_contable_la_recibe(self):
        # Una cuenta creada sin pasar por la señal (p. ej. con loaddata)
        ana = self.cuenta("1", 500)
        CuentaContable.objects.filter(cuenta_cliente=ana).delete()
        migrar(DESPUES)
        self.assertEqual(CuentaContable.objects.get(cuenta_cliente=ana).codigo,
                         f"SALDO_CLIENTE_{ana.id}")
        self.assertCuadra()

    def test_cuentas_con_historial_sin_libro(self):
        # Como en producción: movimientos viejos que nunca pasaron por el libro
        ana = self.cuenta("1", 870)
        Movimiento.objects.create(fecha="2026-10-01T08:00Z", cuenta=ana, tipo="viaje",
                                  monto=-130, saldo_final=870)
        migrar(DESPUES)
        self.assertCuadra()
