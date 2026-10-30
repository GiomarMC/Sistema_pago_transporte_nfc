from io import StringIO
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from libro.libro import (ConflictoAsiento, abrir_saldo_existente, cuenta_cliente,
                         registrar, revertir, saldo_cliente)
from libro.models import Asiento, CuentaContable, InmutableError, Partida
from transporte import servicios
from transporte.models import Alerta, Cuenta, Movimiento, Tarifa


class LibroTests(TestCase):
    def setUp(self):
        self.cuenta = Cuenta.objects.create(nombre="Prueba", documento="M10-1",
                                            tarifa=Tarifa.objects.get(pk=1))
        self.cliente = cuenta_cliente(self.cuenta)

    def test_registro_equilibrado_idempotente_y_conflicto(self):
        p = [("CAJA_PASARELA", 1000), (self.cliente.codigo, -1000)]
        a = registrar("ajuste", "prueba-1", p)
        self.assertTrue(a.confirmado)
        self.assertEqual(a.pk, registrar("ajuste", "prueba-1", list(reversed(p))).pk)
        self.assertEqual(Asiento.objects.filter(ref="prueba-1").count(), 1)
        with self.assertRaises(ConflictoAsiento):
            registrar("ajuste", "prueba-1", [("CAJA_PASARELA", 900),
                                            (self.cliente.codigo, -900)])
        with self.assertRaises(ValueError):
            registrar("ajuste", "mal", [("CAJA_PASARELA", 1000),
                                         (self.cliente.codigo, -999)])
        with self.assertRaises(ValueError):
            registrar("ajuste", "una", [("CAJA_PASARELA", 0)])

    def test_recarga_viaje_reenvio_y_reverso(self):
        recarga = servicios.crear_recarga(self.cuenta.pk, 1000, "web")
        self.assertEqual(saldo_cliente(self.cuenta), 1000)
        self.assertEqual(Cuenta.objects.get(pk=self.cuenta.pk).saldo, 1000)
        viaje = servicios.registrar_movimiento(self.cuenta, None, "viaje", -130, ref="V1-1")
        self.assertEqual(servicios.registrar_movimiento(self.cuenta, None, "viaje", -130,
                                                       ref="V1-1").pk, viaje.pk)
        with self.assertRaises(servicios.ErrorNegocio):
            servicios.registrar_movimiento(self.cuenta, None, "viaje", -140, ref="V1-1")
        self.assertEqual(saldo_cliente(self.cuenta), 870)
        asiento = Asiento.objects.get(ref=viaje.ref)
        self.assertTrue(Partida.objects.filter(asiento=asiento,
            cuenta_contable__codigo="PASAJES_POR_LIQUIDAR", monto=-130).exists())
        reverso = revertir(asiento, "viaje anulado")
        self.assertEqual(revertir(asiento, "viaje anulado").pk, reverso.pk)
        self.assertEqual(saldo_cliente(self.cuenta), 1000)
        self.assertEqual(Cuenta.objects.get(pk=self.cuenta.pk).saldo, 1000)
        self.assertEqual(Movimiento.objects.filter(ref="rev-V1-1").count(), 1)
        self.assertEqual(Asiento.objects.get(ref=f"R{recarga.pk}").partidas.count(), 2)
        call_command("verificar_libro", stdout=StringIO())

    def test_emision_sin_monto_y_apertura_importada(self):
        servicios.registrar_movimiento(self.cuenta, None, "emision", 0)
        self.assertEqual(Asiento.objects.count(), 0)
        otra = Cuenta.objects.create(nombre="Histórica", documento="M10-2",
                                     tarifa=Tarifa.objects.get(pk=1))
        abrir_saldo_existente(otra, 525)
        self.assertEqual(saldo_cliente(otra), 525)
        with self.assertRaises(ConflictoAsiento):
            abrir_saldo_existente(otra, 525)
        call_command("verificar_libro", stdout=StringIO())

    def test_reverso_de_referencia_larga(self):
        ref = "x" * 76
        servicios.registrar_movimiento(self.cuenta, None, "recarga", 100,
                                       ref=ref, origen="web")
        revertir(Asiento.objects.get(ref=ref), "corrección")
        self.assertTrue(Movimiento.objects.filter(ref="rev-" + ref).exists())
        self.assertEqual(saldo_cliente(self.cuenta), 0)

    def test_fallo_contable_revierte_saldo_y_movimiento(self):
        with self.assertRaises(servicios.ErrorNegocio):
            servicios.registrar_movimiento(self.cuenta, None, "recarga", 10.5, ref="float")
        with patch("libro.libro.registrar", side_effect=ValueError("fallo")):
            with self.assertRaises(ValueError):
                servicios.registrar_movimiento(self.cuenta, None, "viaje", -130, ref="V1-2")
        self.assertEqual(Cuenta.objects.get(pk=self.cuenta.pk).saldo, 0)
        self.assertFalse(Movimiento.objects.filter(ref="V1-2").exists())

    def test_inmutabilidad_modelo_queryset_y_sql(self):
        a = registrar("ajuste", "inmutable", [("AJUSTES", 1), (self.cliente.codigo, -1)])
        a.descripcion = "alterado"
        with self.assertRaises(InmutableError):
            a.save()
        with self.assertRaises(InmutableError):
            a.delete()
        with self.assertRaises(DatabaseError), transaction.atomic():
            Asiento.objects.filter(pk=a.pk).update(descripcion="SQL")
        with self.assertRaises(DatabaseError), transaction.atomic():
            Partida.objects.filter(asiento=a).delete()
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("UPDATE libro_partida SET monto = monto + 1 WHERE asiento_id = %s", [a.pk])
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("DELETE FROM libro_asiento WHERE id = %s", [a.pk])
        with self.assertRaises(DatabaseError), transaction.atomic():
            Partida.objects.create(asiento=a, cuenta_contable=self.cliente, monto=1)
        with self.assertRaises(DatabaseError), transaction.atomic():
            CuentaContable.objects.filter(pk=self.cliente.pk).update(codigo="ALTERADA")

    def test_sql_no_puede_confirmar_asiento_desequilibrado(self):
        a = Asiento.objects.create(fecha=timezone.now(),
                                   tipo="ajuste", ref="desequilibrado")
        Partida.objects.create(asiento=a, cuenta_contable=self.cliente, monto=5)
        with self.assertRaises(DatabaseError), transaction.atomic():
            Asiento.objects.filter(pk=a.pk).update(confirmado=True)
        Partida.objects.filter(asiento=a).delete()
        Asiento.objects.filter(pk=a.pk).delete()

    def test_verificador_detecta_desfase_sql(self):
        servicios.crear_recarga(self.cuenta.pk, 1000, "web")
        with connection.cursor() as cur:
            cur.execute("SET LOCAL subepe.libro_escritura = 'on'")
            cur.execute("UPDATE transporte_cuenta SET saldo = saldo + 1 WHERE id = %s", [self.cuenta.pk])
        with self.assertRaises(CommandError):
            call_command("verificar_libro", stdout=StringIO(), stderr=StringIO())
        self.assertTrue(Alerta.objects.filter(tipo="descuadre contable").exists())

    def test_guarda_sql_de_saldo_se_cierra_tras_movimiento(self):
        servicios.crear_recarga(self.cuenta.pk, 1000, "web")
        with self.assertRaises(DatabaseError), transaction.atomic():
            Cuenta.objects.filter(pk=self.cuenta.pk).update(saldo=1001)
        self.assertEqual(Cuenta.objects.get(pk=self.cuenta.pk).saldo, 1000)


