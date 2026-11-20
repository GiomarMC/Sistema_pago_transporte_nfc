import uuid
from smtplib import SMTPServerDisconnected
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from pagos.models import ClienteCuenta, CobroSimulado
from pagos.servicios import confirmar_cobro, crear_orden
from transporte import servicios as transporte
from transporte.models import Cuenta, Tarifa, Tarjeta

from .models import Notificacion


@override_settings(MAILERS={"default": {
    "BACKEND": "django.core.mail.backends.locmem.EmailBackend",
}})
class IntegracionTests(TestCase):
    def setUp(self):
        tarifa = Tarifa.objects.create(codigo=77, nombre="M13", precio=200)
        self.cuenta = Cuenta.objects.create(
            nombre="Ana", documento="M13-1", tarifa=tarifa,
            correo="ana@example.com", correo_verificado_en=timezone.now())
        self.usuario = get_user_model().objects.create_user(username="m13-cliente")
        ClienteCuenta.objects.create(usuario=self.usuario, cuenta=self.cuenta)

    def _orden_pagada(self):
        orden = crear_orden(self.usuario, self.cuenta, 1000, uuid.uuid4())
        CobroSimulado.objects.filter(orden=orden).update(
            estado="pagado", pagado_en=timezone.now())
        return orden

    def test_recarga_confirmada_notifica_una_sola_vez(self):
        orden = self._orden_pagada()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(confirmar_cobro(orden))
        self.assertTrue(confirmar_cobro(orden))
        self.assertEqual(Notificacion.objects.filter(tipo="recarga_acreditada").count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.saldo, 1000)

    def test_fallo_smtp_no_revierte_recarga(self):
        orden = self._orden_pagada()
        with patch("django.core.mail.backends.locmem.EmailBackend.send_messages",
                   side_effect=SMTPServerDisconnected("sin conexión")):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertTrue(confirmar_cobro(orden))
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.saldo, 1000)
        aviso = Notificacion.objects.get(tipo="recarga_acreditada")
        self.assertEqual(aviso.estado, Notificacion.PENDIENTE)
        self.assertEqual(aviso.intentos, 1)

    def test_error_al_preparar_aviso_no_revierte_recarga(self):
        orden = self._orden_pagada()
        with patch("notificaciones.servicios._renderizar", side_effect=ValueError("plantilla rota")):
            self.assertTrue(confirmar_cobro(orden))
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.saldo, 1000)
        self.assertFalse(Notificacion.objects.exists())

    def test_bloqueo_y_viaje_generan_avisos_sin_duplicarlos(self):
        with self.captureOnCommitCallbacks(execute=True):
            transporte.crear_recarga(self.cuenta.pk, 500, "punto")
        tarjeta = Tarjeta.objects.create(
            uid="M13-CARD", cuenta=self.cuenta, estado=Tarjeta.Estado.ACTIVA)
        with self.captureOnCommitCallbacks(execute=True):
            transporte.registrar_movimiento(
                self.cuenta, tarjeta, "viaje", -200, ref="M13-V1")
            transporte.registrar_movimiento(
                self.cuenta, tarjeta, "viaje", -200, ref="M13-V2")
            transporte.bloquear(tarjeta, "prueba")
            transporte.bloquear(tarjeta, "repetido")
        self.assertEqual(Notificacion.objects.filter(tipo="saldo_bajo").count(), 1)
        self.assertEqual(Notificacion.objects.filter(tipo="tarjeta_bloqueada").count(), 1)
        self.assertEqual(len(mail.outbox), 2)
