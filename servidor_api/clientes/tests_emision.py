from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from clientes.models import Cliente, CodigoActivacion
from transporte.models import Cuenta, Tarifa, Tarjeta


class EmisionBancariaTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.operador = User.objects.create_user(username="emisor-m18", password="clave-prueba")
        self.operador.groups.add(Group.objects.get_or_create(name="operadores")[0])
        self.otro = User.objects.create_user(username="ajeno-m18", password="clave-prueba")
        self.api = APIClient()
        self.api.force_authenticate(self.operador)
        tarifa = Tarifa.objects.get_or_create(pk=1, defaults={"nombre": "General", "precio": 150})[0]
        self.cuenta = Cuenta.objects.create(nombre="Ana Quispe", documento="71234567", tarifa=tarifa)
        self.tarjeta = Tarjeta.objects.create(uid="04AABBCC123456", cuenta=self.cuenta,
                                              estado=Tarjeta.Estado.PENDIENTE)

    def confirmar(self):
        return self.api.post(f"/api/emisiones/{self.tarjeta.pk}/confirmar/", {}, format="json")

    def test_codigo_se_entrega_una_vez_y_solo_se_guarda_hash(self):
        respuesta = self.confirmar()
        self.assertEqual(respuesta.status_code, 200)
        codigo = respuesta.data["codigo_activacion"]
        self.assertRegex(codigo, r"^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}$")
        self.assertEqual(len(respuesta.data["numero_impreso"].replace(" ", "")), 9)
        registro = CodigoActivacion.objects.get(tarjeta=self.tarjeta)
        self.assertNotEqual(registro.hash, codigo)
        self.assertTrue(registro.vigente(codigo))
        self.assertEqual(registro.emitido_por, self.operador)
        self.assertEqual(self.confirmar().status_code, 409)
        self.assertEqual(CodigoActivacion.objects.filter(tarjeta=self.tarjeta).count(), 1)

    def test_regeneracion_invalida_el_anterior_y_registra_operador(self):
        primero = self.confirmar().data["codigo_activacion"]
        url = f"/api/tarjetas/{self.tarjeta.pk}/codigo-activacion/"
        respuesta = self.api.post(url, {}, format="json")
        self.assertEqual(respuesta.status_code, 200)
        segundo = respuesta.data["codigo_activacion"]
        anteriores = list(CodigoActivacion.objects.filter(tarjeta=self.tarjeta).order_by("id"))
        self.assertEqual(len(anteriores), 2)
        self.assertFalse(anteriores[0].vigente(primero))
        self.assertTrue(anteriores[1].vigente(segundo))
        self.assertEqual(anteriores[1].emitido_por, self.operador)
        self.api.force_authenticate(self.otro)
        self.assertEqual(self.api.post(url, {}, format="json").status_code, 403)

    def test_cuenta_con_cliente_no_recibe_codigo(self):
        Cliente.objects.create(usuario=self.otro, cuenta=self.cuenta, correo="ana@example.test")
        respuesta = self.confirmar()
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn("codigo_activacion", respuesta.data)
        self.assertFalse(CodigoActivacion.objects.filter(tarjeta=self.tarjeta).exists())
        self.assertEqual(self.api.post(f"/api/tarjetas/{self.tarjeta.pk}/codigo-activacion/",
                                       {}, format="json").status_code, 409)

    def test_comprobante_no_revela_dni_y_requiere_codigo_vigente(self):
        codigo = self.confirmar().data["codigo_activacion"]
        url = f"/api/tarjetas/{self.tarjeta.pk}/comprobante/"
        respuesta = self.api.post(url, {"codigo": codigo}, format="json")
        self.assertEqual(respuesta.status_code, 200)
        html = respuesta.content.decode()
        self.assertIn(codigo, html)
        self.assertNotIn(self.cuenta.documento, html)
        self.assertIn("mi.subepe.app/activar", html)
        self.assertIn("<svg", html)
        self.assertEqual(respuesta["Cache-Control"], "no-store")
        CodigoActivacion.objects.filter(tarjeta=self.tarjeta).update(expira=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.api.post(url, {"codigo": codigo}, format="json").status_code, 409)

    def test_codigo_regenerado_no_sirve_para_imprimir(self):
        viejo = self.confirmar().data["codigo_activacion"]
        self.api.post(f"/api/tarjetas/{self.tarjeta.pk}/codigo-activacion/", {}, format="json")
        self.assertEqual(self.api.post(f"/api/tarjetas/{self.tarjeta.pk}/comprobante/",
                                       {"codigo": viejo}, format="json").status_code, 409)

    def test_tarjeta_bloqueada_no_permite_imprimir_codigo(self):
        codigo = self.confirmar().data["codigo_activacion"]
        Tarjeta.objects.filter(pk=self.tarjeta.pk).update(estado=Tarjeta.Estado.BLOQUEADA)
        self.assertEqual(self.api.post(f"/api/tarjetas/{self.tarjeta.pk}/comprobante/",
                                       {"codigo": codigo}, format="json").status_code, 409)

    def test_panel_imprime_y_regenera_solo_para_operadores(self):
        primero = self.confirmar().data["codigo_activacion"]
        url = reverse("panel:regenerar_comprobante", args=[self.tarjeta.pk])
        self.client.force_login(self.otro)
        self.assertEqual(self.client.post(url).status_code, 403)
        self.client.force_login(self.operador)
        impreso = self.client.post(reverse("panel:comprobante"),
                                   {"tarjeta": self.tarjeta.pk, "codigo": primero})
        self.assertEqual(impreso.status_code, 200)
        self.assertContains(impreso, primero)
        nuevo = self.client.post(url)
        self.assertEqual(nuevo.status_code, 200)
        self.assertNotIn(primero, nuevo.content.decode())
        self.assertEqual(self.client.post(reverse("panel:comprobante"),
                                          {"tarjeta": self.tarjeta.pk,
                                           "codigo": primero}).status_code, 409)
