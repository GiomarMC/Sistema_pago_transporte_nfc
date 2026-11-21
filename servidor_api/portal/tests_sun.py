"""Página pública /t del enlace SUN (M1-08, #42)."""

from unittest.mock import patch

from django.http import QueryDict
from django.test import SimpleTestCase, TestCase, override_settings

from transporte.models import Cuenta, Tarifa, Tarjeta
from transporte.tests import tarjeta_sun

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


@override_settings(PORTAL_EN_RUTA_MI=True, SUN_K3="", SUN_K4_MAESTRAS="")
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

    def test_sin_claves_configuradas_responde_501(self):
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 501)
        self.assertContains(respuesta, "Verificación no disponible", status_code=501)
        self.assertNotContains(respuesta, "auténtica", status_code=501)
        self.assertCabecerasPrivadas(respuesta)

    def test_solo_get(self):
        # HEAD tampoco: verificar gasta el contador del enlace
        for metodo in (self.client.post, self.client.head, self.client.put):
            with self.subTest(metodo=metodo.__name__):
                self.assertEqual(metodo(self.url).status_code, 405)

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


K3 = bytes.fromhex("33" * 16)
MAESTRA_K4 = bytes.fromhex("44" * 16)
UID = bytes.fromhex("04B3E121CA2A81")


@override_settings(PORTAL_EN_RUTA_MI=True, SUN_K3=K3.hex(), SUN_K4_MAESTRAS="1:" + MAESTRA_K4.hex())
class VerificacionSunTests(TestCase):
    """De extremo a extremo: enlaces que generaría la tarjeta (tarjeta_sun, validado
    con AN12196) contra la página, la base de datos y el contador."""

    def setUp(self):
        tarifa = Tarifa.objects.get_or_create(codigo=1, defaults={"nombre": "General", "precio": 100})[0]
        self.cuenta = Cuenta.objects.create(nombre="Ana Pérez", documento="45678912", tarifa=tarifa, saldo=1234)
        self.tarjeta = Tarjeta.objects.create(uid=UID.hex().upper(), cuenta=self.cuenta,
                                              estado=Tarjeta.Estado.ACTIVA)

    def url(self, contador, uid=UID, maestra=MAESTRA_K4, version=1, k3=K3):
        p, m = tarjeta_sun.enlace(k3, tarjeta_sun.k4_de_tarjeta(maestra, uid, version), uid, contador)
        return f"/mi/t?p={p}&m={m}"

    def assertAutentica(self, respuesta, estado="Activa"):
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Tarjeta SubePe auténtica")
        self.assertContains(respuesta, estado)
        self.assertNotContains(respuesta, "Ana")
        self.assertNotContains(respuesta, "12.34")

    def assertRechazada(self, respuesta):
        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(respuesta, "Enlace no válido", status_code=400)
        self.assertNotContains(respuesta, "auténtica", status_code=400)

    def test_url_valida(self):
        self.assertAutentica(self.client.get(self.url(5)))
        self.tarjeta.refresh_from_db()
        self.assertEqual(self.tarjeta.contador_sdm, 5)

    def test_tarjeta_bloqueada(self):
        Tarjeta.objects.filter(pk=self.tarjeta.pk).update(estado=Tarjeta.Estado.BLOQUEADA)
        self.assertAutentica(self.client.get(self.url(1)), estado="Bloqueada")

    def test_cmac_alterado(self):
        url = self.url(5)
        ultimo = url[-1]
        alterada = url[:-1] + ("0" if ultimo != "0" else "1")
        with self.assertLogs("portal.enlace_sun", "WARNING"):
            self.assertRechazada(self.client.get(alterada))
        self.tarjeta.refresh_from_db()
        self.assertEqual(self.tarjeta.contador_sdm, 0)

    def test_contador_repetido(self):
        url = self.url(7)
        self.assertAutentica(self.client.get(url))
        with self.assertLogs("portal.enlace_sun", "WARNING") as registro:
            self.assertRechazada(self.client.get(url))
        self.assertIn("contador repetido", registro.output[0])

    def test_contador_menor_que_el_ultimo_aceptado(self):
        self.assertAutentica(self.client.get(self.url(9)))
        with self.assertLogs("portal.enlace_sun", "WARNING"):
            self.assertRechazada(self.client.get(self.url(8)))
        self.assertAutentica(self.client.get(self.url(10)))

    def test_tarjeta_inexistente(self):
        with self.assertLogs("portal.enlace_sun", "WARNING") as registro:
            self.assertRechazada(self.client.get(self.url(1, uid=bytes.fromhex("04AABBCCDDEEFF"))))
        self.assertIn("tarjeta inexistente", registro.output[0])

    def test_tarjeta_anulada_o_pendiente_no_es_valida(self):
        for estado in (Tarjeta.Estado.ANULADA, Tarjeta.Estado.PENDIENTE):
            with self.subTest(estado=estado):
                Tarjeta.objects.filter(pk=self.tarjeta.pk).update(estado=estado)
                with self.assertLogs("portal.enlace_sun", "WARNING"):
                    self.assertRechazada(self.client.get(self.url(1)))

    def test_reemision_conserva_el_contador_del_uid(self):
        # Un enlace capturado antes de restablecer la tarjeta no vale en la nueva emisión
        self.assertAutentica(self.client.get(self.url(20)))
        Tarjeta.objects.filter(pk=self.tarjeta.pk).update(estado=Tarjeta.Estado.ANULADA)
        nueva = Tarjeta.objects.create(uid=UID.hex().upper(), cuenta=self.cuenta, estado=Tarjeta.Estado.ACTIVA)
        with self.assertLogs("portal.enlace_sun", "WARNING"):
            self.assertRechazada(self.client.get(self.url(15)))
        self.assertAutentica(self.client.get(self.url(21)))
        nueva.refresh_from_db()
        self.assertEqual(nueva.contador_sdm, 21)

    def test_otra_k4_maestra_es_rechazada(self):
        with self.assertLogs("portal.enlace_sun", "WARNING"):
            self.assertRechazada(self.client.get(self.url(3, maestra=bytes.fromhex("66" * 16))))

    @override_settings(SUN_K4_MAESTRAS="2:" + "77" * 16 + ",1:" + "44" * 16)
    def test_rotacion_de_k4(self):
        self.assertAutentica(self.client.get(self.url(1, version=1)))
        self.assertAutentica(self.client.get(self.url(2, maestra=bytes.fromhex("77" * 16), version=2)))

    def test_cabeceras_privadas(self):
        respuesta = self.client.get(self.url(4))
        self.assertEqual(respuesta["Referrer-Policy"], "no-referrer")
        self.assertIn("no-store", respuesta["Cache-Control"])
        self.assertEqual(respuesta["X-Robots-Tag"], "noindex, nofollow")
