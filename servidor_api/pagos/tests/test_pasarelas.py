import hmac
import inspect
import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from pagos.pasarelas import firma, obtener_pasarela
from pagos.pasarelas.base import (
    CobroCreado, ErrorPasarela, EstadoCobro, EventoPasarela, FirmaInvalida, Pasarela,
    Transaccion,
)
from pagos.pasarelas.falsa import PasarelaFalsa

LIMA = ZoneInfo("America/Lima")
INICIO = datetime(2026, 11, 6, 10, 0, tzinfo=LIMA)


class Reloj:
    """Hora controlable para los tests."""

    def __init__(self, ahora=INICIO):
        self.ahora = ahora

    def __call__(self):
        return self.ahora

    def avanzar(self, **kwargs):
        self.ahora += timedelta(**kwargs)


class Base(SimpleTestCase):
    def setUp(self):
        self.reloj = Reloj()
        self.falsa = PasarelaFalsa(secreto="secreto-test", reloj=self.reloj)

    def cobro(self, monto=1500):
        return self.falsa.crear_cobro(orden_id=uuid.uuid4(), monto=monto,
                                      descripcion="Recarga S/ 15.00", correo="ana@ejemplo.pe",
                                      url_retorno="https://subepe.app/portal/recargas/")


class CumpleElContrato(SimpleTestCase):
    def test_tiene_los_metodos_de_c5_con_la_misma_firma(self):
        for nombre in ("crear_cobro", "consultar", "verificar_webhook", "reembolsar",
                       "listar_transacciones"):
            with self.subTest(nombre):
                self.assertEqual(inspect.signature(getattr(Pasarela, nombre)),
                                 inspect.signature(getattr(PasarelaFalsa, nombre)))
        self.assertEqual(PasarelaFalsa.nombre, "falsa")


class CrearCobro(Base):
    def test_devuelve_url_y_expiracion(self):
        c = self.cobro()
        self.assertIsInstance(c, CobroCreado)
        self.assertTrue(c.id_externo.startswith("falsa_"))
        self.assertIn(c.id_externo, c.url_pago)
        self.assertEqual(c.expira, INICIO + timedelta(minutes=30))

    def test_cada_cobro_tiene_su_id(self):
        self.assertNotEqual(self.cobro().id_externo, self.cobro().id_externo)

    def test_rechaza_montos_no_validos(self):
        for monto in (0, -100, 15.5, True):
            with self.subTest(monto), self.assertRaises(ValueError):
                self.cobro(monto=monto)


class Consultar(Base):
    def test_un_cobro_nuevo_esta_pendiente(self):
        c = self.cobro()
        self.assertEqual(self.falsa.consultar(c.id_externo),
                         EstadoCobro(c.id_externo, "pendiente", 1500, None))

    def test_se_puede_fijar_el_resultado(self):
        c = self.cobro()
        self.falsa.fijar_estado(c.id_externo, "pagado")
        self.assertEqual(self.falsa.consultar(c.id_externo),
                         EstadoCobro(c.id_externo, "pagado", 1500, INICIO))
        self.falsa.fijar_estado(c.id_externo, "pagado", monto=1400)  # pagó otra cifra
        self.assertEqual(self.falsa.consultar(c.id_externo).monto, 1400)
        self.falsa.fijar_estado(c.id_externo, "fallido")
        self.assertEqual(self.falsa.consultar(c.id_externo),
                         EstadoCobro(c.id_externo, "fallido", 1400, None))
        with self.assertRaises(ValueError):
            self.falsa.fijar_estado(c.id_externo, "aprobado")

    def test_pendiente_pasa_a_expirado_al_vencer(self):
        c = self.cobro()
        self.reloj.avanzar(minutes=30)
        self.assertEqual(self.falsa.consultar(c.id_externo).estado, "expirado")

    def test_cobro_desconocido_y_fallo_simulado(self):
        with self.assertRaises(ErrorPasarela):
            self.falsa.consultar("falsa_no_existe")
        c = self.cobro()
        self.falsa.fallar("consultar")
        with self.assertRaises(ErrorPasarela):
            self.falsa.consultar(c.id_externo)
        self.assertEqual(self.falsa.consultar(c.id_externo).estado, "pendiente")  # solo una vez


