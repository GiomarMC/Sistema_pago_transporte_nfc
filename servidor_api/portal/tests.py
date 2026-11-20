from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from django.urls import reverse

PANEL = "subepe.app"
PORTAL = "mi.subepe.app"


@override_settings(ALLOWED_HOSTS=[PANEL, PORTAL], DOMINIO_CLIENTE=PORTAL,
                   DOMINIOS_PRINCIPALES={PANEL})
class RuteoPorHostTests(TestCase):
    def setUp(self):
        self.operador = get_user_model().objects.create_user(
            username="operadora", password="prueba-segura-123")
        self.operador.groups.add(Group.objects.get(name="operadores"))

    def test_el_portal_solo_expone_sus_rutas(self):
        portal = Client(HTTP_HOST=PORTAL)
        self.assertEqual(portal.get("/").status_code, 200)
        self.assertEqual(portal.get("/t").status_code, 501)
        for ruta in ("/panel/", "/panel/ingresar/", "/admin/", "/api/sync/"):
            with self.subTest(ruta=ruta):
                self.assertEqual(portal.get(ruta).status_code, 404)

    def test_el_dominio_principal_no_expone_el_portal(self):
        panel = Client(HTTP_HOST=PANEL)
        self.assertEqual(panel.get("/t").status_code, 404)
        self.assertRedirects(panel.get("/"), reverse("panel:inicio"), fetch_redirect_response=False)

    def test_host_no_permitido(self):
        self.assertEqual(Client(HTTP_HOST="mi.otro.com").get("/").status_code, 400)

    def test_la_cookie_de_sesion_vale_solo_para_su_host(self):
        self.assertIsNone(settings.SESSION_COOKIE_DOMAIN)
        panel = Client(HTTP_HOST=PANEL)
        respuesta = panel.post(reverse("panel:login"),
                               {"username": "operadora", "password": "prueba-segura-123"})
        cookie = respuesta.cookies[settings.SESSION_COOKIE_NAME]
        self.assertEqual(cookie["domain"], "")  # sin Domain: el navegador no la envía a mi.
        # Aunque la cookie llegara al portal, ahí no hay rutas de operador
        portal = Client(HTTP_HOST=PORTAL)
        portal.cookies = panel.cookies
        self.assertEqual(portal.get("/panel/").status_code, 404)


class RutaMiSoloEnDesarrolloTests(TestCase):
    """El portal bajo /mi/ del dominio principal no se expone en producción (decisión D7)."""

    @override_settings(PORTAL_EN_RUTA_MI=False)
    def test_en_produccion_mi_responde_404(self):
        self.assertEqual(self.client.get("/mi/ingresar/").status_code, 404)
        self.assertEqual(self.client.get("/mi/api/v1/resumen/").status_code, 404)

    @override_settings(PORTAL_EN_RUTA_MI=False, DOMINIO_CLIENTE="mi.subepe.app")
    def test_la_pagina_antigua_de_reposiciones_redirige_al_portal(self):
        # Era una página de cliente de M19: ahora es "Mi tarjeta" en el host del portal
        respuesta = self.client.get("/mi/reposiciones/")
        self.assertRedirects(respuesta, "http://mi.subepe.app/tarjeta/", status_code=301,
                             fetch_redirect_response=False)

    @override_settings(DOMINIO_CLIENTE="mi.subepe.app", DOMINIOS_PRINCIPALES={"subepe.app"},
                       ALLOWED_HOSTS=["subepe.app", "mi.subepe.app"])
    def test_el_dominio_principal_tambien_redirige_conservando_el_puerto(self):
        respuesta = self.client.get("/mi/reposiciones/", HTTP_HOST="subepe.app:8443")
        self.assertRedirects(respuesta, "http://mi.subepe.app:8443/tarjeta/", status_code=301,
                             fetch_redirect_response=False)

    @override_settings(PORTAL_EN_RUTA_MI=True)
    def test_en_desarrollo_mi_sirve_el_portal(self):
        self.assertEqual(self.client.get("/mi/ingresar/").status_code, 200)
