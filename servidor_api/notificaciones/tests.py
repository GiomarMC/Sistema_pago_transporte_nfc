from datetime import timedelta
from io import StringIO
from smtplib import SMTPServerDisconnected
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from transporte.models import Alerta

from .models import Notificacion
from .servicios import (
    ALERTA_FALLIDA, ESPERAS_MINUTOS, LIMITE_POR_EJECUCION, MAX_INTENTOS,
    notificar, procesar_pendientes,
)

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"
FALLO_SMTP = "django.core.mail.backends.locmem.EmailBackend.send_messages"


def caida():
    return SMTPServerDisconnected("caída")


@override_settings(EMAIL_BACKEND=LOCMEM)
class EnvioTests(TestCase):
    def test_transaccion_revertida_no_envia_ni_guarda(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    notificar("ana@example.com", "otp", codigo="123456", minutos=5)
                    raise RuntimeError("la operación se revierte")

        self.assertEqual(callbacks, [])
        self.assertEqual(mail.outbox, [])
        self.assertFalse(Notificacion.objects.exists())

    def test_otp_se_envia_al_confirmar(self):
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                notificar("ana@example.com", "otp", codigo="123456", minutos=5)

        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.subject, "Código de verificación")
        self.assertIn("123456", correo.body)
        self.assertEqual(correo.to, ["ana@example.com"])
        self.assertIn("<strong", correo.alternatives[0][0])

        n = Notificacion.objects.get()
        self.assertEqual(n.estado, Notificacion.ENVIADA)
        self.assertEqual(n.intentos, 1)
        self.assertIsNotNone(n.enviada_en)
        self.assertIsNone(n.proximo_intento)

    def test_fallo_smtp_deja_pendiente_con_error(self):
        with mock.patch(FALLO_SMTP, side_effect=caida()):
            with self.captureOnCommitCallbacks(execute=True):
                # No debe propagar la excepción: la operación que notifica sigue adelante
                notificar("ana@example.com", "otp", codigo="123456", minutos=5)

        self.assertEqual(mail.outbox, [])
        n = Notificacion.objects.get()
        self.assertEqual(n.estado, Notificacion.PENDIENTE)
        self.assertEqual(n.intentos, 1)
        self.assertIsNone(n.enviada_en)
        self.assertIn("SMTPServerDisconnected", n.error)
        self.assertIn("caída", n.error)
        # Primer reintento: 5 minutos después del fallo
        espera = n.proximo_intento - timezone.now()
        self.assertAlmostEqual(espera.total_seconds(), 5 * 60, delta=60)

    def test_reintento_tras_fallo_envia_y_conserva_error(self):
        with mock.patch(FALLO_SMTP, side_effect=caida()):
            with self.captureOnCommitCallbacks(execute=True):
                notificar("ana@example.com", "otp", codigo="123456", minutos=5)
        n = Notificacion.objects.get()

        procesar_pendientes(ahora=n.proximo_intento)

        n.refresh_from_db()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(n.estado, Notificacion.ENVIADA)
        self.assertEqual(n.intentos, 2)
        self.assertIsNotNone(n.enviada_en)
        self.assertIn("caída", n.error)  # se conserva el último error registrado

    def test_avisos_se_envian_en_orden_de_creacion(self):
        for titulo in ("Primero", "Segundo", "Tercero"):
            with self.captureOnCommitCallbacks(execute=True):
                notificar("ana@example.com", "aviso", titulo=titulo, mensaje="m")

        self.assertEqual([c.subject for c in mail.outbox], ["Primero", "Segundo", "Tercero"])

    def test_fallo_en_aviso_bloquea_los_siguientes(self):
        with mock.patch(FALLO_SMTP, side_effect=caida()):
            for titulo in ("Primero", "Segundo"):
                with self.captureOnCommitCallbacks(execute=True):
                    notificar("ana@example.com", "aviso", titulo=titulo, mensaje="m")

        # El primero falló y espera su reintento; el segundo ni se intentó (orden)
        self.assertEqual(mail.outbox, [])
        primero, segundo = Notificacion.objects.order_by("pk")
        self.assertEqual(primero.intentos, 1)
        self.assertEqual(segundo.intentos, 0)

        primero.refresh_from_db()
        procesar_pendientes(ahora=primero.proximo_intento)

        self.assertEqual([c.subject for c in mail.outbox], ["Primero", "Segundo"])
        self.assertFalse(Notificacion.objects.filter(estado=Notificacion.PENDIENTE).exists())

    def test_otp_pendiente_no_bloquea_la_cola(self):
        with mock.patch(FALLO_SMTP, side_effect=caida()):
            with self.captureOnCommitCallbacks(execute=True):
                notificar("ana@example.com", "otp", codigo="111111", minutos=5)
        with self.captureOnCommitCallbacks(execute=True):
            notificar("ana@example.com", "aviso", titulo="Aviso", mensaje="m")

        self.assertEqual([c.subject for c in mail.outbox], ["Aviso"])
        self.assertEqual(Notificacion.objects.get(tipo="otp").estado, Notificacion.PENDIENTE)


