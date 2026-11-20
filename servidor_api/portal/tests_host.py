from django.test import SimpleTestCase, override_settings


@override_settings(ALLOWED_HOSTS=["subepe.app", "mi.subepe.app", "testserver"],
                   DOMINIO_CLIENTE="mi.subepe.app",
                   DOMINIOS_PRINCIPALES={"subepe.app"})
class HostPortalTests(SimpleTestCase):
    def test_portal_y_operaciones_separados_por_host(self):
        portal = {"HTTP_HOST": "mi.subepe.app"}
        principal = {"HTTP_HOST": "subepe.app"}
        self.assertEqual(self.client.get("/", **portal).status_code, 200)
        self.assertEqual(self.client.get("/ingresar/", **portal).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/resumen/", **portal).status_code, 401)
        self.assertEqual(self.client.get("/panel/", **portal).status_code, 404)
        self.assertEqual(self.client.get("/admin/", **portal).status_code, 404)
        self.assertEqual(self.client.get("/panel/", **principal).status_code, 302)
        self.assertEqual(self.client.get("/mi/", **principal).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/resumen/", **principal).status_code, 404)

    def test_cabeceras_y_errores_json_del_portal(self):
        portal = {"HTTP_HOST": "mi.subepe.app"}
        respuesta = self.client.get("/ingresar/", **portal)
        self.assertEqual(respuesta["Content-Security-Policy"],
                         "default-src 'self'; frame-ancestors 'none'")
        self.assertEqual(respuesta["Referrer-Policy"], "same-origin")
        self.assertEqual(respuesta["X-Content-Type-Options"], "nosniff")
        ausente = self.client.get("/api/v1/no-existe/", **portal)
        self.assertEqual(ausente.status_code, 404)
        self.assertEqual(ausente.json()["error"]["codigo"], "no_encontrado")

    @override_settings(SESSION_COOKIE_SECURE=True)
    def test_hsts_cuando_https_esta_activo(self):
        respuesta = self.client.get("/", HTTP_HOST="mi.subepe.app")
        self.assertEqual(respuesta["Strict-Transport-Security"], "max-age=31536000")
