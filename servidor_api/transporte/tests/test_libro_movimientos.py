"""M10-03: cada movimiento con dinero se asienta en el libro mayor, en la misma
transacción que el saldo."""

from unittest import mock

from django.db import transaction
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from libro import libro
from libro.models import Asiento, Partida
from transporte import servicios
from transporte.models import Cuenta, Movimiento, Recarga, Tarifa, Tarjeta


class Base(TestCase):
    def setUp(self):
        tarifa = Tarifa.objects.get_or_create(
            codigo=1, defaults={"nombre": "General", "precio": 130})[0]
        self.cuenta = Cuenta.objects.create(nombre="Ana", documento="45678912", tarifa=tarifa)
        self.saldo = f"SALDO_CLIENTE_{self.cuenta.id}"

    def tarjeta(self):
        return Tarjeta.objects.create(uid="04AA", cuenta=self.cuenta,
                                      estado=Tarjeta.Estado.ACTIVA)

    def viaje(self, tarjeta, id_evento=1, monto=130, validador=101):
        servicios.procesar_evento(validador, {
            "id": id_evento, "tipo": "viaje", "fecha": timezone.now().isoformat(),
            "tarjeta_id": tarjeta.id, "monto": monto, "operacion": id_evento + 1})

    def partidas(self, ref):
        return sorted(Partida.objects.filter(asiento__ref=ref)
                      .values_list("cuenta_contable__codigo", "monto"))

    def assertCuadra(self):
        """Lo que hará verificar_libro (M10-05): el libro suma cero y cada saldo
        en caché coincide con el del libro."""
        self.assertEqual(Partida.objects.aggregate(s=Sum("monto"))["s"] or 0, 0)
        for cuenta in Cuenta.objects.all():
            self.assertEqual(cuenta.saldo, libro.saldo_cliente(cuenta), f"cuenta {cuenta.id}")


class Contrapartidas(Base):
    def test_recarga_web_entra_a_la_caja_de_la_pasarela(self):
        r = servicios.crear_recarga(self.cuenta.id, 1000, "web")
        asiento = Asiento.objects.get(ref=f"R{r.id}")
        self.assertEqual(asiento.tipo, Asiento.Tipo.RECARGA)
        self.assertEqual(self.partidas(asiento.ref),
                         [("CAJA_PASARELA", 1000), (self.saldo, -1000)])

    def test_cada_origen_va_a_su_caja(self):
        casos = {"web": "CAJA_PASARELA", "yape": "CAJA_PASARELA", "punto": "CAJA_VENTANILLA",
                 "ventanilla": "CAJA_VENTANILLA", "emision": "CAJA_VENTANILLA"}
        for origen, caja in casos.items():
            with self.subTest(origen):
                r = servicios.crear_recarga(self.cuenta.id, 500, origen)
                self.assertEqual(self.partidas(f"R{r.id}"), [(caja, 500), (self.saldo, -500)])
        self.assertCuadra()

    def test_viaje_va_a_ingresos_por_pasajes_con_la_ref_del_validador(self):
        servicios.crear_recarga(self.cuenta.id, 1000, "web")
        self.viaje(self.tarjeta(), id_evento=57, validador=101)
        asiento = Asiento.objects.get(ref="V101-57")
        self.assertEqual(asiento.tipo, Asiento.Tipo.VIAJE)
        self.assertEqual(asiento.fecha, Movimiento.objects.get(ref="V101-57").fecha)
        self.assertEqual(self.partidas("V101-57"),
                         [("INGRESOS_PASAJES", -130), (self.saldo, 130)])
        self.assertEqual(libro.saldo_cliente(self.cuenta), 870)
        self.assertCuadra()

    def test_viaje_sin_saldo_deja_al_cliente_en_negativo_tambien_en_el_libro(self):
        self.viaje(self.tarjeta())
        self.cuenta.refresh_from_db()
        self.assertEqual((self.cuenta.saldo, libro.saldo_cliente(self.cuenta)), (-130, -130))

    def test_emision_con_saldo_inicial_va_a_la_ventanilla(self):
        mov = servicios.registrar_movimiento(self.cuenta, None, "emision", 500)
        self.assertEqual(self.partidas(f"mov-{mov.id}"),
                         [("CAJA_VENTANILLA", 500), (self.saldo, -500)])
        self.assertEqual(Asiento.objects.get(ref=f"mov-{mov.id}").tipo, Asiento.Tipo.EMISION)

    def test_movimiento_sin_ref_usa_mov_id(self):
        mov = servicios.registrar_movimiento(self.cuenta, None, "recarga", 300, origen="punto")
        self.assertTrue(Asiento.objects.filter(ref=f"mov-{mov.id}").exists())

    def test_movimiento_sin_dinero_no_se_asienta(self):
        t = servicios.reservar_emision("04BB", id_cuenta=self.cuenta.id)
        servicios.confirmar_emision(t.id)
        self.assertTrue(Movimiento.objects.filter(tipo="emision", monto=0).exists())
        self.assertFalse(Asiento.objects.exists())


