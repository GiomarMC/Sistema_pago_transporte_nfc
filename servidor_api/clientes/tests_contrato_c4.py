"""#109 (M12-06): las respuestas de las rutas de consulta cumplen el contrato C4.

El contrato es `docs/contratos/api-cliente-v1.yaml` (OpenAPI 3.1, cuyos esquemas son JSON Schema 2020-12). Cada
respuesta se valida contra el esquema que el contrato da para su ruta, método y código HTTP; si el contrato no
documenta ese código, el test falla. Necesita las dependencias de `requirements-dev.txt`.
"""
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

import yaml
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from transporte.models import Cuenta, Movimiento, Tarifa, Tarjeta

from .models import Cliente, Dispositivo, IntentoAcceso

CONTRATO = Path(settings.BASE_DIR).parent / "docs" / "contratos" / "api-cliente-v1.yaml"
URI = "urn:subepe:c4"


@lru_cache(maxsize=1)
def _contrato():
    with open(CONTRATO, encoding="utf-8") as archivo:
        documento = yaml.safe_load(archivo)
    registro = Registry().with_resource(URI, Resource.from_contents(documento, default_specification=DRAFT202012))
    return documento, registro


def _escapar(parte):
    return str(parte).replace("~", "~0").replace("/", "~1")


def _resolver(documento, puntero):
    nodo = documento
    for parte in puntero.lstrip("/").split("/"):
        nodo = nodo[parte.replace("~1", "/").replace("~0", "~")]
    return nodo


def esquema_de_respuesta(ruta, metodo, codigo):
    """Puntero JSON al esquema de la respuesta, siguiendo los $ref a components/responses."""
    documento, _ = _contrato()
    operacion = documento["paths"].get(ruta, {}).get(metodo)
    if operacion is None:
        raise AssertionError(f"C4 no define {metodo.upper()} {ruta}")
    if str(codigo) not in operacion["responses"]:
        raise AssertionError(f"C4 no documenta la respuesta {codigo} de {metodo.upper()} {ruta}")
    puntero = f"/paths/{_escapar(ruta)}/{metodo}/responses/{codigo}"
    respuesta = _resolver(documento, puntero)
    while "$ref" in respuesta:
        puntero = respuesta["$ref"].lstrip("#")
        respuesta = _resolver(documento, puntero)
    if "content" not in respuesta:
        return None  # sin cuerpo (p. ej. 204)
    return puntero + "/content/" + _escapar("application/json") + "/schema"


