"""M9-05: contraseñas MQTT de los validadores y password_file de Mosquitto."""

import base64
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from transporte.models import Validador
from transporte.sync_v2 import mqtt

# Generado con "mosquitto_passwd -b f prueba secreto123" (eclipse-mosquitto 2.1.2)
DE_MOSQUITTO = ("$7$1000$AWrYrVLamsFflWObfwvhDM57cyTBENMz9iIbR57vtV20HaewFrEqI04JOPPTymxwWpBWsTQe"
                "+zqxEoI8m6pO3Q==$LOp06l/ZXDYmDcKV2uM6IeiCEFfQ/BosCpRAGT3DEVtwvriU804oHbCmXE9aDHaK"
                "htlR28RJ0aZHH714O3DjQA==")


def comando(*args):
    salida, errores = StringIO(), StringIO()
    call_command(*args, stdout=salida, stderr=errores)
    return salida.getvalue(), errores.getvalue()


class Hash(TestCase):
    def test_es_el_formato_de_mosquitto_passwd(self):
        self.assertTrue(mqtt.verificar("secreto123", DE_MOSQUITTO))
        self.assertFalse(mqtt.verificar("otra", DE_MOSQUITTO))
        _, _, _, sal, _ = DE_MOSQUITTO.split("$")
        self.assertEqual(mqtt.hash_mosquitto("secreto123", base64.b64decode(sal)), DE_MOSQUITTO)

    def test_sal_aleatoria(self):
        a, b = mqtt.hash_mosquitto("x"), mqtt.hash_mosquitto("x")
        self.assertNotEqual(a, b)
        self.assertTrue(mqtt.verificar("x", a) and mqtt.verificar("x", b))
        self.assertTrue(a.startswith("$7$1000$"))

    def test_hash_mal_formado_no_verifica(self):
        for malo in ("", "abc", "$6$1000$aa$bb", "$7$x$aa$bb"):
            self.assertFalse(mqtt.verificar("x", malo), malo)


class Credenciales(TestCase):
    def setUp(self):
        Usuario = get_user_model()
        self.v = {n: Validador.objects.create(numero=n, usuario=Usuario.objects.create(
            username=f"validador-{n}")) for n in (101, 102, 103)}

    def test_clave_validador_mqtt_muestra_la_clave_una_vez_y_guarda_el_hash(self):
        clave, aviso = comando("clave_validador", "101", "--mqtt")
        clave = clave.strip()
        self.assertGreaterEqual(len(clave), 32)
        self.assertIn("anótala", aviso)
        v = Validador.objects.get(numero=101)
        self.assertNotIn(clave, v.mqtt_clave)
        self.assertTrue(mqtt.verificar(clave, v.mqtt_clave))
        # Otra vez: clave nueva, la anterior deja de valer
        otra = comando("clave_validador", "101", "--mqtt")[0].strip()
        v.refresh_from_db()
        self.assertNotEqual(otra, clave)
        self.assertFalse(mqtt.verificar(clave, v.mqtt_clave))

    def test_mqtt_y_rotar_no_van_juntos(self):
        with self.assertRaises(CommandError):
            comando("clave_validador", "101", "--mqtt", "--rotar")
        self.assertEqual(Validador.objects.get(numero=101).mqtt_clave, "")

    @override_settings(MQTT_PUENTE_CLAVE="clave-del-puente")
    def test_archivo_con_puente_y_validadores_activos_con_clave(self):
        c101 = self.v[101].nueva_clave_mqtt()
        self.v[102].nueva_clave_mqtt()
        Validador.objects.filter(numero=102).update(activo=False)   # inactivo: fuera
        salida, aviso = comando("mqtt_credenciales")               # 103 sin clave: fuera
        self.assertEqual(aviso, "")
        lineas = dict(linea.split(":", 1) for linea in salida.splitlines())
        self.assertEqual(list(lineas), ["puente", "101"])
        self.assertTrue(mqtt.verificar("clave-del-puente", lineas["puente"]))
        self.assertTrue(mqtt.verificar(c101, lineas["101"]))
        self.assertNotIn(c101, salida)

    @override_settings(MQTT_PUENTE_CLAVE="")
    def test_sin_clave_del_puente_avisa(self):
        self.v[101].nueva_clave_mqtt()
        salida, aviso = comando("mqtt_credenciales")
        self.assertIn("MQTT_PUENTE_CLAVE", aviso)
        self.assertEqual([x.split(":")[0] for x in salida.splitlines()], ["101"])
