"""#152 (M20-02): la página /t muestra saldo y último movimiento solo al dueño de la tarjeta."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from clientes.models import Cliente
from transporte.models import Cuenta, Movimiento, Tarifa, Tarjeta
from transporte.tests import tarjeta_sun

K3, MAESTRA_K4 = bytes.fromhex("33" * 16), bytes.fromhex("44" * 16)
UID = bytes.fromhex("04B3E121CA2A81")


@override_settings(PORTAL_EN_RUTA_MI=True, SUN_K3=K3.hex(), SUN_K4_MAESTRAS="1:" + MAESTRA_K4.hex())
class VistaDelDuenoTests(TestCase):
    def setUp(self):
        tarifa = Tarifa.objects.get_or_create(codigo=1, defaults={"nombre": "General", "precio": 130})[0]
        self.cuenta = Cuenta.objects.create(nombre="Ana Quispe", documento="71234567", tarifa=tarifa, saldo=1234)
        self.tarjeta = Tarjeta.objects.create(uid=UID.hex().upper(), cuenta=self.cuenta,
                                              estado=Tarjeta.Estado.ACTIVA)
        self.dueno = self.cliente_de(self.cuenta)
        otra = Cuenta.objects.create(nombre="Luis Mamani", documento="87654321", tarifa=tarifa, saldo=9999)
        self.otro = self.cliente_de(otra)
        ahora = timezone.now()
        Movimiento.objects.create(fecha=ahora - timedelta(hours=2), cuenta=self.cuenta, tipo="recarga",
                                  monto=1364, saldo_final=1364, ref="R-dueno")
        Movimiento.objects.create(fecha=ahora, cuenta=self.cuenta, tarjeta=self.tarjeta, tipo="viaje",
                                  monto=-130, saldo_final=1234, validador=103, ref="V103-7")
        Movimiento.objects.create(fecha=ahora + timedelta(minutes=1), cuenta=otra, tipo="recarga",
                                  monto=9999, saldo_final=9999, ref="R-otro")
        self.contador = 0

    @staticmethod
    def cliente_de(cuenta):
        usuario = get_user_model().objects.create_user(username=f"cli-{cuenta.documento}", password="x")
        Cliente.objects.create(usuario=usuario, cuenta=cuenta, correo=f"{cuenta.documento}@example.test",
                               correo_verificado=True)
        return usuario

    def abrir_enlace(self):
        self.contador += 1
        p, m = tarjeta_sun.enlace(K3, tarjeta_sun.k4_de_tarjeta(MAESTRA_K4, UID), UID, self.contador)
        return self.client.get(f"/mi/t?p={p}&m={m}")

    def assertSoloVistaPublica(self, respuesta):
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Tarjeta SubePe auténtica")
        self.assertIsNone(respuesta.context["dueno"])
        for texto in ("12.34", "99.99", "Último movimiento", "Ana", "Luis", 'id="datos-dueno"'):
            self.assertNotContains(respuesta, texto)
        self.assertContains(respuesta, "Ver mi saldo")

    def test_propietario_autenticado_ve_su_saldo_y_ultimo_movimiento(self):
        self.client.force_login(self.dueno)
        respuesta = self.abrir_enlace()
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Tarjeta SubePe auténtica")
        self.assertContains(respuesta, '<strong id="saldo-dueno">S/ 12.34</strong>', html=False)
        self.assertContains(respuesta, "Viaje · bus V103 · −S/ 1.30")
        self.assertContains(respuesta, timezone.localtime(Movimiento.objects.get(ref="V103-7").fecha)
                            .strftime("%d/%m/%Y %H:%M"))
        self.assertNotContains(respuesta, "99.99")   # nada de la otra cuenta
        self.assertNotContains(respuesta, "Ver mi saldo")
        self.tarjeta.refresh_from_db()
        self.assertEqual(self.tarjeta.contador_sdm, 1)

    def test_otro_cliente_autenticado_solo_ve_la_vista_publica(self):
        self.client.force_login(self.otro)
        self.assertSoloVistaPublica(self.abrir_enlace())

    def test_visitante_sin_sesion_solo_ve_la_vista_publica(self):
        self.assertSoloVistaPublica(self.abrir_enlace())

    def test_dueno_sin_movimientos(self):
        Movimiento.objects.filter(cuenta=self.cuenta).delete()
        self.client.force_login(self.dueno)
        respuesta = self.abrir_enlace()
        self.assertContains(respuesta, "Todavía no hay movimientos.")

    def test_dueno_con_enlace_repetido_no_ve_nada(self):
        self.client.force_login(self.dueno)
        self.abrir_enlace()
        self.contador -= 1                        # el mismo enlace otra vez
        respuesta = self.abrir_enlace()
        self.assertEqual(respuesta.status_code, 400)
        self.assertNotContains(respuesta, "12.34", status_code=400)

    def test_operador_del_panel_no_es_dueno(self):
        operador = get_user_model().objects.create_user(username="operador-sun", password="x", is_staff=True)
        self.client.force_login(operador)
        self.assertSoloVistaPublica(self.abrir_enlace())
