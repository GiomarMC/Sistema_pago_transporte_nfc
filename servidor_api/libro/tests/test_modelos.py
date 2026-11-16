"""M10-01: modelos contables inmutables."""

from django.contrib.auth import get_user_model
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from libro.models import Asiento, CuentaContable, Partida, RegistroInmutable
from transporte import servicios
from transporte.models import Cuenta, Tarifa


class Base(TestCase):
    def setUp(self):
        self.cuenta = Cuenta.objects.create(nombre="Ana", documento="45678912",
                                            tarifa=Tarifa.objects.order_by("codigo").first())
        self.saldo = CuentaContable.objects.get(cuenta_cliente=self.cuenta)
        self.caja = CuentaContable.objects.get(codigo="CAJA_PASARELA")

    def recarga(self, ref="R1", monto=1000):
        asiento = Asiento.objects.create(tipo=Asiento.Tipo.RECARGA, ref=ref,
                                         descripcion="recarga web")
        Partida.objects.create(asiento=asiento, cuenta_contable=self.caja, monto=monto)
        Partida.objects.create(asiento=asiento, cuenta_contable=self.saldo, monto=-monto)
        return asiento

    def assertRechazaSql(self, sql, *params):
        with self.assertRaises(DatabaseError) as error, transaction.atomic():
            with connection.cursor() as c:
                c.execute(sql, params)
        self.assertIn("inmutable", str(error.exception))


class Cuentas(Base):
    def test_cuentas_fijas_creadas_por_migracion(self):
        fijas = dict(CuentaContable.objects.filter(cuenta_cliente__isnull=True)
                     .values_list("codigo", "naturaleza"))
        self.assertEqual(fijas, {"CAJA_PASARELA": "activo", "CAJA_VENTANILLA": "activo",
                                 "INGRESOS_PASAJES": "ingreso", "AJUSTES": "activo"})

    def test_cuenta_de_cliente_nueva_tiene_su_saldo_cliente(self):
        self.assertEqual((self.saldo.codigo, self.saldo.naturaleza),
                         (f"SALDO_CLIENTE_{self.cuenta.id}", "pasivo"))
        self.assertEqual(self.cuenta.cuenta_contable, self.saldo)

    def test_guardar_otra_vez_la_cuenta_no_duplica(self):
        self.cuenta.saldo = 500
        self.cuenta.save()
        self.assertEqual(CuentaContable.objects.filter(cuenta_cliente=self.cuenta).count(), 1)

    def test_la_cuenta_de_un_cliente_debe_ser_pasivo(self):
        otra = Cuenta.objects.create(nombre="Beto", documento="1", tarifa=self.cuenta.tarifa)
        CuentaContable.objects.filter(cuenta_cliente=otra).delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            CuentaContable.objects.create(codigo="X", nombre="x", naturaleza="activo",
                                          cuenta_cliente=otra)

    def test_emision_cancelada_borra_la_cuenta_y_su_saldo_cliente(self):
        tarjeta = servicios.reservar_emision("04AA", nombre="Beto", documento="2",
                                             codigo_tarifa=self.cuenta.tarifa.codigo)
        id_cuenta = tarjeta.cuenta_id
        self.assertTrue(CuentaContable.objects.filter(cuenta_cliente_id=id_cuenta).exists())
        servicios.cancelar_emision(tarjeta.id)
        self.assertFalse(Cuenta.objects.filter(pk=id_cuenta).exists())
        self.assertFalse(CuentaContable.objects.filter(cuenta_cliente_id=id_cuenta).exists())

    def test_cuenta_con_partidas_no_se_puede_borrar(self):
        self.recarga()
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.saldo.delete()


