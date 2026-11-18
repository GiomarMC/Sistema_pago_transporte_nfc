"""Contratos reales del portal que ya tienen servicio en el servidor."""
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from clientes.models import Cliente
from libro.libro import saldo_cliente
from pagos.models import CobroSimulado, OrdenRecarga
from pagos.servicios import confirmar_cobro
from transporte.models import Cuenta, Movimiento, Tarifa, Tarjeta


class PortalRealTests(TestCase):
    def setUp(self):
        tarifa = Tarifa.objects.get_or_create(pk=1, defaults={
            "nombre": "general", "precio": 130})[0]
        self.cuenta = Cuenta.objects.create(
            nombre="Ana Quispe", documento="71234567", tarifa=tarifa)
        self.usuario = get_user_model().objects.create_user(
            username="cli-71234567", password="ClaveSegura2026")
        Cliente.objects.create(usuario=self.usuario, cuenta=self.cuenta,
                               correo="ana@example.test", correo_verificado=True)
        self.tarjeta = Tarjeta.objects.create(
            uid="M20-PROPIA", cuenta=self.cuenta, estado=Tarjeta.Estado.ACTIVA)
        self.client.force_login(self.usuario)

    def test_resumen_y_movimientos_solo_de_la_cuenta_propia(self):
        ajena = Cuenta.objects.create(nombre="Otra", documento="87654321",
                                      tarifa=self.cuenta.tarifa)
        Tarjeta.objects.create(uid="M20-AJENA", cuenta=ajena,
                               estado=Tarjeta.Estado.ACTIVA)
        Movimiento.objects.create(fecha=timezone.now(), cuenta=ajena, tipo="recarga",
                                  monto=9999, saldo_final=9999, ref="M20-AJENO")
        ahora = timezone.now()
        Movimiento.objects.bulk_create([
            Movimiento(fecha=ahora - timedelta(minutes=i), cuenta=self.cuenta,
                       tarjeta=self.tarjeta, tipo="viaje", monto=-130,
                       saldo_final=0, validador=103, ref=f"M20-V{i}")
            for i in range(22)
        ])
        resumen = self.client.get("/api/v1/resumen/")
        self.assertEqual(resumen.status_code, 200)
        self.assertEqual(resumen.json()["cliente"], {
            "nombre": "Ana Quispe", "documento_enmascarado": "****4567",
            "correo": "ana@example.test"})
        self.assertEqual([t["id"] for t in resumen.json()["tarjetas"]], [self.tarjeta.pk])
        primera = self.client.get("/api/v1/movimientos/?pagina=1").json()
        segunda = self.client.get("/api/v1/movimientos/?pagina=2").json()
        self.assertEqual((len(primera["resultados"]), len(segunda["resultados"]),
                          primera["paginas"]), (20, 2, 2))
        self.assertEqual(primera["resultados"][0]["descripcion"], "Viaje · bus V103")
        self.assertFalse(any(m["monto"] == 9999 for m in primera["resultados"] + segunda["resultados"]))
        self.assertEqual(self.client.get("/api/v1/movimientos/?tipo=recarga").json()[
            "resultados"], [])
        self.assertEqual(self.client.get("/api/v1/movimientos/?pagina=3").status_code, 400)
        self.assertEqual(self.client.get("/api/v1/movimientos/?desde=2026-99-01").status_code, 400)
        self.assertEqual(self.client.get("/api/v1/movimientos/?tipo=ajeno").status_code, 400)

    def test_sin_sesion_no_expone_resumen_ni_movimientos(self):
        self.client.logout()
        for ruta in ("/api/v1/resumen/", "/api/v1/movimientos/",
                     "/api/v1/perfil/", "/api/v1/sesion/"):
            respuesta = self.client.get(ruta)
            self.assertEqual(respuesta.status_code, 401)
            self.assertEqual(respuesta.json()["error"]["codigo"], "no_autenticado")

    def test_sesion_perfil_y_salida(self):
        self.assertEqual(self.client.get("/api/v1/sesion/").json(), {"activa": True})
        perfil = self.client.get("/api/v1/perfil/").json()
        self.assertEqual({k: perfil[k] for k in ("nombre", "documento_enmascarado", "correo")}, {
            "nombre": "Ana Quispe", "documento_enmascarado": "****4567",
            "correo": "ana@example.test"})
        self.assertIn("creado", perfil)
        self.assertEqual(self.client.delete("/api/v1/sesion/").status_code, 204)
        self.assertEqual(self.client.get("/api/v1/sesion/").status_code, 401)

    def test_pago_simulado_regresa_al_portal_sin_acreditar_por_redireccion(self):
        respuesta = self.client.post(
            "/api/v1/recargas/ordenes/", {"monto": 1000},
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        datos = respuesta.json()
        self.assertTrue(datos["url_pago"].startswith("/pagos/simulador/"))
        orden = OrdenRecarga.objects.get(pk=datos["id"])
        cobro = CobroSimulado.objects.get(orden=orden)
        self.assertEqual(cobro.url_retorno, "http://testserver/mi/recargar/resultado/")
        redireccion = self.client.post(
            datos["url_pago"], {"decision": "aprobar", "webhook": "ninguno"})
        self.assertEqual(redireccion.status_code, 302)
        self.assertEqual(redireccion.url,
                         f"http://testserver/mi/recargar/resultado/?orden={orden.pk}")
        orden.refresh_from_db()
        self.cuenta.refresh_from_db()
        self.assertEqual((orden.estado, self.cuenta.saldo), ("pendiente", 0))
        self.assertTrue(confirmar_cobro(orden))
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.saldo, 1000)
        self.assertEqual(saldo_cliente(self.cuenta), 1000)
        detalle = self.client.get(f"/api/v1/recargas/ordenes/{orden.pk}/").json()
        self.assertEqual(detalle["estado"], "acreditada")
        self.assertEqual(self.client.get("/api/v1/movimientos/?tipo=recarga").json()[
            "resultados"][0]["monto"], 1000)

    @override_settings(ALLOWED_HOSTS=["mi.localhost", "testserver"],
                       DOMINIO_CLIENTE="mi.localhost")
    def test_pago_del_host_dedicado_regresa_a_ruta_raiz(self):
        respuesta = self.client.post(
            "/api/v1/recargas/ordenes/", {"monto": 1000},
            content_type="application/json", HTTP_HOST="mi.localhost",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        orden = OrdenRecarga.objects.get(pk=respuesta.json()["id"])
        cobro = CobroSimulado.objects.get(orden=orden)
        self.assertEqual(cobro.url_retorno, "http://mi.localhost/recargar/resultado/")
        self.assertEqual(self.client.get(respuesta.json()["url_pago"],
                                         HTTP_HOST="mi.localhost").status_code, 200)

    def test_bloqueo_y_solicitud_se_reflejan_en_consultas_reales(self):
        respuesta = self.client.post(
            f"/api/v1/tarjetas/{self.tarjeta.pk}/bloquear/",
            {"motivo": "robo", "contrasena": "ClaveSegura2026"},
            content_type="application/json")
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertEqual(respuesta.json()["estado"], "bloqueada")
        self.assertIn("bloqueada_en", respuesta.json())
        self.assertEqual(self.client.get("/api/v1/resumen/").json()["tarjetas"][0][
            "estado"], "bloqueada")
        solicitud = self.client.post(
            "/api/v1/reposiciones/", {"tarjeta_id": self.tarjeta.pk},
            content_type="application/json")
        self.assertEqual(solicitud.status_code, 201, solicitud.content)
        self.assertEqual(self.client.get("/api/v1/reposiciones/").json()[0]["estado"],
                         "solicitada")


class UrlResultadoTests(TestCase):
    """El pago vuelve al portal: bajo /mi/ en desarrollo, en la raíz del host mi.<dominio>."""

    def test_url_de_retorno_segun_el_host(self):
        from django.test import RequestFactory
        from pagos.api_cliente import url_resultado
        fabrica = RequestFactory()
        with self.settings(ALLOWED_HOSTS=["subepe.app", "mi.subepe.app"]):
            principal = fabrica.get("/", HTTP_HOST="subepe.app")
            portal = fabrica.get("/", HTTP_HOST="mi.subepe.app")
            portal.es_portal_cliente = True   # lo marca config.middleware.RuteoPorHost
            self.assertEqual(url_resultado(principal), "http://subepe.app/mi/recargar/resultado/")
            self.assertEqual(url_resultado(portal), "http://mi.subepe.app/recargar/resultado/")