class VerificarWebhook(Base):
    def test_acepta_el_webhook_que_genera(self):
        c = self.cobro()
        cabeceras, cuerpo = self.falsa.pagar(c.id_externo)
        evento = self.falsa.verificar_webhook(cabeceras, cuerpo)
        self.assertIsInstance(evento, EventoPasarela)
        self.assertEqual((evento.tipo, evento.id_externo), ("cobro.pagado", c.id_externo))
        self.assertTrue(evento.id_evento)
        self.assertEqual(self.falsa.consultar(c.id_externo).estado, "pagado")

    def test_la_cabecera_no_distingue_mayusculas(self):
        cabeceras, cuerpo = self.falsa.generar_webhook("falsa_x", "cobro.fallido")
        evento = self.falsa.verificar_webhook({"x-firma": cabeceras["X-Firma"]}, cuerpo)
        self.assertEqual(evento.tipo, "cobro.fallido")

    def test_rechaza_cuerpo_alterado_o_firma_de_otro_secreto(self):
        cabeceras, cuerpo = self.falsa.generar_webhook("falsa_x")
        with self.assertRaises(FirmaInvalida):
            self.falsa.verificar_webhook(cabeceras, cuerpo.replace(b"falsa_x", b"falsa_y"))
        otra = PasarelaFalsa(secreto="otro-secreto", reloj=self.reloj)
        with self.assertRaises(FirmaInvalida):
            self.falsa.verificar_webhook(*otra.generar_webhook("falsa_x"))

    def test_rechaza_fuera_de_los_300_s(self):
        t = int(INICIO.timestamp())
        self.falsa.verificar_webhook(*self.falsa.generar_webhook("falsa_x", t=t - 300))
        self.falsa.verificar_webhook(*self.falsa.generar_webhook("falsa_x", t=t + 300))
        for desfase in (-301, 301):
            with self.subTest(desfase), self.assertRaises(FirmaInvalida):
                self.falsa.verificar_webhook(*self.falsa.generar_webhook("falsa_x",
                                                                         t=t + desfase))

    def test_rechaza_cabecera_ausente_o_mal_formada(self):
        _, cuerpo = self.falsa.generar_webhook("falsa_x")
        for cabeceras in ({}, {"X-Firma": ""}, {"X-Firma": "basura"},
                          {"X-Firma": "t=abc,v1=00"}, {"X-Firma": "v1=00"}):
            with self.subTest(cabeceras), self.assertRaises(FirmaInvalida):
                self.falsa.verificar_webhook(cabeceras, cuerpo)

    def test_cuerpo_firmado_pero_inesperado(self):
        t = int(INICIO.timestamp())
        for cuerpo in (b"no es json", json.dumps({"tipo": "cobro.pagado"}).encode(),
                       json.dumps({"id_evento": "e", "tipo": "cobro.raro",
                                   "id_externo": "x"}).encode()):
            cabeceras = {"X-Firma": firma.firmar("secreto-test", cuerpo, t)}
            with self.subTest(cuerpo), self.assertRaises(ErrorPasarela):
                self.falsa.verificar_webhook(cabeceras, cuerpo)


