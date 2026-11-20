"""Flujo M17 en servidor; la propagación física requiere validadores reales."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.db.models import Max
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from clientes.models import Cliente, CodigoActivacion
from libro import libro
from libro.models import PREFIJO_CLIENTE
from notificaciones.models import Notificacion
from pagos.models import ClienteCuenta
from transporte import servicios
from transporte.models import Alerta, CambioLista, Cuenta, Movimiento, Tarifa, Tarjeta

from .models import SolicitudReposicion


@override_settings(MAILERS={"default": {
    "BACKEND": "django.core.mail.backends.locmem.EmailBackend",
}})
class RoboReposicionTests(TestCase):
    def setUp(self):
        tarifa = Tarifa.objects.get_or_create(pk=1, defaults={"nombre": "General", "precio": 200})[0]
        self.cuenta = Cuenta.objects.create(
            nombre="Ana", documento="M17-ANA", tarifa=tarifa, saldo=1000,
            correo="ana@example.test", correo_verificado_en=timezone.now())
        # Saldo de apertura explicado en el libro (M10-04)
        libro.registrar("apertura", f"apertura-{self.cuenta.pk}",
                        [(f"{PREFIJO_CLIENTE}{self.cuenta.pk}", 1000), ("AJUSTES", -1000)])
        self.usuario = get_user_model().objects.create_user(
            username="m17-ana", password="clave-segura-123")
        self.operador = get_user_model().objects.create_user(username="m17-operador")
        self.operador.groups.add(Group.objects.get_or_create(name="operadores")[0])
        self.tarjeta = Tarjeta.objects.create(
            uid="M17-ANTERIOR", cuenta=self.cuenta, estado=Tarjeta.Estado.ACTIVA)
        self.cliente = APIClient()
        self.cliente.force_login(self.usuario)
        self.emisor = APIClient()
        self.emisor.force_authenticate(self.operador)

    def bloquear(self, tarjeta=None, clave="clave-segura-123"):
        tarjeta = tarjeta or self.tarjeta
        return self.cliente.post(
            f"/api/v1/tarjetas/{tarjeta.pk}/bloquear/",
            {"motivo": "robo", "contrasena": clave}, format="json")

    def solicitud(self):
        return self.cliente.post("/api/v1/reposiciones/",
                                 {"tarjeta_id": self.tarjeta.pk}, format="json")

    def nueva_tarjeta(self):
        return Tarjeta.objects.create(uid="M17-NUEVA", cuenta=self.cuenta,
                                      estado=Tarjeta.Estado.PENDIENTE)

    def test_bloqueo_portal_autenticado_publica_cambio_y_avisos_idempotentes(self):
        # La titularidad de M12 crea el enlace mínimo que todavía usa M11/M19.
        Cliente.objects.create(usuario=self.usuario, cuenta=self.cuenta,
                               correo="ana@example.test", correo_verificado=True)
        self.assertEqual(self.cliente.get("/api/v1/resumen/").json()["tarjetas"][0]["id"],
                         self.tarjeta.pk)
        version = CambioLista.objects.aggregate(m=Max("version"))["m"] or 0
        with self.captureOnCommitCallbacks(execute=True):
            respuesta = self.bloquear()
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.tarjeta.refresh_from_db()
        self.assertEqual(self.tarjeta.estado, Tarjeta.Estado.BLOQUEADA)
        # Cambio de lista urgente: lo difunde el puente MQTT a todos los validadores
        cambio = CambioLista.objects.get(version__gt=version)
        self.assertEqual((cambio.tipo, cambio.op, cambio.a, cambio.urgente),
                         (CambioLista.Tipo.BLOQUEO, CambioLista.AGREGAR, self.tarjeta.pk, True))
        self.assertEqual(Notificacion.objects.filter(tipo="tarjeta_bloqueada").count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.bloquear().status_code, 409)
        self.assertEqual(CambioLista.objects.filter(version__gt=version).count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_bloqueo_rechaza_clave_erronea_y_tarjeta_ajena(self):
        ClienteCuenta.objects.create(usuario=self.usuario, cuenta=self.cuenta)
        self.assertEqual(self.bloquear(clave="incorrecta").status_code, 403)
        otra_cuenta = Cuenta.objects.create(nombre="Otra", documento="M17-OTRA",
                                            tarifa=self.cuenta.tarifa)
        ajena = Tarjeta.objects.create(uid="M17-AJENA", cuenta=otra_cuenta,
                                       estado=Tarjeta.Estado.ACTIVA)
        self.assertEqual(self.bloquear(ajena).status_code, 404)
        self.tarjeta.refresh_from_db()
        ajena.refresh_from_db()
        self.assertEqual((self.tarjeta.estado, ajena.estado), ("activa", "activa"))

    def test_bloqueo_notifica_al_correo_verificado_de_m12(self):
        self.cuenta.correo_verificado_en = None
        self.cuenta.save(update_fields=["correo_verificado_en"])
        Cliente.objects.create(usuario=self.usuario, cuenta=self.cuenta,
                               correo="m12@example.test", correo_verificado=True)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.bloquear().status_code, 200)
        self.assertEqual(mail.outbox[0].to, ["m12@example.test"])

    def test_identidad_m12_sin_verificar_no_usa_correo_antiguo(self):
        Cliente.objects.create(usuario=self.usuario, cuenta=self.cuenta,
                               correo="nuevo@example.test", correo_verificado=False)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.bloquear().status_code, 200)
        self.assertFalse(Notificacion.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_viaje_offline_posterior_al_bloqueo_avisa_una_sola_vez(self):
        ClienteCuenta.objects.create(usuario=self.usuario, cuenta=self.cuenta)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.bloquear().status_code, 200)
            evento = {"id": 17, "tipo": "viaje", "tarjeta_id": self.tarjeta.pk,
                      "fecha": timezone.now().isoformat(), "operacion": 1,
                      "contador": 2, "monto": 200}
            servicios.procesar_evento(101, evento)
            servicios.procesar_evento(101, evento)
        self.assertEqual(Movimiento.objects.filter(ref="V101-17").count(), 1)
        self.assertTrue(Alerta.objects.filter(tipo="uso de tarjeta no activa").exists())
        self.assertEqual(Notificacion.objects.filter(tipo="uso_tarjeta_bloqueada").count(), 1)
        self.assertEqual(len(mail.outbox), 2)

    def test_emision_confirmada_entrega_solicitud_y_enlaza_tarjeta_nueva(self):
        ClienteCuenta.objects.create(usuario=self.usuario, cuenta=self.cuenta)
        self.tarjeta.estado = Tarjeta.Estado.BLOQUEADA
        self.tarjeta.bloqueada_en = timezone.now()
        self.tarjeta.save(update_fields=["estado", "bloqueada_en"])
        respuesta = self.solicitud()
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        solicitud = SolicitudReposicion.objects.get(pk=respuesta.json()["id"])
        nueva = self.nueva_tarjeta()
        with self.captureOnCommitCallbacks(execute=True):
            confirmacion = self.emisor.post(
                f"/api/emisiones/{nueva.pk}/confirmar/", {}, format="json")
        self.assertEqual(confirmacion.status_code, 200, confirmacion.content)
        self.assertIn(solicitud.pk, confirmacion.data["reposiciones_entregadas"])
        self.assertIn("codigo_activacion", confirmacion.data)
        self.assertTrue(CodigoActivacion.objects.filter(tarjeta=nueva).exists())
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, SolicitudReposicion.Estado.ENTREGADA)
        self.assertEqual(solicitud.tarjeta_nueva_id, nueva.pk)
        self.assertEqual(solicitud.gestionada_por, self.operador)
        self.assertIsNotNone(solicitud.lista_en)
        self.assertIsNotNone(solicitud.entregada_en)
        self.assertEqual(Notificacion.objects.filter(tipo="reposicion_lista").count(), 1)
        self.assertEqual(self.cliente.get("/api/v1/reposiciones/").json()[0]["tarjeta_nueva_id"], nueva.pk)
        self.assertEqual(self.cliente.get("/api/v1/resumen/").json()["tarjetas"][0]["id"],
                         nueva.pk)
        self.assertEqual(confirmacion.data["tarjeta"]["estado"], "activa")

    def test_cliente_existente_no_recibe_codigo_y_lista_no_duplica_aviso(self):
        ClienteCuenta.objects.create(usuario=self.usuario, cuenta=self.cuenta)
        Cliente.objects.create(usuario=self.usuario, cuenta=self.cuenta,
                               correo="ana@example.test", correo_verificado=True)
        self.tarjeta.estado = Tarjeta.Estado.BLOQUEADA
        self.tarjeta.save(update_fields=["estado"])
        solicitud = SolicitudReposicion.objects.create(
            cliente=ClienteCuenta.objects.get(usuario=self.usuario), tarjeta=self.tarjeta,
            estado=SolicitudReposicion.Estado.LISTA, lista_en=timezone.now())
        nueva = self.nueva_tarjeta()
        respuesta = self.emisor.post(f"/api/emisiones/{nueva.pk}/confirmar/", {}, format="json")
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotIn("codigo_activacion", respuesta.data)
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.tarjeta_nueva_id, nueva.pk)
        self.assertEqual(solicitud.estado, "entregada")
        self.assertFalse(Notificacion.objects.filter(tipo="reposicion_lista").exists())

    def test_fallo_al_confirmar_revierte_entrega_y_emision(self):
        enlace = ClienteCuenta.objects.create(usuario=self.usuario, cuenta=self.cuenta)
        self.tarjeta.estado = Tarjeta.Estado.BLOQUEADA
        self.tarjeta.save(update_fields=["estado"])
        solicitud = SolicitudReposicion.objects.create(cliente=enlace, tarjeta=self.tarjeta)
        nueva = self.nueva_tarjeta()
        with patch("transporte.servicios.registrar_movimiento", side_effect=RuntimeError("fallo")):
            with self.assertRaises(RuntimeError):
                servicios.confirmar_emision(nueva.pk, emitido_por=self.operador)
        solicitud.refresh_from_db()
        nueva.refresh_from_db()
        self.assertEqual(solicitud.estado, "solicitada")
        self.assertIsNone(solicitud.tarjeta_nueva_id)
        self.assertEqual(nueva.estado, "pendiente")
