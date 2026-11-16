"""M10-02: API del libro (registrar, saldo_cliente, revertir)."""

import threading
from datetime import datetime, timezone as tz

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase

from libro import libro
from libro.libro import AsientoDesbalanceado, ConflictoAsiento, ErrorLibro
from libro.models import Asiento, Partida
from transporte.models import Cuenta, Tarifa


def crear_cuenta(documento="45678912"):
    return Cuenta.objects.create(nombre="Ana", documento=documento,
                                 tarifa=Tarifa.objects.order_by("codigo").first())


class Base(TestCase):
    def setUp(self):
        self.cuenta = crear_cuenta()
        self.saldo = f"SALDO_CLIENTE_{self.cuenta.id}"

    def recarga(self, ref="R1", monto=1000):
        return libro.registrar(Asiento.Tipo.RECARGA, ref,
                               [("CAJA_PASARELA", monto), (self.saldo, -monto)],
                               descripcion="recarga web")

    def viaje(self, ref="V1", monto=130):
        return libro.registrar(Asiento.Tipo.VIAJE, ref,
                               [(self.saldo, monto), ("INGRESOS_PASAJES", -monto)])

    def partidas(self, asiento):
        return sorted(asiento.partidas.values_list("cuenta_contable__codigo", "monto"))


class Registrar(Base):
    def test_registra_asiento_con_sus_partidas(self):
        asiento = self.recarga()
        self.assertEqual((asiento.tipo, asiento.ref, asiento.descripcion),
                         ("recarga", "R1", "recarga web"))
        self.assertEqual(self.partidas(asiento), [("CAJA_PASARELA", 1000), (self.saldo, -1000)])

    def test_fecha_y_autor(self):
        operador = get_user_model().objects.create(username="op")
        fecha = datetime(2026, 11, 1, 8, 30, tzinfo=tz.utc)
        asiento = libro.registrar(Asiento.Tipo.VIAJE, "V1",
                                  [(self.saldo, 130), ("INGRESOS_PASAJES", -130)],
                                  fecha=fecha, creado_por=operador)
        asiento.refresh_from_db()
        self.assertEqual((asiento.fecha, asiento.creado_por), (fecha, operador))
        self.assertGreater(asiento.registrado, fecha)

    def test_mas_de_dos_partidas(self):
        asiento = libro.registrar(Asiento.Tipo.AJUSTE, "A1", [
            ("CAJA_VENTANILLA", 500), ("CAJA_PASARELA", 500), (self.saldo, -1000)])
        self.assertEqual(asiento.partidas.count(), 3)

    def test_rechaza_asiento_que_no_suma_cero(self):
        with self.assertRaises(AsientoDesbalanceado):
            libro.registrar(Asiento.Tipo.RECARGA, "R1",
                            [("CAJA_PASARELA", 1000), (self.saldo, -999)])
        self.assertFalse(Asiento.objects.exists())

    def test_rechaza_asientos_invalidos(self):
        casos = {
            "una partida": ("recarga", "R1", [("CAJA_PASARELA", 0)]),
            "sin partidas": ("recarga", "R1", []),
            "monto cero": ("ajuste", "A1", [("CAJA_PASARELA", 0), ("AJUSTES", 0)]),
            "monto en soles": ("viaje", "V1", [(self.saldo, 1.3), ("INGRESOS_PASAJES", -1.3)]),
            "monto booleano": ("viaje", "V1", [(self.saldo, True), ("AJUSTES", -1)]),
            "tipo desconocido": ("regalo", "X1", [("CAJA_PASARELA", 1), (self.saldo, -1)]),
            "ref vacía": ("recarga", "", [("CAJA_PASARELA", 1), (self.saldo, -1)]),
            "ref larga": ("recarga", "R" * 81, [("CAJA_PASARELA", 1), (self.saldo, -1)]),
            "cuenta inexistente": ("recarga", "R1", [("CAJA_X", 1), (self.saldo, -1)]),
        }
        for caso, (tipo, ref, partidas) in casos.items():
            with self.subTest(caso), self.assertRaises(ErrorLibro):
                libro.registrar(tipo, ref, partidas)
        self.assertEqual((Asiento.objects.count(), Partida.objects.count()), (0, 0))


