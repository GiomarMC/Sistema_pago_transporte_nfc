from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from panel.auth import es_operador
from transporte import servicios
from transporte.models import Cuenta, Tarifa, Tarjeta

from .models import (
    Cliente,
    CodigoActivacion,
    CodigoOTP,
    Dispositivo,
    numero_impreso,
    usuario_cliente,
)


class NumeroImpresoTests(SimpleTestCase):
    def test_base_de_ocho_digitos_con_control_luhn_en_grupos(self):
        casos = {1: "0000 0001 8", 123: "0000 0123 0", 12345678: "1234 5678 2"}
        for id_tarjeta, esperado in casos.items():
            with self.subTest(id_tarjeta=id_tarjeta):
                self.assertEqual(numero_impreso(SimpleNamespace(id=id_tarjeta)), esperado)


class ClientesTests(TestCase):
    def setUp(self):
        self.tarifa = Tarifa.objects.get(pk=1)
        self.cuenta = Cuenta.objects.create(nombre="Ana Quispe", documento="71234567", tarifa=self.tarifa)
        self.tarjeta = Tarjeta.objects.create(uid="04AABBCCDDEEFF", cuenta=self.cuenta)
        self.cliente = self.crear_cliente(self.cuenta, "ana@example.com")

    def crear_cliente(self, cuenta, correo):
        usuario = get_user_model().objects.create_user(username=usuario_cliente(cuenta.documento))
        return Cliente.objects.create(usuario=usuario, cuenta=cuenta, correo=correo)

    def otp(self, **campos):
        return CodigoOTP.objects.create(proposito=CodigoOTP.Proposito.INICIO, hash="x", **campos)

    def test_el_cliente_no_es_operador(self):
        self.assertEqual(self.cliente.usuario.username, "cli-71234567")
        self.assertFalse(self.cliente.correo_verificado)
        self.assertFalse(es_operador(self.cliente.usuario))

    def test_crear_operador_rechaza_el_prefijo_de_clientes(self):
        for usuario in (self.cliente.usuario.username, "CLI-70000001"):
            with self.subTest(usuario=usuario), self.assertRaises(CommandError):
                call_command("crear_operador", usuario)
        self.assertFalse(es_operador(self.cliente.usuario))
        self.assertFalse(get_user_model().objects.filter(username="CLI-70000001").exists())

    def test_los_codigos_se_guardan_solo_como_hash(self):
        for codigo in (CodigoActivacion(tarjeta=self.tarjeta), CodigoOTP(cliente=self.cliente)):
            with self.subTest(modelo=type(codigo).__name__):
                codigo.asignar_codigo("482913")
                codigo.save()
                codigo.refresh_from_db()
                self.assertNotIn("482913", codigo.hash)
                self.assertTrue(codigo.verificar_codigo("482913"))
                self.assertFalse(codigo.verificar_codigo("000000"))

    def test_vencimientos_por_defecto(self):
        margen = timedelta(minutes=1)
        activacion = CodigoActivacion(tarjeta=self.tarjeta)
        self.assertAlmostEqual(activacion.expira, timezone.now() + timedelta(hours=72), delta=margen)
        otp = CodigoOTP(cliente=self.cliente)
        self.assertAlmostEqual(otp.expira, timezone.now() + timedelta(minutes=10), delta=margen)

    def test_otp_pertenece_a_cliente_o_a_token(self):
        self.otp(cliente=self.cliente)
        self.otp(token_activacion="a" * 64)
        for campos in ({}, {"cliente": self.cliente, "token_activacion": "a" * 64}):
            with self.subTest(campos=campos), self.assertRaises(IntegrityError), transaction.atomic():
                self.otp(**campos)

    def test_otp_admite_cinco_intentos_como_maximo(self):
        self.otp(cliente=self.cliente, intentos=5)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.otp(cliente=self.cliente, intentos=6)

    def test_huella_unica_por_cliente(self):
        Dispositivo.objects.create(cliente=self.cliente, huella="h" * 64)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Dispositivo.objects.create(cliente=self.cliente, huella="h" * 64)
        cuenta = Cuenta.objects.create(nombre="Luis Mamani", documento="70000001", tarifa=self.tarifa)
        otro = self.crear_cliente(cuenta, "luis@example.com")
        Dispositivo.objects.create(cliente=otro, huella="h" * 64)

    def test_cancelar_emision_elimina_sus_codigos(self):
        CodigoActivacion.objects.create(tarjeta=self.tarjeta, hash="x")
        servicios.cancelar_emision(self.tarjeta.id)
        self.assertFalse(CodigoActivacion.objects.exists())
        self.assertFalse(Tarjeta.objects.filter(pk=self.tarjeta.pk).exists())