class AperturaMigrationTests(TransactionTestCase):
    def test_apertura_historica_no_duplica_movimientos(self):
        from django.db.migrations.executor import MigrationExecutor

        if not Tarifa.objects.filter(pk=1).exists():
            Tarifa.objects.bulk_create([Tarifa(codigo=1, nombre="general", precio=130)])
        executor = MigrationExecutor(connection)
        executor.migrate([("libro", "0002_asiento_confirmado")])
        try:
            tarifa = Tarifa.objects.get(pk=1)
            cuenta = Cuenta.objects.create(nombre="Previa", documento="M10-previa",
                                           tarifa=tarifa, saldo=720)
            negativa = Cuenta.objects.create(nombre="Deudora", documento="M10-negativa",
                                             tarifa=tarifa, saldo=-130)
            cero = Cuenta.objects.create(nombre="Cero", documento="M10-cero",
                                         tarifa=tarifa, saldo=0)
            Movimiento.objects.create(fecha=timezone.now(),
                                      cuenta=cuenta, tipo="recarga", monto=720,
                                      saldo_final=720, ref="R-historica")
            executor = MigrationExecutor(connection)
            executor.migrate([("libro", "0003_integridad_y_apertura")])
            self.assertEqual(Asiento.objects.filter(ref=f"apertura-{cuenta.pk}").count(), 1)
            self.assertFalse(Asiento.objects.filter(ref="R-historica").exists())
            self.assertEqual(saldo_cliente(cuenta), 720)
            self.assertEqual(saldo_cliente(negativa), -130)
            self.assertEqual(saldo_cliente(cero), 0)
            self.assertFalse(Asiento.objects.filter(ref=f"apertura-{cero.pk}").exists())
            MigrationExecutor(connection).migrate([("libro", "0003_integridad_y_apertura")])
            self.assertEqual(Asiento.objects.filter(tipo="apertura").count(), 2)
            call_command("verificar_libro", stdout=StringIO())
        finally:
            MigrationExecutor(connection).migrate([("libro", "0003_integridad_y_apertura")])


class ConcurrenciaTests(TransactionTestCase):
    def test_misma_referencia_concurrente_aplica_una_vez(self):
        if not Tarifa.objects.filter(pk=1).exists():
            Tarifa.objects.bulk_create([Tarifa(codigo=1, nombre="general", precio=130)])
        CuentaContable.objects.get_or_create(codigo="CAJA_PASARELA", defaults={
            "nombre": "Caja pasarela", "naturaleza": "activo"})
        cuenta = Cuenta.objects.create(nombre="Concurrente", documento="M10-concurrente",
                                       tarifa=Tarifa.objects.get(pk=1))

        def recargar():
            try:
                return servicios.registrar_movimiento(
                    Cuenta.objects.get(pk=cuenta.pk), None, "recarga", 100,
                    ref="R-concurrente", origen="web").pk
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(lambda _: recargar(), range(2)))
        self.assertEqual(resultados[0], resultados[1])
        self.assertEqual(Cuenta.objects.get(pk=cuenta.pk).saldo, 100)
        self.assertEqual(saldo_cliente(cuenta), 100)
        self.assertEqual(Asiento.objects.filter(ref="R-concurrente").count(), 1)
