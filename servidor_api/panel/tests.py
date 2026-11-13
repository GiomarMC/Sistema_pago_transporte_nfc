from datetime import timedelta
from io import StringIO
import json
import uuid

from django.core.management import call_command
from django.core.management.base import CommandError
from django.core import mail
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from transporte.models import Alerta, Cuenta, Movimiento, Tarifa, Tarjeta, Validador, EstadoSync
from transporte import servicios as transporte_servicios
from libro.libro import abrir_saldo_existente
from pagos.models import ClienteCuenta, CobroSimulado, EventoPasarela, OrdenRecarga
from pagos.servicios import crear_orden, confirmar_cobro
from .models import ConciliacionPago, SolicitudReposicion, VerificacionLibro


class PanelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.operador = User.objects.create_user(username="operadora", password="prueba-segura-123")
        self.operador.groups.add(Group.objects.get(name="operadores"))
        self.ajeno = User.objects.create_user(username="ajeno", password="prueba-segura-123")
        tarifa = Tarifa.objects.get(pk=1)
        self.cuenta = Cuenta.objects.create(nombre="Ana Quispe", documento="71234567", tarifa=tarifa)
        abrir_saldo_existente(self.cuenta, 920)
        self.tarjeta = Tarjeta.objects.create(uid="04AABBCCDDEEFF", cuenta=self.cuenta, estado="activa")

    def viaje(self, numero, cuenta=None, tarjeta=None):
        return Movimiento.objects.create(
            fecha=timezone.now(), cuenta=cuenta or self.cuenta, tarjeta=tarjeta or self.tarjeta,
            tipo="viaje", monto=-80, saldo_final=920, operacion=numero + 1,
            validador=101, ref=f"V101-{numero}",
        )

    def test_acceso_reservado_a_operadores(self):
        self.assertEqual(self.client.get(reverse("panel:inicio")).status_code, 302)
        self.client.force_login(self.ajeno)
        self.assertEqual(self.client.get(reverse("panel:inicio")).status_code, 403)
        self.client.force_login(self.operador)
        self.assertEqual(self.client.get(reverse("panel:inicio")).status_code, 200)

    def test_raiz_lleva_al_panel(self):
        self.assertRedirects(self.client.get("/"), reverse("panel:inicio"), target_status_code=302)

    def test_busqueda_exacta_por_dni_y_uid(self):
        self.client.force_login(self.operador)
        ruta = reverse("panel:cuentas")
        for consulta in ("71234567", "04AABBCCDDEEFF", str(self.cuenta.pk)):
            self.assertContains(self.client.get(ruta, {"q": consulta}), "Ana Quispe")
        self.assertNotContains(self.client.get(ruta, {"q": "7123"}), "Ana Quispe")

    def test_paginas_principales_renderizan(self):
        self.client.force_login(self.operador)
        rutas = [
            reverse("panel:cuenta_detalle", args=[self.cuenta.pk]),
            reverse("panel:validadores"),
            reverse("panel:alertas"),
            reverse("panel:viajes"),
        ]
        for ruta in rutas:
            with self.subTest(ruta=ruta):
                self.assertEqual(self.client.get(ruta).status_code, 200)

    def test_resumen_muestra_datos_reales(self):
        self.viaje(1)
        Alerta.objects.create(tipo="incidencia", detalle="revisar", tarjeta=self.tarjeta)
        usuario_bus = get_user_model().objects.create(username="bus-101")
        Validador.objects.create(numero=101, usuario=usuario_bus,
                                 ultima_sync=timezone.now() - timedelta(minutes=10))
        self.client.force_login(self.operador)
        respuesta = self.client.get(reverse("panel:inicio"))
        self.assertEqual(respuesta.context["viajes_hoy"], 1)
        self.assertEqual(respuesta.context["cobrado_hoy"], 80)
        self.assertEqual(respuesta.context["alertas_abiertas"], 1)
        self.assertEqual(respuesta.context["validadores_atrasados"], 1)

    def test_viajes_paginados_y_filtrados(self):
        for numero in range(31):
            self.viaje(numero)
        self.client.force_login(self.operador)
        ruta = reverse("panel:viajes")
        primera = self.client.get(ruta, {"q": self.cuenta.documento})
        self.assertEqual(len(primera.context["viajes"]), 30)
        self.assertIsNotNone(primera.context["siguiente"])
        segunda = self.client.get(ruta, {"antes": primera.context["siguiente"]})
        self.assertEqual(len(segunda.context["viajes"]), 1)

    def test_bloqueo_requiere_post_y_motivo(self):
        self.client.force_login(self.operador)
        ruta = reverse("panel:bloquear_tarjeta", args=[self.tarjeta.pk])
        self.assertEqual(self.client.get(ruta).status_code, 405)
        self.assertEqual(self.client.post(ruta, {"motivo": ""}).status_code, 400)
        self.assertEqual(self.client.post(ruta, {"motivo": "reporte de pérdida"}).status_code, 302)
        self.tarjeta.refresh_from_db()
        self.assertEqual(self.tarjeta.estado, "bloqueada")
        self.assertTrue(Alerta.objects.filter(tarjeta=self.tarjeta, tipo="bloqueo manual desde el panel").exists())

    def test_revision_de_alerta(self):
        alerta = Alerta.objects.create(tipo="incidencia", detalle="revisar")
        self.client.force_login(self.operador)
        ruta = reverse("panel:revisar_alerta", args=[alerta.pk])
        self.assertEqual(self.client.get(ruta).status_code, 405)
        self.assertEqual(self.client.post(ruta).status_code, 302)
        alerta.refresh_from_db()
        self.assertTrue(alerta.revisada)
        self.assertEqual(alerta.revisada_por, self.operador)
        self.assertIsNotNone(alerta.revisada_en)

    def test_csrf_protege_bloqueo(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.operador)
        ruta = reverse("panel:bloquear_tarjeta", args=[self.tarjeta.pk])
        self.assertEqual(cliente.post(ruta, {"motivo": "pérdida"}).status_code, 403)
        self.tarjeta.refresh_from_db()
        self.assertEqual(self.tarjeta.estado, "activa")


class UsuariosTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(username="admin", password="prueba-segura-123")
        self.operador = User.objects.create_user(username="operadora", password="prueba-segura-123")
        self.operador.groups.add(Group.objects.get(name="operadores"))

    def solicitar(self, usuario="nuevo", **extra):
        datos = {"username": usuario, "first_name": "Luis", "last_name": "Mamani",
                 "email": "", "password1": "Volcan-Misti-27", "password2": "Volcan-Misti-27"}
        datos.update(extra)
        return Client().post(reverse("panel:solicitar_acceso"), datos)

    def test_registro_queda_pendiente_sin_acceso(self):
        cliente = Client()
        respuesta = cliente.post(reverse("panel:solicitar_acceso"), {
            "username": "nuevo", "first_name": "Luis", "last_name": "Mamani",
            "password1": "Volcan-Misti-27", "password2": "Volcan-Misti-27"})
        self.assertRedirects(respuesta, reverse("panel:inicio"), target_status_code=403)
        nuevo = get_user_model().objects.get(username="nuevo")
        self.assertTrue(nuevo.groups.filter(name="solicitudes").exists())
        self.assertFalse(nuevo.groups.filter(name="operadores").exists())
        pagina = cliente.get(reverse("panel:viajes"))
        self.assertEqual(pagina.status_code, 403)
        self.assertContains(pagina, "esperando la aprobación", status_code=403)

    def test_registro_valida_datos(self):
        self.assertEqual(self.solicitar(password2="otra-cosa-99").status_code, 200)
        self.assertEqual(self.solicitar(password1="12345678", password2="12345678").status_code, 200)
        self.assertEqual(self.solicitar(first_name="").status_code, 200)
        self.assertEqual(self.solicitar(usuario="OPERADORA").status_code, 200)  # ya existe
        self.assertFalse(get_user_model().objects.filter(groups__name="solicitudes").exists())

    def test_registro_se_cierra_con_muchas_pendientes(self):
        grupo = Group.objects.get(name="solicitudes")
        for n in range(50):
            get_user_model().objects.create_user(username=f"spam{n}").groups.add(grupo)
        respuesta = self.solicitar()
        self.assertContains(respuesta, "demasiadas solicitudes")
        self.assertFalse(get_user_model().objects.filter(username="nuevo").exists())

    def test_usuarios_solo_para_superusuarios(self):
        ruta = reverse("panel:usuarios")
        self.client.force_login(self.operador)
        self.assertEqual(self.client.get(ruta).status_code, 403)
        self.assertNotContains(self.client.get(reverse("panel:inicio")), ruta)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("panel:inicio")), ruta)
        self.assertEqual(self.client.get(ruta).status_code, 200)

    def test_aprobar_y_rechazar(self):
        self.solicitar("luis")
        self.solicitar("intruso")
        User = get_user_model()
        luis, intruso = User.objects.get(username="luis"), User.objects.get(username="intruso")
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("panel:usuarios")), "intruso")
        self.client.post(reverse("panel:gestionar_usuario", args=[luis.pk]), {"accion": "aprobar"})
        self.client.post(reverse("panel:gestionar_usuario", args=[intruso.pk]), {"accion": "rechazar"})
        self.assertFalse(User.objects.filter(username="intruso").exists())
        self.assertTrue(luis.groups.filter(name="operadores").exists())
        self.assertFalse(luis.groups.filter(name="solicitudes").exists())
        cliente = Client()
        cliente.force_login(luis)
        self.assertEqual(cliente.get(reverse("panel:inicio")).status_code, 200)
        # Un operador ya aprobado no se puede "rechazar" (borrar) con la acción de solicitudes
        self.client.post(reverse("panel:gestionar_usuario", args=[luis.pk]), {"accion": "rechazar"})
        self.assertTrue(User.objects.filter(username="luis").exists())

    def test_desactivar_quita_el_acceso(self):
        ruta = reverse("panel:gestionar_usuario", args=[self.operador.pk])
        self.client.force_login(self.admin)
        self.client.post(ruta, {"accion": "desactivar"})
        self.operador.refresh_from_db()
        self.assertFalse(self.operador.is_active)
        self.assertFalse(Client().login(username="operadora", password="prueba-segura-123"))
        self.client.post(ruta, {"accion": "reactivar"})
        self.operador.refresh_from_db()
        self.assertTrue(self.operador.is_active)

    def test_no_se_desactiva_a_administradores(self):
        self.client.force_login(self.admin)
        ruta = reverse("panel:gestionar_usuario", args=[self.admin.pk])
        self.assertEqual(self.client.post(ruta, {"accion": "desactivar"}).status_code, 400)
        self.assertEqual(self.client.get(ruta).status_code, 405)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)


