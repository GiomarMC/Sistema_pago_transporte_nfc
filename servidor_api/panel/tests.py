from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from transporte.models import Alerta, Cuenta, Movimiento, Tarifa, Tarjeta, Validador


class PanelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.operador = User.objects.create_user(username="operadora", password="prueba-segura-123")
        self.operador.groups.add(Group.objects.get(name="operadores"))
        self.ajeno = User.objects.create_user(username="ajeno", password="prueba-segura-123")
        tarifa = Tarifa.objects.get(pk=1)
        self.cuenta = Cuenta.objects.create(nombre="Ana Quispe", documento="71234567", tarifa=tarifa, saldo=920)
        self.tarjeta = Tarjeta.objects.create(uid="04AABBCCDDEEFF", cuenta=self.cuenta, estado="activa")

    def viaje(self, numero, cuenta=None, tarjeta=None):
        return Movimiento.objects.create(
            fecha=timezone.now(), cuenta=cuenta or self.cuenta, tarjeta=tarjeta or self.tarjeta,
            tipo="viaje", monto=-80, saldo_final=920, operacion=numero + 1,
            validador=101, ref=f"V101-{numero}",
        )

    def test_acceso_reservado_a_operadores(self):
        self.assertEqual(self.client.get(reverse("panel:inicio")).status_code, 302)
        self.client.force_login(self.ajeno)
        self.assertEqual(self.client.get(reverse("panel:inicio")).status_code, 403)
        self.client.force_login(self.operador)
        self.assertEqual(self.client.get(reverse("panel:inicio")).status_code, 200)

    def test_busqueda_exacta_por_dni_y_uid(self):
        self.client.force_login(self.operador)
        ruta = reverse("panel:cuentas")
        for consulta in ("71234567", "04AABBCCDDEEFF", str(self.cuenta.pk)):
            self.assertContains(self.client.get(ruta, {"q": consulta}), "Ana Quispe")
        self.assertNotContains(self.client.get(ruta, {"q": "7123"}), "Ana Quispe")

    def test_paginas_principales_renderizan(self):
        self.client.force_login(self.operador)
        rutas = [
            reverse("panel:cuenta_detalle", args=[self.cuenta.pk]),
            reverse("panel:validadores"),
            reverse("panel:alertas"),
            reverse("panel:viajes"),
        ]
        for ruta in rutas:
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 200)

    def test_resumen_muestra_datos_reales(self):
        self.viaje(1)
        Alerta.objects.create(tipo="incidencia", detalle="revisar", tarjeta=self.tarjeta)
        usuario_bus = get_user_model().objects.create(username="bus-101")
        Validador.objects.create(numero=101, usuario=usuario_bus,
                                 ultima_sync=timezone.now() - timedelta(minutes=10))
        self.client.force_login(self.operador)
        respuesta = self.client.get(reverse("panel:inicio"))
        self.assertEqual(respuesta.context["viajes_hoy"], 1)
        self.assertEqual(respuesta.context["cobrado_hoy"], 80)
        self.assertEqual(respuesta.context["alertas_abiertas"], 1)
        self.assertEqual(respuesta.context["validadores_atrasados"], 1)

    def test_viajes_paginados_y_filtrados(self):
        for numero in range(31):
            self.viaje(numero)
        self.client.force_login(self.operador)
        ruta = reverse("panel:viajes")
        primera = self.client.get(ruta, {"q": self.cuenta.documento})
        self.assertEqual(len(primera.context["viajes"]), 30)
        self.assertIsNotNone(primera.context["siguiente"])
        segunda = self.client.get(ruta, {"antes": primera.context["siguiente"]})
        self.assertEqual(len(segunda.context["viajes"]), 1)

    def test_bloqueo_requiere_post_y_motivo(self):
        self.client.force_login(self.operador)
        ruta = reverse("panel:bloquear_tarjeta", args=[self.tarjeta.pk])
        self.assertEqual(self.client.get(ruta).status_code, 405)
        self.assertEqual(self.client.post(ruta, {"motivo": ""}).status_code, 400)
        self.assertEqual(self.client.post(ruta, {"motivo": "reporte de pérdida"}).status_code, 302)
        self.tarjeta.refresh_from_db()
        self.assertEqual(self.tarjeta.estado, "bloqueada")
        self.assertTrue(Alerta.objects.filter(tarjeta=self.tarjeta, tipo="bloqueo manual desde el panel").exists())

    def test_revision_de_alerta(self):
        alerta = Alerta.objects.create(tipo="incidencia", detalle="revisar")
        self.client.force_login(self.operador)
        ruta = reverse("panel:revisar_alerta", args=[alerta.pk])
        self.assertEqual(self.client.get(ruta).status_code, 405)
        self.assertEqual(self.client.post(ruta).status_code, 302)
        alerta.refresh_from_db()
        self.assertTrue(alerta.revisada)
        self.assertEqual(alerta.revisada_por, self.operador)
        self.assertIsNotNone(alerta.revisada_en)

    def test_csrf_protege_bloqueo(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.operador)
        ruta = reverse("panel:bloquear_tarjeta", args=[self.tarjeta.pk])
        self.assertEqual(cliente.post(ruta, {"motivo": "pérdida"}).status_code, 403)
        self.tarjeta.refresh_from_db()
        self.assertEqual(self.tarjeta.estado, "activa")