@override_settings(ALLOWED_HOSTS=["mi.localhost", "testserver"], DOMINIO_CLIENTE="mi.localhost")
class ContratoC4ConsultaTests(TestCase):
    host = {"HTTP_HOST": "mi.localhost"}

    def setUp(self):
        tarifa = Tarifa.objects.get_or_create(pk=1, defaults={"nombre": "general", "precio": 130})[0]
        self.cuenta = Cuenta.objects.create(nombre="Ana Quispe", documento="71234567", tarifa=tarifa, saldo=370)
        self.usuario = get_user_model().objects.create_user(username="cli-71234567", password="ClaveSegura2026")
        self.cliente_m12 = Cliente.objects.create(usuario=self.usuario, cuenta=self.cuenta,
                                                  correo="ana@example.test", correo_verificado=True)
        self.tarjeta = Tarjeta.objects.create(uid="C4-PROPIA", cuenta=self.cuenta, estado=Tarjeta.Estado.ACTIVA)
        Tarjeta.objects.create(uid="C4-BLOQUEADA", cuenta=self.cuenta, estado=Tarjeta.Estado.BLOQUEADA)
        ahora = timezone.now()
        Movimiento.objects.create(fecha=ahora - timedelta(minutes=5), cuenta=self.cuenta, tarjeta=self.tarjeta,
                                  tipo="recarga", monto=500, saldo_final=500, ref="C4-R1")
        Movimiento.objects.create(fecha=ahora, cuenta=self.cuenta, tarjeta=self.tarjeta, tipo="viaje",
                                  monto=-130, saldo_final=370, validador=103, ref="V103-1")
        IntentoAcceso.objects.create(documento="71234567", ip="203.0.113.7", exito=True, motivo="otp",
                                     dispositivo="Firefox en Linux")
        IntentoAcceso.objects.create(documento="71234567", ip=None, exito=False, motivo="clave",
                                     dispositivo="Navegador")
        self.dispositivo = Dispositivo.objects.create(cliente=self.cliente_m12, huella="a" * 64,
                                                      nombre="Firefox en Linux",
                                                      confiable_hasta=ahora + timedelta(days=90))
        self.client.force_login(self.usuario)

    def validar(self, respuesta, ruta, metodo="get"):
        puntero = esquema_de_respuesta(ruta, metodo, respuesta.status_code)
        if puntero is None:
            self.assertEqual(respuesta.content, b"", f"{metodo.upper()} {ruta} no debería tener cuerpo")
            return
        _, registro = _contrato()
        validador = Draft202012Validator({"$ref": f"{URI}#{puntero}"}, registry=registro,
                                         format_checker=FormatChecker())
        errores = [f"{'/'.join(map(str, e.absolute_path)) or '(raíz)'}: {e.message}"
                   for e in validador.iter_errors(respuesta.json())]
        self.assertEqual(errores, [], f"{metodo.upper()} {ruta} → {respuesta.status_code} no cumple C4")

    def get(self, ruta, cliente=None):
        return (cliente or self.client).get("/api/v1" + ruta, **self.host)

    def test_el_validador_detecta_una_respuesta_que_no_cumple(self):
        # Control: si el validador aceptara cualquier cosa, los demás tests no probarían nada
        _, registro = _contrato()
        validador = Draft202012Validator({"$ref": URI + "#" + esquema_de_respuesta("/resumen/", "get", 200)},
                                         registry=registro, format_checker=FormatChecker())
        self.assertFalse(validador.is_valid({"cliente": {"nombre": "Ana"}, "cuenta": {}, "tarjetas": []}))
        self.assertFalse(validador.is_valid({
            "cliente": {"nombre": "Ana", "documento_enmascarado": "71234567", "correo": "ana@example.test"},
            "cuenta": {"id": 1, "saldo": 0, "por_cargar_en_tarjeta": 0,
                       "tarifa": {"codigo": 1, "nombre": "General", "precio": 130}},
            "tarjetas": []}))  # documento sin enmascarar

    def test_resumen(self):
        respuesta = self.get("/resumen/")
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(len(respuesta.json()["tarjetas"]), 2)
        self.validar(respuesta, "/resumen/")

    def test_movimientos_con_y_sin_filtros(self):
        for consulta in ("", "?tipo=viaje", "?tipo=recarga&pagina=1",
                         f"?desde={timezone.localdate():%Y-%m-%d}&hasta={timezone.localdate():%Y-%m-%d}",
                         "?desde=2000-01-01&hasta=2000-01-02"):
            with self.subTest(consulta=consulta):
                respuesta = self.get("/movimientos/" + consulta)
                self.assertEqual(respuesta.status_code, 200)
                self.validar(respuesta, "/movimientos/")

    def test_movimientos_con_filtro_invalido(self):
        for consulta in ("?tipo=ajeno", "?pagina=0", "?desde=2026-99-01"):
            with self.subTest(consulta=consulta):
                respuesta = self.get("/movimientos/" + consulta)
                self.assertEqual(respuesta.status_code, 400)
                self.validar(respuesta, "/movimientos/")

    def test_perfil(self):
        respuesta = self.get("/perfil/")
        self.assertEqual(respuesta.status_code, 200)
        self.validar(respuesta, "/perfil/")

    def test_seguridad_accesos_y_dispositivos(self):
        for ruta in ("/seguridad/accesos/", "/seguridad/dispositivos/"):
            with self.subTest(ruta=ruta):
                respuesta = self.get(ruta)
                self.assertEqual(respuesta.status_code, 200)
                self.assertTrue(respuesta.json())
                self.validar(respuesta, ruta)

    def test_olvidar_dispositivo_propio_y_ajeno(self):
        otra = Cuenta.objects.create(nombre="Luis Mamani", documento="87654321", tarifa=self.cuenta.tarifa)
        otro_usuario = get_user_model().objects.create_user(username="cli-87654321", password="x")
        otro_cliente = Cliente.objects.create(usuario=otro_usuario, cuenta=otra, correo="luis@example.test",
                                              correo_verificado=True)
        ajeno = Dispositivo.objects.create(cliente=otro_cliente, huella="b" * 64, nombre="Chrome",
                                           confiable_hasta=timezone.now() + timedelta(days=90))
        ruta = "/seguridad/dispositivos/{id}/"
        respuesta = self.client.delete(f"/api/v1/seguridad/dispositivos/{ajeno.pk}/", **self.host)
        self.assertEqual(respuesta.status_code, 404)
        self.validar(respuesta, ruta, "delete")
        self.assertTrue(Dispositivo.objects.filter(pk=ajeno.pk).exists())
        respuesta = self.client.delete(f"/api/v1/seguridad/dispositivos/{self.dispositivo.pk}/", **self.host)
        self.assertEqual(respuesta.status_code, 204)
        self.validar(respuesta, ruta, "delete")

    def test_sin_sesion(self):
        anonimo = Client()
        for ruta in ("/resumen/", "/movimientos/", "/perfil/", "/seguridad/accesos/", "/seguridad/dispositivos/"):
            with self.subTest(ruta=ruta):
                respuesta = self.get(ruta, cliente=anonimo)
                self.assertEqual(respuesta.status_code, 401)
                self.validar(respuesta, ruta)
