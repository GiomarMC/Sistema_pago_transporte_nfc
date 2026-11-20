"""Página pública /t del enlace SUN (M1-08, parte no criptográfica)."""

from unittest.mock import patch

from django.http import QueryDict
from django.test import SimpleTestCase, override_settings

from . import enlace_sun

P_VALIDO = "EF963FF7828658A599F3041510671E88"  # forma de AN12196 §3.4.2 (solo el formato)
M_VALIDO = "94EED9EE65337086"


class LeerEnlaceTests(SimpleTestCase):
    def leer(self, consulta):
        return enlace_sun.leer_enlace(QueryDict(consulta))

    def test_enlace_valido(self):
        enlace = self.leer(f"p={P_VALIDO}&m={M_VALIDO}")
        self.assertEqual(enlace, enlace_sun.EnlaceSun(bytes.fromhex(P_VALIDO), bytes.fromhex(M_VALIDO)))

    def test_acepta_minusculas(self):
        self.assertEqual(self.leer(f"p={P_VALIDO.lower()}&m={M_VALIDO.lower()}").mac,
                         bytes.fromhex(M_VALIDO))

    def test_enlaces_mal_formados(self):
        malos = ["", f"m={M_VALIDO}", f"p={P_VALIDO}", f"p={P_VALIDO[:-1]}&m={M_VALIDO}",
                 f"p={P_VALIDO}0&m={M_VALIDO}", f"p={P_VALIDO}&m={M_VALIDO[:-1]}G",
                 f"p={P_VALIDO}&m={M_VALIDO}&m={M_VALIDO}", f"p= {P_VALIDO[1:]}&m={M_VALIDO}",
                 f"p={P_VALIDO}&m=%00{M_VALIDO[1:]}"]
        for consulta in malos:
            with self.subTest(consulta=consulta), self.assertRaises(enlace_sun.EnlaceInvalido):
                self.leer(consulta)

    def test_resultado_solo_admite_estados_conocidos(self):
        with self.assertRaises(ValueError):
            enlace_sun.ResultadoSun("perdida")


@override_settings(PORTAL_EN_RUTA_MI=True)
class PaginaVerificacionTests(SimpleTestCase):
    url = f"/mi/t?p={P_VALIDO}&m={M_VALIDO}"

    def assertCabecerasPrivadas(self, respuesta):
        self.assertEqual(respuesta["Referrer-Policy"], "no-referrer")
        self.assertIn("no-store", respuesta["Cache-Control"])
        self.assertEqual(respuesta["X-Robots-Tag"], "noindex, nofollow")

    def test_enlace_mal_formado_es_400(self):
        respuesta = self.client.get("/mi/t?p=123&m=456")
        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(respuesta, "Enlace no válido", status_code=400)
        self.assertCabecerasPrivadas(respuesta)

    def test_sin_parametros_es_400(self):
        self.assertEqual(self.client.get("/mi/t").status_code, 400)

    def test_mientras_no_hay_verificacion_responde_501(self):
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 501)
        self.assertContains(respuesta, "Verificación no disponible", status_code=501)
        self.assertNotContains(respuesta, "auténtica", status_code=501)
        self.assertCabecerasPrivadas(respuesta)

    def test_solo_get_y_head(self):
        self.assertEqual(self.client.post(self.url).status_code, 405)
        self.assertEqual(self.client.head(self.url).status_code, 501)

    def test_tarjeta_autentica_activa(self):
        with patch.object(enlace_sun, "verificar_enlace",
                          return_value=enlace_sun.ResultadoSun(enlace_sun.ACTIVA)) as verificar:
            respuesta = self.client.get(self.url)
        verificar.assert_called_once_with(enlace_sun.EnlaceSun(bytes.fromhex(P_VALIDO),
                                                                bytes.fromhex(M_VALIDO)))
        self.assertContains(respuesta, "Tarjeta SubePe auténtica")
        self.assertContains(respuesta, "Activa")
        self.assertContains(respuesta, 'href="/mi/ingresar/"')
        self.assertContains(respuesta, "Ver mi saldo")
        self.assertCabecerasPrivadas(respuesta)

    def test_tarjeta_autentica_bloqueada(self):
        with patch.object(enlace_sun, "verificar_enlace",
                          return_value=enlace_sun.ResultadoSun(enlace_sun.BLOQUEADA)):
            respuesta = self.client.get(self.url)
        self.assertContains(respuesta, "Tarjeta SubePe auténtica")
        self.assertContains(respuesta, "Bloqueada")

    def test_enlace_rechazado_no_dice_por_que(self):
        with patch.object(enlace_sun, "verificar_enlace",
                          side_effect=enlace_sun.EnlaceRechazado("contador repetido")):
            respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(respuesta, "Enlace no válido", status_code=400)
        self.assertNotContains(respuesta, "contador", status_code=400)

    def test_nunca_pasa_saldo_ni_nombre_a_la_plantilla(self):
        with patch.object(enlace_sun, "verificar_enlace",
                          return_value=enlace_sun.ResultadoSun(enlace_sun.ACTIVA)):
            respuesta = self.client.get(self.url)
        claves = set().union(*(set(c.flatten()) for c in respuesta.context))
        self.assertFalse(claves & {"saldo", "nombre", "cuenta", "tarjeta", "uid"}, claves)
