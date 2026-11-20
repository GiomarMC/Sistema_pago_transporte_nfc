import re
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from transporte.models import Alerta, Cuenta, Movimiento, Recarga, Tarifa, Tarjeta

from . import disparadores
from .models import Notificacion
from .muestras import MUESTRAS
from .servicios import _renderizar

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"


def _crear_test_de_plantilla(tipo):
    def test(self):
        asunto, texto, html = _renderizar(tipo, MUESTRAS[tipo])

        self.assertTrue(asunto.strip(), "el asunto no puede estar vacío")
        self.assertTrue(texto.strip(), "el texto plano no puede estar vacío")
        self.assertIn("<html", html)
        # Ninguna etiqueta o variable sin resolver
        for salida in (asunto, texto, html):
            self.assertNotIn("{{", salida)
            self.assertNotIn("{%", salida)
        # Sin imágenes ni recursos externos (se ven igual en Gmail web y móvil)
        self.assertNotIn("<img", html.lower())
        self.assertIsNone(re.search(r"""(src|href|background)=["']https?://""", html, re.I))
        # Los datos del contexto aparecen en ambas versiones (el asunto cuenta como texto plano)
        for valor in MUESTRAS[tipo].values():
            self.assertIn(str(valor), f"{asunto}\n{texto}")
            self.assertIn(str(valor), html)
    test.__name__ = f"test_render_{tipo}"
    return test


class PlantillasTests(TestCase):
    """Un test de renderizado por cada tipo de notificación (texto plano y HTML)."""


for _tipo in MUESTRAS:
    setattr(PlantillasTests, f"test_render_{_tipo}", _crear_test_de_plantilla(_tipo))


class PrevisualizacionTests(TestCase):
    def test_comando_escribe_html_y_texto_de_cada_plantilla(self):
        with tempfile.TemporaryDirectory() as carpeta:
            call_command("previsualizar_notificaciones", carpeta, stdout=mock.Mock())
            for tipo in MUESTRAS:
                self.assertTrue((Path(carpeta) / f"{tipo}.html").read_text(encoding="utf-8").strip())
                self.assertTrue((Path(carpeta) / f"{tipo}.txt").read_text(encoding="utf-8").strip())