class Asientos(Base):
    def test_asiento_con_sus_partidas(self):
        asiento = self.recarga(monto=1000)
        self.assertEqual(sorted(asiento.partidas.values_list("cuenta_contable__codigo", "monto")),
                         [("CAJA_PASARELA", 1000), (self.saldo.codigo, -1000)])
        self.assertIsNotNone(asiento.fecha)
        self.assertIsNotNone(asiento.registrado)

    def test_ref_unica(self):
        self.recarga(ref="R1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Asiento.objects.create(tipo=Asiento.Tipo.RECARGA, ref="R1")

    def test_partida_de_monto_cero_se_rechaza(self):
        asiento = Asiento.objects.create(tipo=Asiento.Tipo.AJUSTE, ref="A1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Partida.objects.create(asiento=asiento, cuenta_contable=self.caja, monto=0)

    def test_usuario_con_asientos_no_se_borra(self):
        operador = get_user_model().objects.create(username="op")
        Asiento.objects.create(tipo=Asiento.Tipo.AJUSTE, ref="A1", creado_por=operador)
        with self.assertRaises(ProtectedError), transaction.atomic():
            operador.delete()


class Inmutabilidad(Base):
    def test_modificar_un_asiento_falla(self):
        asiento = self.recarga()
        asiento.descripcion = "otra cosa"
        with self.assertRaises(RegistroInmutable):
            asiento.save()
        asiento.refresh_from_db()
        self.assertEqual(asiento.descripcion, "recarga web")

    def test_modificar_una_partida_falla(self):
        partida = self.recarga().partidas.get(cuenta_contable=self.caja)
        partida.monto = 1
        with self.assertRaises(RegistroInmutable):
            partida.save()
        partida.refresh_from_db()
        self.assertEqual(partida.monto, 1000)

    def test_borrar_un_asiento_o_una_partida_falla(self):
        asiento = self.recarga()
        with self.assertRaises(RegistroInmutable):
            asiento.delete()
        with self.assertRaises(RegistroInmutable):
            asiento.partidas.first().delete()
        self.assertEqual(Partida.objects.filter(asiento=asiento).count(), 2)

    def test_update_y_delete_masivos_fallan(self):
        self.recarga()
        for qs in (Asiento.objects.all(), Partida.objects.all()):
            with self.subTest(qs.model.__name__):
                with self.assertRaises(RegistroInmutable):
                    qs.update(id=1)
                with self.assertRaises(RegistroInmutable):
                    qs.delete()
        self.assertEqual((Asiento.objects.count(), Partida.objects.count()), (1, 2))

    def test_sql_directo_tampoco_puede_reescribir_el_libro(self):
        asiento = self.recarga()
        self.assertRechazaSql("UPDATE libro_partida SET monto = 1 WHERE asiento_id = %s",
                              asiento.id)
        self.assertRechazaSql("DELETE FROM libro_partida WHERE asiento_id = %s", asiento.id)
        self.assertRechazaSql("UPDATE libro_asiento SET ref = 'X' WHERE id = %s", asiento.id)
        self.assertRechazaSql("DELETE FROM libro_asiento WHERE id = %s", asiento.id)
        self.assertEqual(sorted(asiento.partidas.values_list("monto", flat=True)), [-1000, 1000])


class Admin(Base):
    def setUp(self):
        super().setUp()
        self.asiento = self.recarga()
        self.client.force_login(get_user_model().objects.create_superuser("admin", "", "x"))

    def test_se_ve_pero_no_se_puede_crear_cambiar_ni_borrar(self):
        a = self.asiento.id
        self.assertEqual(self.client.get("/admin/libro/asiento/").status_code, 200)
        vista = self.client.get(f"/admin/libro/asiento/{a}/change/")
        self.assertEqual(vista.status_code, 200)
        self.assertNotContains(vista, 'name="_save"')
        self.assertContains(vista, "CAJA_PASARELA")
        self.assertEqual(self.client.get("/admin/libro/asiento/add/").status_code, 403)
        self.assertEqual(self.client.post(f"/admin/libro/asiento/{a}/delete/",
                                          {"post": "yes"}).status_code, 403)
        self.assertEqual(self.client.post(f"/admin/libro/asiento/{a}/change/",
                                          {"descripcion": "x"}).status_code, 403)
        self.asiento.refresh_from_db()
        self.assertEqual(self.asiento.descripcion, "recarga web")

    def test_lista_de_cuentas_con_su_suma(self):
        r = self.client.get("/admin/libro/cuentacontable/")
        self.assertContains(r, "SALDO_CLIENTE_")
        self.assertContains(r, "S/ 10.00")
        self.assertContains(r, "-S/ 10.00")
