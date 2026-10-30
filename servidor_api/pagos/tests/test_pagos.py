import json
import io
import threading
import time
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from libro.libro import saldo_cliente
from libro.models import Asiento, CuentaContable
from pagos.models import ClienteCuenta, CobroSimulado, EventoPasarela, HistorialOrden, OrdenRecarga
from pagos.pasarelas.simulada import firmar
from pagos.servicios import (ErrorPago, IdempotenciaConflicto, LimitePago,
                             TransicionInvalida, acreditar, confirmar_cobro, crear_orden,
                             reembolsar, revisar_orden, transicion)
from transporte import servicios as transporte
from transporte.models import Alerta, Cuenta, Movimiento, Recarga, Tarifa, Validador
from transporte.sync_v2.paquetes import PENDIENTE, RECARGA
from transporte.sync_v2.servicio import listas_para


@override_settings(PAGOS_SECRETO_WEBHOOK="secreto-de-pruebas")
class PagosTests(TestCase):
    def setUp(self):
        self.tarifa = Tarifa.objects.create(codigo=55, nombre="Prueba M11", precio=100)
        self.cuenta = Cuenta.objects.create(nombre="Cliente", documento="M11-1", tarifa=self.tarifa)
        self.usuario = get_user_model().objects.create_user(username="cliente-m11", password="clave-m11")
        ClienteCuenta.objects.create(usuario=self.usuario, cuenta=self.cuenta)
        self.operador = get_user_model().objects.create_user(username="operador-m11", is_staff=True)

    def orden(self, monto=1000, clave=None):
        return crear_orden(self.usuario, self.cuenta, monto, clave or uuid.uuid4())

    def pagar(self, orden):
        CobroSimulado.objects.filter(orden=orden).update(estado="pagado", pagado_en=timezone.now())
        return confirmar_cobro(orden)

    def webhook(self, orden, *, evento_id=None, firma=True, instante=None):
        cuerpo = json.dumps({"id_evento": evento_id or str(uuid.uuid4()), "tipo": "cobro.pagado",
                             "id_externo": orden.id_externo}).encode()
        cabeceras = firmar(cuerpo, instante)
        if not firma:
            cabeceras["X-Firma"] = f"t={int(time.time())},v1=" + "0" * 64
        return Client().post("/api/pagos/webhook/simulada/", cuerpo,
                             content_type="application/json",
                             **{"HTTP_" + k.upper().replace("-", "_"): v for k, v in cabeceras.items()})

    def test_modelos_unicidad(self):
        orden = self.orden()
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrdenRecarga.objects.create(cuenta=self.cuenta, usuario=self.usuario, monto=100,
                clave_idempotencia=orden.clave_idempotencia, expira=timezone.now())
        EventoPasarela.objects.create(pasarela="simulada", id_evento="e1", cuerpo=b"a")
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventoPasarela.objects.create(pasarela="simulada", id_evento="e1", cuerpo=b"a")

    def test_estados_y_limites(self):
        orden = self.orden()
        self.assertEqual(orden.estado, "pendiente")
        with self.assertRaises(TransicionInvalida):
            transicion(orden, "acreditada", "salto")
        with self.assertRaises(LimitePago):
            self.orden(99)
        self.orden(20000)
        self.orden(20000)
        with self.assertRaises(LimitePago):
            self.orden(10000)
        self.assertEqual(HistorialOrden.objects.filter(orden=orden).count(), 1)

    def test_idempotencia_y_conflicto(self):
        clave = uuid.uuid4()
        a = self.orden(1000, clave)
        b = self.orden(1000, clave)
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(CobroSimulado.objects.count(), 1)
        with self.assertRaises(IdempotenciaConflicto):
            self.orden(2000, clave)

    def test_tres_ordenes_pendientes_maximo(self):
        for _ in range(3):
            self.orden(100)
        with self.assertRaisesRegex(LimitePago, "máximo tres"):
            self.orden(100)

    def test_fallo_pasarela_deja_orden_creada_y_reintenta(self):
        clave = uuid.uuid4()
        with patch("pagos.pasarelas.simulada.PasarelaSimulada.crear_cobro", side_effect=Exception("fallo")):
            with self.assertRaises(Exception):
                self.orden(1000, clave)
        # El fallo genérico no es recuperable; un ErrorPasarela sí lo es.
        from pagos.pasarelas.base import ErrorPasarela
        with patch("pagos.pasarelas.simulada.PasarelaSimulada.crear_cobro", side_effect=ErrorPasarela("red")):
            creada = self.orden(1000, clave)
            self.assertEqual(creada.estado, "creada")
        self.assertEqual(self.orden(1000, clave).estado, "pendiente")

    def test_webhook_verificado_idempotente(self):
        orden = self.orden()
        self.assertEqual(self.webhook(orden, firma=False).status_code, 400)
        self.assertTrue(EventoPasarela.objects.filter(firma_valida=False).exists())
        self.assertEqual(self.webhook(orden, instante=0).status_code, 400)
        self.pagar(orden)
        evento = str(uuid.uuid4())
        self.assertEqual(self.webhook(orden, evento_id=evento).status_code, 200)
        self.assertEqual(self.webhook(orden, evento_id=evento).status_code, 200)
        self.assertEqual(Recarga.objects.count(), 1)
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.saldo, 1000)
        self.assertEqual(saldo_cliente(self.cuenta), 1000)
        self.assertEqual(Asiento.objects.filter(tipo="recarga").count(), 1)

    def test_webhook_no_acredita_sin_confirmacion_y_alerta_monto(self):
        orden = self.orden()
        self.assertEqual(self.webhook(orden).status_code, 202)
        self.assertEqual(Recarga.objects.count(), 0)
        CobroSimulado.objects.filter(orden=orden).update(estado="pagado", monto=999)
        self.assertEqual(self.webhook(orden).status_code, 202)
        self.assertTrue(Alerta.objects.filter(tipo="pago inconsistente").exists())

    def test_reconsulta_recupera_webhook_perdido(self):
        orden = self.orden()
        CobroSimulado.objects.filter(orden=orden).update(estado="pagado", pagado_en=timezone.now())
        self.assertEqual(revisar_orden(orden).estado, "acreditada")
        self.assertEqual(Recarga.objects.count(), 1)

    def test_comando_revisa_pago_sin_webhook(self):
        orden = self.orden()
        OrdenRecarga.objects.filter(pk=orden.pk).update(creada=timezone.now() - timedelta(minutes=3))
        CobroSimulado.objects.filter(orden=orden).update(estado="pagado", pagado_en=timezone.now())
        call_command("revisar_ordenes", stdout=io.StringIO())
        self.assertEqual(OrdenRecarga.objects.get(pk=orden.pk).estado, "acreditada")

    def test_reembolso_bloqueado_antes_y_despues_de_distribucion_offline(self):
        orden = self.orden()
        self.pagar(orden)
        orden.refresh_from_db()
        with self.assertRaisesRegex(ErrorPago, "no se puede garantizar la cancelación"):
            reembolsar(orden, self.operador, "solicitud del cliente")

        validador = Validador.objects.create(
            numero=101, usuario=get_user_model().objects.create_user(username="validador-m11"))
        distribuida_v1 = transporte.sincronizar(validador, [])["recargas"]
        distribuida_v2 = [e for paquete in listas_para(0) for e in paquete.entradas]
        self.assertTrue(any(r["cuenta_id"] == self.cuenta.pk and r["seq"] == orden.recarga.seq
                            for r in distribuida_v1))
        self.assertTrue(any(e.tipo == RECARGA and e.op == PENDIENTE and e.a == self.cuenta.pk
                            and e.b == orden.recarga.seq for e in distribuida_v2))
        with self.assertRaisesRegex(ErrorPago, "no se puede garantizar la cancelación"):
            reembolsar(orden, self.operador, "solicitud del cliente")

        orden.refresh_from_db()
        self.cuenta.refresh_from_db()
        self.assertEqual(self.cuenta.saldo, saldo_cliente(self.cuenta))
        self.assertEqual(self.cuenta.saldo, orden.monto)
        self.assertEqual(orden.estado, "acreditada")
        self.assertIsNone(orden.recarga.reembolsada_en)
        self.assertEqual(CobroSimulado.objects.get(orden=orden).estado, "pagado")
        self.assertEqual(transporte.recargas_para_tarjeta(self.cuenta.pk, 0), (orden.monto, 1))
        self.assertFalse(Asiento.objects.filter(tipo="reverso").exists())
        self.assertFalse(Movimiento.objects.filter(tipo="reverso").exists())

        Recarga.objects.filter(pk=orden.recarga_id).update(aplicada_en=timezone.now())
        with self.assertRaisesRegex(ErrorPago, "requiere ajuste manual"):
            reembolsar(orden, self.operador, "tarde")

    def test_api_sesion_y_propiedad(self):
        cliente = Client()
        self.assertEqual(cliente.get("/api/v1/recargas/ordenes/").status_code, 401)
        cliente.force_login(self.usuario)
        clave = str(uuid.uuid4())
        r = cliente.post("/api/v1/recargas/ordenes/", data=json.dumps({"monto": 500}),
                         content_type="application/json", HTTP_IDEMPOTENCY_KEY=clave)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(cliente.get(f"/api/v1/recargas/ordenes/{r.json()['id']}/").status_code, 200)
        r2 = cliente.post("/api/v1/recargas/ordenes/", data=json.dumps({"monto": 600}),
                          content_type="application/json", HTTP_IDEMPOTENCY_KEY=clave)
        self.assertEqual(r2.status_code, 409)
        otro = Client()
        otro.force_login(self.operador)
        self.assertEqual(otro.get(f"/api/v1/recargas/ordenes/{r.json()['id']}/").status_code, 403)

    def test_respuestas_api_contra_contrato_c4(self):
        # Campos obligatorios de OrdenCreada, Orden y Error en docs/contratos/api-cliente-v1.yaml
        cliente = Client()
        cliente.force_login(self.usuario)
        creada = cliente.post("/api/v1/recargas/ordenes/", data=json.dumps({"monto": 500}),
                              content_type="application/json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
        self.assertEqual(creada.status_code, 201)
        detalle = cliente.get(f"/api/v1/recargas/ordenes/{creada.json()['id']}/")
        error = cliente.get(f"/api/v1/recargas/ordenes/{uuid.uuid4()}/")
        self.assertEqual(error.status_code, 404)
        obligatorios = {
            "OrdenCreada": (creada, {"id", "estado", "monto", "url_pago", "expira"}),
            "Orden": (detalle, {"id", "estado", "monto", "creada", "pagada_en", "acreditada_en"}),
        }
        for nombre, (respuesta, campos) in obligatorios.items():
            datos = respuesta.json()
            self.assertTrue(campos.issubset(datos), f"{nombre}: faltan {campos - set(datos)}")
            self.assertIs(type(datos["monto"]), int)
            self.assertIsInstance(datos["id"], str)
            self.assertIsInstance(datos["estado"], str)
        self.assertTrue({"codigo", "mensaje", "campos"}.issubset(error.json()["error"]))

    def test_webhook_orden_inexistente(self):
        orden = self.orden()
        OrdenRecarga.objects.filter(pk=orden.pk).update(id_externo="otro-cobro")
        self.assertEqual(self.webhook(orden).status_code, 200)
        self.assertTrue(Alerta.objects.filter(tipo="pago inconsistente").exists())

    def test_expiracion_y_reconsulta(self):
        orden = self.orden()
        CobroSimulado.objects.filter(orden=orden).update(estado="fallido")
        self.assertEqual(revisar_orden(orden).estado, "fallida")
        self.assertEqual(Recarga.objects.count(), 0)
        otra = self.orden()
        CobroSimulado.objects.filter(orden=otra).update(expira=timezone.now() - timedelta(minutes=1))
        self.assertEqual(revisar_orden(otra).estado, "expirada")

    def test_conciliacion_diferencias(self):
        dia = timezone.localdate()
        orden = self.orden()
        CobroSimulado.objects.filter(orden=orden).update(estado="pagado", pagado_en=timezone.now())
        with self.assertRaises(CommandError):
            call_command("conciliar_pagos", dia=str(dia), stdout=io.StringIO(), stderr=io.StringIO())
        self.assertTrue(Alerta.objects.filter(detalle__contains="sin acreditación").exists())
        confirmar_cobro(orden)
        CobroSimulado.objects.filter(orden=orden).update(monto=999)
        with self.assertRaises(CommandError):
            call_command("conciliar_pagos", dia=str(dia), stdout=io.StringIO(), stderr=io.StringIO())
        self.assertTrue(Alerta.objects.filter(detalle__contains="montos distintos").exists())
        CobroSimulado.objects.filter(orden=orden).update(pagado_en=timezone.now() - timedelta(days=1))
        with self.assertRaises(CommandError):
            call_command("conciliar_pagos", dia=str(dia), stdout=io.StringIO(), stderr=io.StringIO())
        self.assertTrue(Alerta.objects.filter(detalle__contains="sin transacción").exists())

    def test_simulador_acredita_solo_por_webhook(self):
        orden = self.orden()
        cliente = Client()
        ruta = f"/pagos/simulador/{orden.id_externo}/"
        self.assertContains(cliente.get(ruta), "NO ES UN PAGO REAL")
        with self.captureOnCommitCallbacks(execute=True):
            respuesta = cliente.post(ruta, {"decision": "aprobar", "webhook": "doble"})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(Recarga.objects.count(), 1)


@override_settings(PAGOS_SECRETO_WEBHOOK="secreto-de-pruebas")
class AcreditacionConcurrente(TransactionTestCase):
    def test_dos_hilos_una_recarga(self):
        CuentaContable.objects.get_or_create(codigo="CAJA_PASARELA", defaults={
            "nombre": "Caja de pasarela", "naturaleza": CuentaContable.Naturaleza.ACTIVO})
        tarifa = Tarifa.objects.create(codigo=56, nombre="Concurrencia", precio=100)
        cuenta = Cuenta.objects.create(nombre="Concurrencia", documento="M11-concurrente", tarifa=tarifa)
        usuario = get_user_model().objects.create_user(username="concurrente")
        orden = crear_orden(usuario, cuenta, 1000, uuid.uuid4())
        CobroSimulado.objects.filter(orden=orden).update(estado="pagado", pagado_en=timezone.now())
        errores = []
        inicio = threading.Barrier(2)

        def ejecutar():
            close_old_connections()
            try:
                inicio.wait(timeout=5)
                confirmar_cobro(orden)
            except Exception as exc:
                errores.append(exc)
            finally:
                close_old_connections()

        hilos = [threading.Thread(target=ejecutar) for _ in range(2)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=10)
        self.assertFalse(errores, errores)
        self.assertEqual(Recarga.objects.count(), 1)
        self.assertEqual(Cuenta.objects.get(pk=cuenta.pk).saldo, 1000)
        self.assertEqual(Asiento.objects.filter(tipo="recarga").count(), 1)