@override_settings(EMAIL_BACKEND=LOCMEM)
class ReintentosTests(TestCase):
    def crear_fallida_en_curso(self):
        """Notificación OTP con un primer intento fallido; devuelve la notificación."""
        with mock.patch(FALLO_SMTP, side_effect=caida()):
            with self.captureOnCommitCallbacks(execute=True):
                notificar("ana@example.com", "otp", codigo="123456", minutos=5)
        return Notificacion.objects.get()

    def test_espera_creciente_5_15_60_240_minutos(self):
        n = self.crear_fallida_en_curso()
        with mock.patch(FALLO_SMTP, side_effect=caida()):
            for intentos in range(1, MAX_INTENTOS):   # intentos ya hechos: 1, 2, 3, 4
                antes = n.proximo_intento
                # Un segundo antes de la espera, no hay reintento
                procesar_pendientes(ahora=antes - timedelta(seconds=1))
                n.refresh_from_db()
                self.assertEqual(n.intentos, intentos)

                # Cumplida la espera, se reintenta y la siguiente espera es la que toca
                procesar_pendientes(ahora=antes)
                n.refresh_from_db()
                self.assertEqual(n.intentos, intentos + 1)
                if n.estado == Notificacion.PENDIENTE:
                    espera_aplicada = n.proximo_intento - antes
                    self.assertEqual(espera_aplicada,
                                     timedelta(minutes=ESPERAS_MINUTOS[intentos]))

    def reintentar_hasta_el_final(self, n, ciclos=10):
        """Avanza el reloj hasta cada reintento previsto, hasta que la notificación deja de estar pendiente."""
        for _ in range(ciclos):
            n.refresh_from_db()
            if n.estado != Notificacion.PENDIENTE:
                break
            procesar_pendientes(ahora=n.proximo_intento)

    def test_maximo_cinco_intentos_y_queda_fallida(self):
        n = self.crear_fallida_en_curso()
        with mock.patch(FALLO_SMTP, side_effect=caida()) as envio:
            self.reintentar_hasta_el_final(n)
            # Muchos más ciclos después: ya no se intenta
            procesar_pendientes(ahora=timezone.now() + timedelta(days=30))

        n.refresh_from_db()
        self.assertEqual(envio.call_count, MAX_INTENTOS - 1)  # el primero fue en el alta
        self.assertEqual(n.intentos, MAX_INTENTOS)
        self.assertEqual(n.estado, Notificacion.FALLIDA)
        self.assertIsNone(n.proximo_intento)

    def test_alerta_cuando_queda_fallida(self):
        n = self.crear_fallida_en_curso()
        self.assertFalse(Alerta.objects.filter(tipo=ALERTA_FALLIDA).exists())

        with mock.patch(FALLO_SMTP, side_effect=caida()):
            self.reintentar_hasta_el_final(n)

        alertas = Alerta.objects.filter(tipo=ALERTA_FALLIDA)
        self.assertEqual(alertas.count(), 1)
        self.assertIn(f"Notificación {n.pk}", alertas.get().detalle)
        self.assertIn("SMTPServerDisconnected", alertas.get().detalle)

    def test_exito_en_un_reintento_no_genera_alerta(self):
        n = self.crear_fallida_en_curso()
        procesar_pendientes(ahora=n.proximo_intento)

        n.refresh_from_db()
        self.assertEqual(n.estado, Notificacion.ENVIADA)
        self.assertFalse(Alerta.objects.filter(tipo=ALERTA_FALLIDA).exists())

    def test_no_procesa_mas_de_50_por_ejecucion(self):
        total = LIMITE_POR_EJECUCION + 10
        Notificacion.objects.bulk_create([
            Notificacion(destinatario="ana@example.com", tipo="aviso", asunto=f"Aviso {i}",
                         texto="m", html="<p>m</p>")
            for i in range(total)
        ])

        intentadas = procesar_pendientes()

        self.assertEqual(intentadas, LIMITE_POR_EJECUCION)
        self.assertEqual(len(mail.outbox), LIMITE_POR_EJECUCION)
        self.assertEqual(Notificacion.objects.filter(estado=Notificacion.PENDIENTE).count(), 10)
        # Las restantes siguen pendientes y se envían en la siguiente ejecución
        self.assertEqual(Notificacion.objects.filter(intentos=0).count(), 10)
        procesar_pendientes()
        self.assertEqual(len(mail.outbox), total)

    def test_comando_enviar_notificaciones(self):
        Notificacion.objects.bulk_create([
            Notificacion(destinatario="ana@example.com", tipo="aviso", asunto="A",
                         texto="m", html="<p>m</p>")
            for _ in range(LIMITE_POR_EJECUCION + 1)
        ])
        salida = StringIO()

        call_command("enviar_notificaciones", stdout=salida)

        self.assertIn(f"Intentadas: {LIMITE_POR_EJECUCION}.", salida.getvalue())
        self.assertIn("Pendientes: 1", salida.getvalue())
        self.assertEqual(len(mail.outbox), LIMITE_POR_EJECUCION)