class Rechazos(Base):
    def test_recarga_de_origen_desconocido_se_rechaza_sin_dejar_nada(self):
        with self.assertRaisesMessage(servicios.ErrorNegocio, "origen de recarga desconocido"):
            servicios.crear_recarga(self.cuenta.id, 1000, "bitcoin")
        self.cuenta.refresh_from_db()
        self.assertEqual((self.cuenta.saldo, Recarga.objects.count(),
                          Movimiento.objects.count(), Asiento.objects.count()), (0, 0, 0, 0))

    def test_tipo_sin_contrapartida_se_rechaza(self):
        with self.assertRaises(servicios.ErrorNegocio), transaction.atomic():
            servicios.registrar_movimiento(self.cuenta, None, "recarga", 100, origen=None)
        self.cuenta.refresh_from_db()
        self.assertEqual((self.cuenta.saldo, Movimiento.objects.count()), (0, 0))


class Atomicidad(Base):
    def test_si_falla_el_libro_no_queda_ni_saldo_ni_movimiento(self):
        with mock.patch.object(libro, "registrar", side_effect=libro.ErrorLibro("caído")):
            with self.assertRaises(libro.ErrorLibro):
                servicios.crear_recarga(self.cuenta.id, 1000, "web")
        self.cuenta.refresh_from_db()
        self.assertEqual((self.cuenta.saldo, Recarga.objects.count(),
                          Movimiento.objects.count()), (0, 0, 0))

    def test_viaje_que_falla_en_el_libro_queda_como_evento_invalido(self):
        tarjeta = self.tarjeta()
        with mock.patch.object(libro, "registrar", side_effect=libro.ErrorLibro("caído")):
            servicios.sincronizar(mock.Mock(numero=101), [{
                "id": 1, "tipo": "viaje", "fecha": timezone.now().isoformat(),
                "tarjeta_id": tarjeta.id, "monto": 130, "operacion": 2}])
        self.cuenta.refresh_from_db()
        self.assertEqual((self.cuenta.saldo, Movimiento.objects.count()), (0, 0))

    def test_si_falla_despues_del_libro_tampoco_queda_el_asiento(self):
        class Fallo(Exception):
            pass

        with self.assertRaises(Fallo), transaction.atomic():
            servicios.crear_recarga(self.cuenta.id, 1000, "web")
            raise Fallo
        self.assertEqual((Asiento.objects.count(), Partida.objects.count()), (0, 0))


class Cuadre(Base):
    def test_un_dia_de_operacion_cuadra(self):
        tarjeta = self.tarjeta()
        servicios.crear_recarga(self.cuenta.id, 2000, "web")
        servicios.crear_recarga(self.cuenta.id, 500, "punto")
        for i in range(1, 6):
            self.viaje(tarjeta, id_evento=i)
        self.viaje(tarjeta, id_evento=3)        # reenvío: idempotente
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.saldo, 2500 - 5 * 130)
        self.assertEqual(Asiento.objects.count(), 7)
        self.assertCuadra()
