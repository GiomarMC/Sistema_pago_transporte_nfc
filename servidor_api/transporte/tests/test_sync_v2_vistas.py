"""M9-04: POST /api/v2/paquetes/."""

from itertools import count

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from transporte.models import Alerta, EventoRecibido, Validador
from transporte.sync_v2 import paquetes as p

CLAVE = ec.generate_private_key(ec.SECP256R1())
PEM = CLAVE.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
URL = "/api/v2/paquetes/"
BINARIO = "application/octet-stream"
_secuencias = count(1)


def cliente(usuario=None):
    c = APIClient()
    if usuario is not None:
        c.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=usuario).key}")
    return c


@override_settings(LISTAS_CLAVE_PRIVADA=PEM)
class Paquetes(TestCase):
    def setUp(self):
        cache.clear()   # el límite de frecuencia vive en la caché
        Usuario = get_user_model()
        self.v101 = Validador.objects.create(
            numero=101, usuario=Usuario.objects.create(username="validador-101"))
        self.v102 = Validador.objects.create(
            numero=102, usuario=Usuario.objects.create(username="validador-102"))
        self.bus = cliente(self.v101.usuario)
        self.bus102 = cliente(self.v102.usuario)

    def hola(self, origen=101):
        v = {101: self.v101, 102: self.v102}[origen]
        cab = p.Cabecera(p.HOLA, origen=origen, destino=p.SERVIDOR,
                         secuencia=next(_secuencias), creado=1793000000)
        return p.empaquetar(cab, p.Hola(0, 0, 0, 0, 0, p.WIFI, -30, 0, 0, 20261114),
                            clave_hmac=v.clave_hmac_bytes)

    def eventos(self, *ids, origen=101):
        v = {101: self.v101, 102: self.v102}[origen]
        cab = p.Cabecera(p.EVENTOS, origen=origen, destino=p.SERVIDOR,
                         secuencia=next(_secuencias), creado=1793000000)
        evs = [p.Evento(i, p.INCIDENCIA, 0, 1793000000 + i, 0, b"", detalle="prueba")
               for i in ids]
        return p.empaquetar(cab, p.Eventos(evs), clave_hmac=v.clave_hmac_bytes)

    def post(self, c, cuerpo, tipo=BINARIO):
        return c.generic("POST", URL, cuerpo, content_type=tipo)

    def leer(self, r):
        return [p.desempaquetar(x, lambda n: None, CLAVE.public_key())
                for x in p.separar(r.content)]

    # --- Acceso ---

    def test_anonimo_recibe_401(self):
        self.assertEqual(self.post(cliente(), self.hola()).status_code, 401)

    def test_usuario_sin_grupo_recibe_403(self):
        otro = get_user_model().objects.create(username="cualquiera")
        self.assertEqual(self.post(cliente(otro), self.hola()).status_code, 403)

    def test_validador_inactivo_recibe_403(self):
        Validador.objects.filter(pk=self.v101.pk).update(activo=False)
        self.assertEqual(self.post(self.bus, self.hola()).status_code, 403)

    def test_solo_post(self):
        self.assertEqual(self.bus.get(URL).status_code, 405)

    # --- Cuerpo ---

    def test_cuerpo_de_mas_de_64_kb_recibe_400(self):
        r = self.post(self.bus, b"\0" * (64 * 1024 + 1))
        self.assertEqual(r.status_code, 400)
        self.assertIn("64 KB", r.json()["error"])

    def test_cuerpo_de_64_kb_justos_se_lee(self):
        r = self.post(self.bus, b"\0" * (64 * 1024))
        self.assertEqual(r.status_code, 400)
        self.assertIn("mal formado", r.json()["error"], "pasa el tamaño; falla al separar")

    def test_otro_tipo_de_contenido_recibe_415(self):
        self.assertEqual(self.post(self.bus, self.hola(), tipo="application/json").status_code,
                         415)

    def test_cuerpo_vacio_recibe_400(self):
        self.assertEqual(self.post(self.bus, b"").status_code, 400)

    def test_flujo_mal_formado_recibe_400_sin_procesar_nada(self):
        r = self.post(self.bus, self.eventos(1) + self.hola()[:-3])
        self.assertEqual(r.status_code, 400)
        self.assertFalse(EventoRecibido.objects.exists())
        self.assertTrue(Alerta.objects.filter(tipo="paquete inválido").exists())

    # --- Procesamiento ---

    def test_validador_sube_varios_paquetes_y_recibe_las_respuestas(self):
        r = self.post(self.bus, self.eventos(1, 2) + self.hola())
        self.assertEqual((r.status_code, r["Content-Type"]), (200, BINARIO))
        tipos = [(x.cabecera.tipo, x.cabecera.destino) for x in self.leer(r)]
        self.assertEqual(tipos[:2], [(p.ACUSE, 101), (p.ACUSE, 101)])
        self.assertIn((p.TARIFAS, 101), tipos)
        self.assertEqual(set(EventoRecibido.objects.values_list("via", flat=True)), {"http"})

    def test_validador_sube_un_paquete_ajeno(self):
        r = self.post(self.bus, self.eventos(1, origen=102))
        (acuse,) = self.leer(r)
        self.assertEqual((acuse.cabecera.destino, acuse.cuerpo.hasta_evento), (102, 1))
        self.assertEqual(EventoRecibido.objects.get().validador, self.v102)

    def test_paquete_invalido_dentro_del_flujo_no_corta_los_demas(self):
        malo = bytearray(self.eventos(9))
        malo[-1] ^= 1
        r = self.post(self.bus, bytes(malo) + self.eventos(1))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([x.cuerpo.hasta_evento for x in self.leer(r)], [1])
        self.assertTrue(Alerta.objects.filter(tipo="paquete inválido").exists())

    # --- Frecuencia ---

    def test_maximo_60_peticiones_por_minuto_por_usuario(self):
        hola = self.hola()
        estados = [self.post(self.bus, hola).status_code for _ in range(61)]
        self.assertEqual(estados[:60], [200] * 60)
        self.assertEqual(estados[60], 429)
        self.assertEqual(self.post(self.bus102, hola).status_code, 200, "es por usuario")