class Reembolsar(Base):
    def test_reembolsa_parcial_y_total_sin_pasarse(self):
        c = self.cobro()
        self.falsa.pagar(c.id_externo)
        primero = self.falsa.reembolsar(c.id_externo, 500)
        segundo = self.falsa.reembolsar(c.id_externo, 1000)
        self.assertTrue(primero.startswith("falsa_reemb_"))
        self.assertNotEqual(primero, segundo)
        with self.assertRaises(ValueError):
            self.falsa.reembolsar(c.id_externo, 1)  # ya se devolvió todo

    def test_solo_cobros_pagados_y_montos_validos(self):
        c = self.cobro()
        with self.assertRaises(ValueError):
            self.falsa.reembolsar(c.id_externo, 500)  # pendiente
        self.falsa.pagar(c.id_externo)
        for monto in (0, -1, 1501):
            with self.subTest(monto), self.assertRaises(ValueError):
                self.falsa.reembolsar(c.id_externo, monto)
        with self.assertRaises(ErrorPasarela):
            self.falsa.reembolsar("falsa_no_existe", 100)


class ListarTransacciones(Base):
    def test_lista_pagos_y_reembolsos_del_dia_local(self):
        a, b, pendiente = self.cobro(1000), self.cobro(2000), self.cobro(3000)
        self.falsa.pagar(a.id_externo)
        self.reloj.avanzar(hours=1)
        self.falsa.pagar(b.id_externo)
        self.reloj.avanzar(minutes=5)
        self.falsa.reembolsar(a.id_externo, 400)
        self.reloj.avanzar(hours=14)  # 01:00 del día siguiente en Lima (06:00 UTC)
        self.falsa.reembolsar(b.id_externo, 2000)

        hoy = self.falsa.listar_transacciones(INICIO.date())
        self.assertEqual(hoy, [
            Transaccion(a.id_externo, 1000, "pagado", INICIO),
            Transaccion(b.id_externo, 2000, "pagado", INICIO + timedelta(hours=1)),
            Transaccion(a.id_externo, 400, "reembolsado", INICIO + timedelta(hours=1, minutes=5)),
        ])
        manana = self.falsa.listar_transacciones(INICIO.date() + timedelta(days=1))
        self.assertEqual([(t.id_externo, t.monto, t.estado) for t in manana],
                         [(b.id_externo, 2000, "reembolsado")])
        self.assertNotIn(pendiente.id_externo, [t.id_externo for t in hoy + manana])

    def test_dia_sin_movimientos(self):
        self.assertEqual(self.falsa.listar_transacciones(INICIO.date()), [])


class Firma(SimpleTestCase):
    def test_formato_de_la_cabecera(self):
        esperado = hmac.new(b"s", b"1700000000.{}", "sha256").hexdigest()
        self.assertEqual(firma.firmar("s", b"{}", 1700000000), f"t=1700000000,v1={esperado}")

    def test_sin_secreto_no_firma_ni_verifica(self):
        with self.assertRaises(ImproperlyConfigured):
            firma.firmar("", b"{}")
        with self.assertRaises(ImproperlyConfigured):
            firma.verificar("", {"X-Firma": "t=1,v1=00"}, b"{}", ahora=1)


class ObtenerPasarela(SimpleTestCase):
    @override_settings(PAGOS_PASARELA="falsa", PAGOS_SECRETO_WEBHOOK="del-ajuste")
    def test_devuelve_la_falsa_siempre_la_misma(self):
        p = obtener_pasarela()
        self.assertIsInstance(p, PasarelaFalsa)
        self.assertIs(p, obtener_pasarela())
        self.assertEqual(p.secreto, "del-ajuste")

    def test_cambiar_el_ajuste_cambia_la_instancia(self):
        with self.settings(PAGOS_PASARELA="falsa"):
            primera = obtener_pasarela()
        with self.settings(PAGOS_PASARELA="falsa"):
            self.assertIsNot(primera, obtener_pasarela())

    @override_settings(PAGOS_PASARELA="inventada")
    def test_nombre_desconocido(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "inventada"):
            obtener_pasarela()

    @override_settings(PAGOS_PASARELA="simulada")
    def test_simulada_aun_no_existe(self):
        # Se implementa en M11-02; hasta entonces el error lo dice claramente
        with self.assertRaisesMessage(ImproperlyConfigured, "todavía no está implementada"):
            obtener_pasarela()
