from html.parser import HTMLParser

from django.test import SimpleTestCase, override_settings
from django.contrib.staticfiles import finders


class Identificadores(HTMLParser):
    def __init__(self):
        super().__init__()
        self.identificadores = []

    def handle_starttag(self, etiqueta, atributos):
        identificador = dict(atributos).get("id")
        if identificador:
            self.identificadores.append(identificador)


@override_settings(PORTAL_EN_RUTA_MI=True)
class PaginasPortalTests(SimpleTestCase):
    rutas = (
        "", "ingresar/", "activar/", "recuperar/", "restablecer/",
        "movimientos/", "recargar/", "recargar/resultado/", "tarjeta/",
        "seguridad/", "perfil/",
    )

    def test_rutas_y_estructura(self):
        for ruta in self.rutas:
            with self.subTest(ruta=ruta):
                respuesta = self.client.get(f"/mi/{ruta}?simulado=1")
                self.assertEqual(respuesta.status_code, 200)
                self.assertContains(respuesta, 'id="contenido"')
                self.assertContains(respuesta, "portal/js/paginas.js")
                self.assertIn("csrftoken", respuesta.cookies)

    def test_verificacion_sun_sigue_disponible(self):
        # Sin p ni m el enlace no es válido; los demás casos están en tests_sun.py
        self.assertEqual(self.client.get("/mi/t").status_code, 400)

    def test_identificadores_unicos_en_cada_pagina(self):
        for ruta in self.rutas:
            with self.subTest(ruta=ruta):
                pagina = Identificadores()
                pagina.feed(self.client.get(f"/mi/{ruta}?simulado=1").content.decode())
                self.assertEqual(len(pagina.identificadores), len(set(pagina.identificadores)))

    def test_archivos_estaticos_del_portal(self):
        for nombre in ("css/portal.css", "js/api-simulada.js", "js/api.js", "js/cliente.js",
                       "js/app.js", "js/paginas.js", "js/intentos.js"):
            with self.subTest(nombre=nombre):
                self.assertIsNotNone(finders.find(f"portal/{nombre}"))
