from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from django.urls import reverse

PANEL = "subepe.app"
PORTAL = "mi.subepe.app"


@override_settings(ALLOWED_HOSTS=[PANEL, PORTAL])
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
