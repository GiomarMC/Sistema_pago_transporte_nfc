import re
import secrets
import tempfile
from io import StringIO
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.views.debug import ExceptionReporter

from transporte.models import Validador
from transporte.sync_v2 import claves
from transporte.sync_v2 import paquetes as p

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def hola(origen):
    return (p.Cabecera(p.HOLA, origen=origen, destino=p.SERVIDOR, secuencia=1,
                       creado=1793000000),
            p.Hola(version_listas=1, version_tarifas=1, ultimo_evento=0, pendientes=0,
                   ajenos=0, enlace=p.WIFI, senal=-60, bytes_tx_hoy=0, bytes_rx_hoy=0,
                   version_firmware=20261106))


def comando(*args, **opciones):
    salida, errores = StringIO(), StringIO()
    call_command(*args, stdout=salida, stderr=errores, **opciones)
    return salida.getvalue().strip(), errores.getvalue()


class ClaveHmacDelValidador(TestCase):
    def setUp(self):
        Usuario = get_user_model()
        self.v101 = Validador.objects.create(numero=101, usuario=Usuario.objects.create(
            username="validador-101"))
        self.v102 = Validador.objects.create(numero=102, usuario=Usuario.objects.create(
            username="validador-102"))

    def test_cada_validador_nace_con_su_clave(self):
        for v in (self.v101, self.v102):
            v.refresh_from_db()
            self.assertRegex(v.clave_hmac, HEX64)
            self.assertEqual(len(v.clave_hmac_bytes), 32)
        self.assertNotEqual(self.v101.clave_hmac, self.v102.clave_hmac)

    def test_paquete_del_101_no_se_verifica_con_la_clave_del_102(self):
        datos = p.empaquetar(*hola(101), clave_hmac=self.v101.clave_hmac_bytes)
        with self.assertRaises(p.PaqueteInvalido) as error:
            p.desempaquetar(datos, lambda n: self.v102.clave_hmac_bytes, None)
        self.assertEqual(error.exception.motivo, "firma")
        # Con la clave buscada en la base de datos, la suya, sí se acepta
        self.assertEqual(p.desempaquetar(datos, claves.clave_hmac_de, None).cabecera.origen, 101)

    def test_el_102_no_puede_hacerse_pasar_por_el_101(self):
        datos = p.empaquetar(*hola(101), clave_hmac=self.v102.clave_hmac_bytes)
        with self.assertRaises(p.PaqueteInvalido) as error:
            p.desempaquetar(datos, claves.clave_hmac_de, None)
        self.assertEqual(error.exception.motivo, "firma")

    def test_validador_inactivo_o_inexistente_no_tiene_clave(self):
        self.assertIsNone(claves.clave_hmac_de(999))
        self.v102.activo = False
        self.v102.save()
        self.assertIsNone(claves.clave_hmac_de(102))
        datos = p.empaquetar(*hola(102), clave_hmac=self.v102.clave_hmac_bytes)
        with self.assertRaises(p.PaqueteInvalido) as error:
            p.desempaquetar(datos, claves.clave_hmac_de, None)
        self.assertEqual(error.exception.motivo, "validador")

    def test_comando_muestra_y_rota_la_clave(self):
        salida, _ = comando("clave_validador", "101")
        self.assertEqual(salida, self.v101.clave_hmac)
        antes = p.empaquetar(*hola(101), clave_hmac=self.v101.clave_hmac_bytes)

        nueva, aviso = comando("clave_validador", "101", "--rotar")
        self.assertRegex(nueva, HEX64)
        self.assertNotEqual(nueva, self.v101.clave_hmac)
        self.assertIn("rotada", aviso)
        self.v101.refresh_from_db()
        self.assertEqual(self.v101.clave_hmac, nueva)
        with self.assertRaises(p.PaqueteInvalido):  # lo firmado con la anterior ya no vale
            p.desempaquetar(antes, claves.clave_hmac_de, None)
        self.v102.refresh_from_db()
        self.assertNotEqual(self.v102.clave_hmac, nueva)  # solo cambia la del 101

    def test_comando_con_validador_inexistente(self):
        with self.assertRaisesMessage(CommandError, "no existe el validador 555"):
            comando("clave_validador", "555")

    def test_crear_validador_le_da_clave(self):
        comando("crear_validador", "103")
        self.assertRegex(Validador.objects.get(numero=103).clave_hmac, HEX64)
        clave = Validador.objects.get(numero=101).clave_hmac
        comando("crear_validador", "101", descripcion="Bus 1")  # actualizar no la cambia
        self.assertEqual(Validador.objects.get(numero=101).clave_hmac, clave)