class M19Tests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.operador = User.objects.create_user(username="m19-operador", password="prueba")
        self.operador.groups.add(Group.objects.get(name="operadores"))
        self.cliente = User.objects.create_user(
            username="m19-cliente", password="prueba", email="cliente@example.com")
        self.cuenta = Cuenta.objects.create(
            nombre="Cliente M19", documento="M19-123", tarifa=Tarifa.objects.get(pk=1))
        self.vinculo = ClienteCuenta.objects.create(usuario=self.cliente, cuenta=self.cuenta)

    def test_ordenes_filtros_detalle_eventos_y_reembolso_solo_operador(self):
        orden = crear_orden(self.cliente, self.cuenta, 1000, uuid.uuid4())
        CobroSimulado.objects.filter(orden=orden).update(estado="pagado", pagado_en=timezone.now())
        confirmar_cobro(orden)
        orden.refresh_from_db()
        EventoPasarela.objects.create(
            pasarela="simulada", id_evento="m19-evento", id_externo=orden.id_externo,
            tipo="cobro.pagado", cuerpo=b"{}", firma_valida=True)
        ConciliacionPago.objects.create(
            dia=timezone.localdate(), transacciones=1, ordenes=1,
            diferencias=["diferencia de prueba"])
        self.client.force_login(self.operador)
        lista = self.client.get(reverse("panel:ordenes_pago"), {
            "estado": "acreditada", "fecha": timezone.localdate().isoformat(),
            "cuenta": str(self.cuenta.pk)})
        self.assertEqual(lista.status_code, 200)
        self.assertEqual(list(lista.context["pagina"]), [orden])
        self.assertContains(lista, "diferencia de prueba")
        detalle = self.client.get(reverse("panel:orden_pago_detalle", args=[orden.pk]))
        self.assertContains(detalle, "cobro.pagado")
        self.assertContains(detalle, "Reembolsar")
        ruta = reverse("panel:reembolsar_orden", args=[orden.pk])
        self.assertEqual(self.client.post(ruta, {"motivo": ""}).status_code, 400)
        self.client.force_login(self.cliente)
        self.assertEqual(self.client.post(ruta, {"motivo": "solicitud"}).status_code, 403)
        self.client.force_login(self.operador)
        self.assertEqual(self.client.post(ruta, {"motivo": "solicitud"}).status_code, 302)
        orden.refresh_from_db()
        self.assertEqual(orden.estado, "acreditada")  # M11 bloquea reembolsos offline

    def test_hola_autenticado_se_refleja_en_validadores(self):
        bus = get_user_model().objects.create_user(username="m19-bus")
        validador = Validador.objects.create(numero=101, usuario=bus)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=bus).key}")
        datos = {"canal": "wifi", "version_listas": 4, "pendientes": 7,
                 "consumo_dia": 2048, "firmware": "v2.3"}
        self.assertEqual(api.post("/api/validadores/hola/", datos, format="json").status_code, 200)
        ajeno = APIClient()
        ajeno.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=self.operador).key}")
        self.assertEqual(ajeno.post("/api/validadores/hola/", datos, format="json").status_code, 403)
        validador.refresh_from_db()
        self.assertEqual(validador.ultimo_hola, datos)
        self.client.force_login(self.operador)
        respuesta = self.client.get(reverse("panel:validadores"))
        for texto in ("wifi", "v2.3", "2048 bytes", "En línea"):
            self.assertContains(respuesta, texto)
        self.assertEqual(respuesta.context["validadores"][0]["objeto"].pendientes, 7)
        Validador.objects.filter(pk=validador.pk).update(
            ultima_sync=timezone.now() - timedelta(hours=2),
            ultimo_hola_en=timezone.now() - timedelta(hours=2))
        respuesta = self.client.get(reverse("panel:validadores"))
        self.assertContains(respuesta, "fleet-stale")
        transporte_servicios.registrar_conexion(101, True)
        respuesta = self.client.get(reverse("panel:validadores"))
        self.assertContains(respuesta, "En línea")

    def test_verificacion_libro_desfase_aparece_en_rojo(self):
        call_command("verificar_libro", stdout=StringIO())
        self.client.force_login(self.operador)
        self.assertContains(self.client.get(reverse("panel:inicio")), "Correcto")
        Movimiento.objects.create(fecha=timezone.now(), cuenta=self.cuenta,
                                 tipo="recarga", monto=100, saldo_final=100,
                                 ref="sin-asiento-m19")
        with self.assertRaises(CommandError):
            call_command("verificar_libro", stdout=StringIO(), stderr=StringIO())
        self.assertFalse(VerificacionLibro.objects.order_by("-pk").first().correcto)
        respuesta = self.client.get(reverse("panel:inicio"))
        self.assertContains(respuesta, "book-error")
        self.assertContains(respuesta, "sin-asiento-m19")

    def test_conciliacion_ejecutada_queda_visible(self):
        call_command("conciliar_pagos", dia=timezone.localdate().isoformat(),
                     stdout=StringIO(), stderr=StringIO())
        self.assertEqual(ConciliacionPago.objects.count(), 1)
        self.client.force_login(self.operador)
        self.assertContains(self.client.get(reverse("panel:ordenes_pago")), "0 diferencias")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_reposicion_cambia_en_portal_y_notifica_cliente(self):
        tarjeta = Tarjeta.objects.create(uid="M19-BLOQUEADA", cuenta=self.cuenta,
                                         estado=Tarjeta.Estado.BLOQUEADA)
        self.client.force_login(self.cliente)
        respuesta = self.client.post("/api/v1/reposiciones/",
            data=json.dumps({"tarjeta_id": tarjeta.pk}), content_type="application/json")
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        solicitud = SolicitudReposicion.objects.get(pk=respuesta.json()["id"])
        self.assertEqual(self.client.get("/api/v1/reposiciones/").json()[0]["estado"], "solicitada")
        self.client.force_login(self.operador)
        ruta = reverse("panel:gestionar_reposicion", args=[solicitud.pk])
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(ruta, {"accion": "lista"}).status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.cliente.email])
        self.client.force_login(self.cliente)
        self.assertContains(self.client.get("/mi/reposiciones/"), "Tu nueva tarjeta está lista para recoger")
        self.assertEqual(self.client.get("/api/v1/reposiciones/").json()[0]["estado"], "lista")
        self.assertEqual(self.client.post(ruta, {"accion": "entregada"}).status_code, 403)
        self.client.force_login(self.operador)
        self.assertEqual(self.client.post(ruta, {"accion": "entregada"}).status_code, 302)
        self.client.force_login(self.cliente)
        self.assertEqual(self.client.get("/api/v1/reposiciones/").json()[0]["estado"], "entregada")
        repetida = self.client.post("/api/v1/reposiciones/",
            data=json.dumps({"tarjeta_id": tarjeta.pk}), content_type="application/json")
        self.assertEqual(repetida.status_code, 200)
        self.assertEqual(SolicitudReposicion.objects.count(), 1)

    def test_reposicion_no_acepta_tarjeta_ajena_ni_duplicados(self):
        propia = Tarjeta.objects.create(uid="M19-PROPIA", cuenta=self.cuenta,
                                        estado=Tarjeta.Estado.BLOQUEADA)
        otra_cuenta = Cuenta.objects.create(
            nombre="Otro", documento="M19-OTRO", tarifa=Tarifa.objects.get(pk=1))
        ajena = Tarjeta.objects.create(uid="M19-AJENA", cuenta=otra_cuenta,
                                       estado=Tarjeta.Estado.BLOQUEADA)
        self.client.force_login(self.cliente)
        ruta = "/api/v1/reposiciones/"
        for tarjeta in (ajena,):
            respuesta = self.client.post(ruta, json.dumps({"tarjeta_id": tarjeta.pk}),
                                         content_type="application/json")
            self.assertEqual(respuesta.status_code, 409)
        primera = self.client.post(ruta, json.dumps({"tarjeta_id": propia.pk}),
                                   content_type="application/json")
        segunda = self.client.post(ruta, json.dumps({"tarjeta_id": propia.pk}),
                                   content_type="application/json")
        self.assertEqual((primera.status_code, segunda.status_code), (201, 200))
        self.assertEqual(SolicitudReposicion.objects.count(), 1)