class Idempotencia(Base):
    def test_misma_ref_y_mismas_partidas_no_duplica(self):
        primero = self.recarga()
        segundo = self.recarga()
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual((Asiento.objects.count(), Partida.objects.count()), (1, 2))

    def test_el_orden_de_las_partidas_no_importa(self):
        primero = self.recarga()
        segundo = libro.registrar(Asiento.Tipo.RECARGA, "R1",
                                  [(self.saldo, -1000), ("CAJA_PASARELA", 1000)])
        self.assertEqual(primero.pk, segundo.pk)

    def test_misma_ref_con_otras_partidas_es_conflicto(self):
        self.recarga(monto=1000)
        with self.assertRaises(ConflictoAsiento):
            self.recarga(monto=2000)
        with self.assertRaises(ConflictoAsiento):
            libro.registrar(Asiento.Tipo.RECARGA, "R1",
                            [("CAJA_VENTANILLA", 1000), (self.saldo, -1000)])
        self.assertEqual(Partida.objects.count(), 2)

    def test_misma_ref_con_otro_tipo_es_conflicto(self):
        self.recarga()
        with self.assertRaises(ConflictoAsiento):
            libro.registrar(Asiento.Tipo.AJUSTE, "R1",
                            [("CAJA_PASARELA", 1000), (self.saldo, -1000)])

    def test_dentro_de_una_transaccion_que_sigue(self):
        with transaction.atomic():
            self.recarga()
            self.recarga()
            self.viaje()
        self.assertEqual(libro.saldo_cliente(self.cuenta), 870)


class SaldoCliente(Base):
    def test_sin_movimientos_es_cero(self):
        self.assertEqual(libro.saldo_cliente(self.cuenta), 0)

    def test_recarga_y_viaje(self):
        self.recarga(monto=1000)
        self.viaje(monto=130)
        self.assertEqual(libro.saldo_cliente(self.cuenta), 870)
        self.assertEqual(libro.saldo_cliente(self.cuenta.id), 870)

    def test_solo_cuenta_las_partidas_de_ese_cliente(self):
        otra = crear_cuenta("11111111")
        self.recarga(monto=1000)
        libro.registrar(Asiento.Tipo.RECARGA, "R2",
                        [("CAJA_PASARELA", 500), (f"SALDO_CLIENTE_{otra.id}", -500)])
        self.assertEqual((libro.saldo_cliente(self.cuenta), libro.saldo_cliente(otra)),
                         (1000, 500))


class Revertir(Base):
    def test_crea_asiento_espejo_y_no_toca_el_original(self):
        original = self.viaje(monto=130)
        reverso = libro.revertir(original, "cobro duplicado")
        self.assertEqual((reverso.tipo, reverso.ref), ("reverso", "rev-V1"))
        self.assertIn("cobro duplicado", reverso.descripcion)
        self.assertEqual(self.partidas(reverso), [("INGRESOS_PASAJES", 130), (self.saldo, -130)])
        self.assertEqual(self.partidas(original), [("INGRESOS_PASAJES", -130), (self.saldo, 130)])

    def test_devuelve_el_saldo(self):
        self.recarga(monto=1000)
        libro.revertir(self.viaje(monto=130), "cobro duplicado")
        self.assertEqual(libro.saldo_cliente(self.cuenta), 1000)

    def test_revertir_dos_veces_no_duplica(self):
        viaje = self.viaje()
        self.assertEqual(libro.revertir(viaje, "a").pk, libro.revertir(viaje, "b").pk)
        self.assertEqual(Asiento.objects.filter(tipo="reverso").count(), 1)

    def test_un_reverso_no_se_revierte(self):
        reverso = libro.revertir(self.viaje(), "error")
        with self.assertRaises(ErrorLibro):
            libro.revertir(reverso, "otra vez")

    def test_necesita_motivo(self):
        with self.assertRaises(ErrorLibro):
            libro.revertir(self.viaje(), "")


class Concurrencia(TransactionTestCase):
    """Dos procesos registran la misma ref a la vez (reintento de un validador)."""

    # El flush de TransactionTestCase borra las cuentas fijas y tarifas de las
    # migraciones; así vuelven para los tests que siguen
    serialized_rollback = True

    def test_misma_ref_en_paralelo_queda_un_solo_asiento(self):
        cuenta = crear_cuenta()
        partidas = [("CAJA_PASARELA", 1000), (f"SALDO_CLIENTE_{cuenta.id}", -1000)]
        barrera = threading.Barrier(2)
        resultados, errores = [], []

        def registrar():
            try:
                with transaction.atomic():
                    barrera.wait()
                    resultados.append(libro.registrar(Asiento.Tipo.RECARGA, "R1", partidas).pk)
            except Exception as e:  # noqa: BLE001 - se revisa abajo
                errores.append(e)
            finally:
                connection.close()

        hilos = [threading.Thread(target=registrar) for _ in range(2)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        self.assertEqual(errores, [])
        self.assertEqual(len(set(resultados)), 1)
        self.assertEqual((Asiento.objects.count(), Partida.objects.count()), (1, 2))