@override_settings(EMAIL_BACKEND=LOCMEM)
class DisparadoresTests(TestCase):
    def setUp(self):
        # Código libre: las tarifas 1 y 2 ya las crea la migración de datos iniciales
        self.tarifa = Tarifa.objects.create(codigo=99, nombre="Prueba", precio=200)  # S/ 2.00
        self.cuenta = Cuenta.objects.create(
            nombre="Ana Quispe", documento="71234567", tarifa=self.tarifa, saldo=5000,
            correo="ana@example.com", correo_verificado_en=timezone.now())
        self.tarjeta = Tarjeta.objects.create(uid="04AABBCCDDEEFF", cuenta=self.cuenta,
                                              estado=Tarjeta.Estado.ACTIVA)

    def viaje(self, saldo=None, ref="V101-1"):
        if saldo is not None:
            self.cuenta.saldo = saldo
            self.cuenta.save(update_fields=["saldo"])
        return Movimiento.objects.create(
            fecha=timezone.now(), cuenta=self.cuenta, tarjeta=self.tarjeta, tipo="viaje",
            monto=-200, saldo_final=self.cuenta.saldo, ref=ref)

    def test_sin_correo_verificado_no_notifica_nada(self):
        self.cuenta.correo_verificado_en = None
        self.cuenta.save(update_fields=["correo_verificado_en"])
        orden = Recarga.objects.create(cuenta=self.cuenta, seq=1, monto=2000, origen="web")
        self.tarjeta.estado = Tarjeta.Estado.BLOQUEADA
        self.tarjeta.bloqueada_en = timezone.now()

        self.assertEqual(disparadores.al_acreditar(orden), [])
        self.assertEqual(disparadores.al_bloquear(self.tarjeta), [])
        self.assertEqual(disparadores.al_registrar_viaje(self.viaje(saldo=100)), [])
        self.assertFalse(Notificacion.objects.exists())

    def test_correo_sin_verificar_aunque_este_informado_no_notifica(self):
        self.cuenta.correo_verificado_en = None
        self.assertIsNone(disparadores.correo_verificado(self.cuenta))
        self.cuenta.correo_verificado_en = timezone.now()
        self.cuenta.correo = ""
        self.assertIsNone(disparadores.correo_verificado(self.cuenta))

    def test_al_acreditar_notifica_la_recarga(self):
        orden = Recarga.objects.create(cuenta=self.cuenta, seq=1, monto=2000, origen="web")

        with self.captureOnCommitCallbacks(execute=True):
            enviadas = disparadores.al_acreditar(orden)

        self.assertEqual(len(enviadas), 1)
        self.assertEqual(mail.outbox[0].to, ["ana@example.com"])
        self.assertIn("S/ 20.00", mail.outbox[0].subject)

    def test_al_bloquear_notifica_el_bloqueo(self):
        self.tarjeta.estado = Tarjeta.Estado.BLOQUEADA
        self.tarjeta.bloqueada_en = timezone.now()

        with self.captureOnCommitCallbacks(execute=True):
            disparadores.al_bloquear(self.tarjeta)

        self.assertEqual(mail.outbox[0].subject, "Tu tarjeta SubePe fue bloqueada")
        self.assertIn(f"N.º {self.tarjeta.id}", mail.outbox[0].body)

    def test_saldo_bajo_cuando_saldo_menor_a_dos_tarifas(self):
        with self.captureOnCommitCallbacks(execute=True):
            disparadores.al_registrar_viaje(self.viaje(saldo=399))  # < 2 × S/ 2.00
        self.assertEqual(Notificacion.objects.filter(tipo="saldo_bajo").count(), 1)

    def test_saldo_igual_a_dos_tarifas_no_avisa(self):
        with self.captureOnCommitCallbacks(execute=True):
            enviadas = disparadores.al_registrar_viaje(self.viaje(saldo=400))  # = 2 × tarifa
        self.assertEqual(enviadas, [])
        self.assertFalse(Notificacion.objects.exists())

    def test_saldo_bajo_como_maximo_una_vez_al_dia(self):
        with self.captureOnCommitCallbacks(execute=True):
            disparadores.al_registrar_viaje(self.viaje(saldo=100, ref="V101-1"))
            segundo = disparadores.al_registrar_viaje(self.viaje(saldo=50, ref="V101-2"))

        self.assertEqual(segundo, [])
        self.assertEqual(Notificacion.objects.filter(tipo="saldo_bajo").count(), 1)

    def test_saldo_bajo_vuelve_a_avisar_al_dia_siguiente(self):
        with self.captureOnCommitCallbacks(execute=True):
            disparadores.al_registrar_viaje(self.viaje(saldo=100, ref="V101-1"))
        Notificacion.objects.update(creada=timezone.now() - timedelta(days=1))

        with self.captureOnCommitCallbacks(execute=True):
            disparadores.al_registrar_viaje(self.viaje(saldo=100, ref="V101-2"))

        self.assertEqual(Notificacion.objects.filter(tipo="saldo_bajo").count(), 2)

    def test_uso_de_tarjeta_bloqueada_cuando_hay_alerta_del_movimiento(self):
        movimiento = self.viaje(saldo=5000, ref="V101-7")
        Alerta.objects.create(tarjeta=self.tarjeta, tipo="uso de tarjeta no activa",
                              detalle="V101-7: tarjeta 'bloqueada' aceptada por un validador")

        with self.captureOnCommitCallbacks(execute=True):
            enviadas = disparadores.al_registrar_viaje(movimiento)

        self.assertEqual([n.tipo for n in enviadas], ["uso_tarjeta_bloqueada"])
        self.assertIn("bloqueada", mail.outbox[0].subject.lower())

    def test_sin_alerta_no_avisa_de_uso_bloqueado(self):
        with self.captureOnCommitCallbacks(execute=True):
            enviadas = disparadores.al_registrar_viaje(self.viaje(saldo=5000, ref="V101-8"))
        self.assertEqual(enviadas, [])
        self.assertEqual(mail.outbox, [])

    def test_alerta_de_otro_movimiento_no_cuenta(self):
        Alerta.objects.create(tarjeta=self.tarjeta, tipo="uso de tarjeta no activa",
                              detalle="V101-99: otro viaje")
        with self.captureOnCommitCallbacks(execute=True):
            enviadas = disparadores.al_registrar_viaje(self.viaje(saldo=5000, ref="V101-8"))
        self.assertEqual(enviadas, [])