class GenerarClaveListas(SimpleTestCase):
    def setUp(self):
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.cabecera = Path(carpeta.name) / "clave_servidor.h"

    def generar(self, *extra):
        return comando("generar_clave_listas", "--cabecera", str(self.cabecera), *extra)

    def punto_de_la_cabecera(self):
        texto = self.cabecera.read_text()
        bloque = texto[texto.index("CLAVE_SERVIDOR[65]"):]
        return bytes(int(h, 16) for h in re.findall(r"0x([0-9A-F]{2})", bloque))

    @override_settings(LISTAS_CLAVE_PRIVADA="")
    def test_genera_la_privada_para_el_env_y_la_publica_en_la_cabecera(self):
        salida, aviso = self.generar()
        self.assertTrue(salida.startswith("LISTAS_CLAVE_PRIVADA=-----BEGIN PRIVATE KEY-----\\n"))
        self.assertNotIn("\n", salida)  # una sola línea, válida para el .env
        self.assertIn("No la subas", aviso)

        with self.settings(LISTAS_CLAVE_PRIVADA=salida.split("=", 1)[1]):
            privada = claves.clave_privada_listas()
        self.assertEqual(privada.curve.name, "secp256r1")
        punto = self.punto_de_la_cabecera()
        self.assertEqual(len(punto), 65)
        self.assertEqual(punto, claves.punto_publico(privada.public_key()))
        texto = self.cabecera.read_text()
        self.assertIn("#define CLAVE_SERVIDOR_CONFIGURADA 1", texto)
        self.assertIn(claves.huella(privada.public_key()), texto)

        # La clave sirve para firmar paquetes del servidor que se verifican con la pública
        acuse = p.empaquetar(p.Cabecera(p.ACUSE, origen=p.SERVIDOR, destino=101, secuencia=1,
                                        creado=1793000000), p.Acuse(3, 1793000000),
                             clave_ecdsa=privada)
        publica = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), punto)
        self.assertEqual(p.desempaquetar(acuse, lambda n: None, publica).cuerpo.hasta_evento, 3)

    @override_settings(LISTAS_CLAVE_PRIVADA="")
    def test_no_sobrescribe_una_cabecera_configurada_sin_forzar(self):
        self.generar()
        anterior = self.cabecera.read_text()
        with self.assertRaisesMessage(CommandError, "--forzar"):
            self.generar()
        self.assertEqual(self.cabecera.read_text(), anterior)
        self.generar("--forzar")
        self.assertNotEqual(self.cabecera.read_text(), anterior)

    def test_no_reemplaza_la_clave_del_env_sin_forzar(self):
        with self.settings(LISTAS_CLAVE_PRIVADA="-----BEGIN PRIVATE KEY-----\\nX"):
            with self.assertRaisesMessage(CommandError, "LISTAS_CLAVE_PRIVADA ya está definida"):
                self.generar()
        self.assertFalse(self.cabecera.exists())

    @override_settings(LISTAS_CLAVE_PRIVADA="")
    def test_la_cabecera_provisional_no_cuenta_como_clave(self):
        self.cabecera.write_text("#define CLAVE_SERVIDOR_CONFIGURADA 0\n")
        self.generar()
        self.assertIn("CLAVE_SERVIDOR_CONFIGURADA 1", self.cabecera.read_text())

    def test_solo_cabecera_la_rehace_desde_el_env(self):
        privada = ec.generate_private_key(ec.SECP256R1())
        pem = privada.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
        with self.settings(LISTAS_CLAVE_PRIVADA=claves.pem_en_una_linea(pem)):
            salida, _ = self.generar("--solo-cabecera")
        self.assertEqual(salida, "")  # no imprime ninguna clave privada
        self.assertEqual(self.punto_de_la_cabecera(), claves.punto_publico(privada.public_key()))

    @override_settings(LISTAS_CLAVE_PRIVADA="")
    def test_solo_cabecera_sin_clave_en_el_env(self):
        with self.assertRaisesMessage(CommandError, "Falta LISTAS_CLAVE_PRIVADA"):
            self.generar("--solo-cabecera")

    def test_carpeta_inexistente(self):
        with self.assertRaisesMessage(CommandError, "--cabecera"):
            comando("generar_clave_listas", "--cabecera", "/no/existe/clave_servidor.h")


class CargarClavePrivada(SimpleTestCase):
    def test_vacia_mal_formada_o_de_otra_curva(self):
        otra = ec.generate_private_key(ec.SECP384R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        for valor, mensaje in (("", "Falta"), ("basura", "no es una clave PEM"),
                               (claves.pem_en_una_linea(otra), "P-256")):
            with self.subTest(mensaje), self.settings(LISTAS_CLAVE_PRIVADA=valor), \
                    self.assertRaisesMessage(ImproperlyConfigured, mensaje):
                claves.clave_privada_listas()

    def test_acepta_el_pem_con_saltos_reales(self):
        privada = ec.generate_private_key(ec.SECP256R1())
        pem = privada.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
        with self.settings(LISTAS_CLAVE_PRIVADA=pem):
            self.assertEqual(claves.punto_publico(claves.clave_publica_listas()),
                             claves.punto_publico(privada.public_key()))


class ErroresOcultanLaClave(SimpleTestCase):
    def test_la_pagina_de_error_no_muestra_la_clave_privada(self):
        # Aleatorio y fuera de las variables locales: la página también muestra el código
        # fuente y las variables de cada función del traceback
        self.secreto = secrets.token_hex(16)
        with self.settings(LISTAS_CLAVE_PRIVADA=self.secreto):
            try:
                raise RuntimeError("fallo")
            except RuntimeError as e:
                html = ExceptionReporter(RequestFactory().get("/"), type(e), e,
                                         e.__traceback__).get_traceback_html()
        self.assertIn("LISTAS_CLAVE_PRIVADA", html)
        self.assertNotIn(self.secreto, html)
