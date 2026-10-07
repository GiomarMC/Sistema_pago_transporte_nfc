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

    def test_raiz_lleva_al_panel(self):
        self.assertRedirects(self.client.get("/"), reverse("panel:inicio"), target_status_code=302)

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


class UsuariosTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(username="admin", password="prueba-segura-123")
        self.operador = User.objects.create_user(username="operadora", password="prueba-segura-123")
        self.operador.groups.add(Group.objects.get(name="operadores"))

    def solicitar(self, usuario="nuevo", **extra):
        datos = {"username": usuario, "first_name": "Luis", "last_name": "Mamani",
                 "email": "", "password1": "Volcan-Misti-27", "password2": "Volcan-Misti-27"}
        datos.update(extra)
        return Client().post(reverse("panel:solicitar_acceso"), datos)

    def test_registro_queda_pendiente_sin_acceso(self):
        cliente = Client()
        respuesta = cliente.post(reverse("panel:solicitar_acceso"), {
            "username": "nuevo", "first_name": "Luis", "last_name": "Mamani",
            "password1": "Volcan-Misti-27", "password2": "Volcan-Misti-27"})
        self.assertRedirects(respuesta, reverse("panel:inicio"), target_status_code=403)
        nuevo = get_user_model().objects.get(username="nuevo")
        self.assertTrue(nuevo.groups.filter(name="solicitudes").exists())
        self.assertFalse(nuevo.groups.filter(name="operadores").exists())
        pagina = cliente.get(reverse("panel:viajes"))
        self.assertEqual(pagina.status_code, 403)
        self.assertContains(pagina, "esperando la aprobación", status_code=403)

    def test_registro_valida_datos(self):
        self.assertEqual(self.solicitar(password2="otra-cosa-99").status_code, 200)
        self.assertEqual(self.solicitar(password1="12345678", password2="12345678").status_code, 200)
        self.assertEqual(self.solicitar(first_name="").status_code, 200)
        self.assertEqual(self.solicitar(usuario="OPERADORA").status_code, 200)  # ya existe
        self.assertFalse(get_user_model().objects.filter(groups__name="solicitudes").exists())

    def test_registro_se_cierra_con_muchas_pendientes(self):
        grupo = Group.objects.get(name="solicitudes")
        for n in range(50):
            get_user_model().objects.create_user(username=f"spam{n}").groups.add(grupo)
        respuesta = self.solicitar()
        self.assertContains(respuesta, "demasiadas solicitudes")
        self.assertFalse(get_user_model().objects.filter(username="nuevo").exists())

    def test_usuarios_solo_para_superusuarios(self):
        ruta = reverse("panel:usuarios")
        self.client.force_login(self.operador)
        self.assertEqual(self.client.get(ruta).status_code, 403)
        self.assertNotContains(self.client.get(reverse("panel:inicio")), ruta)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("panel:inicio")), ruta)
        self.assertEqual(self.client.get(ruta).status_code, 200)

    def test_aprobar_y_rechazar(self):
        self.solicitar("luis")
        self.solicitar("intruso")
        User = get_user_model()
        luis, intruso = User.objects.get(username="luis"), User.objects.get(username="intruso")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("panel:usuarios")), "intruso")
        self.client.post(reverse("panel:gestionar_usuario", args=[luis.pk]), {"accion": "aprobar"})
        self.client.post(reverse("panel:gestionar_usuario", args=[intruso.pk]), {"accion": "rechazar"})
        self.assertFalse(User.objects.filter(username="intruso").exists())
        self.assertTrue(luis.groups.filter(name="operadores").exists())
        self.assertFalse(luis.groups.filter(name="solicitudes").exists())
        cliente = Client()
        cliente.force_login(luis)
        self.assertEqual(cliente.get(reverse("panel:inicio")).status_code, 200)
        # Un operador ya aprobado no se puede "rechazar" (borrar) con la acción de solicitudes
        self.client.post(reverse("panel:gestionar_usuario", args=[luis.pk]), {"accion": "rechazar"})
        self.assertTrue(User.objects.filter(username="luis").exists())

    def test_desactivar_quita_el_acceso(self):
        ruta = reverse("panel:gestionar_usuario", args=[self.operador.pk])
        self.client.force_login(self.admin)
        self.client.post(ruta, {"accion": "desactivar"})
        self.operador.refresh_from_db()
        self.assertFalse(self.operador.is_active)
        self.assertFalse(Client().login(username="operadora", password="prueba-segura-123"))
        self.client.post(ruta, {"accion": "reactivar"})
        self.operador.refresh_from_db()
        self.assertTrue(self.operador.is_active)

    def test_no_se_desactiva_a_administradores(self):
        self.client.force_login(self.admin)
        ruta = reverse("panel:gestionar_usuario", args=[self.admin.pk])
        self.assertEqual(self.client.post(ruta, {"accion": "desactivar"}).status_code, 400)
        self.assertEqual(self.client.get(ruta).status_code, 405)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
