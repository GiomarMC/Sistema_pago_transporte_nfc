from datetime import timedelta
import re
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django.contrib.auth.tokens import PasswordResetTokenGenerator

from notificaciones.models import Notificacion

from pagos.models import ClienteCuenta
from transporte.models import Cuenta, Tarifa, Tarjeta

from .models import Cliente, CodigoActivacion, CodigoOTP, Dispositivo, IntentoAcceso, numero_impreso


@override_settings(ALLOWED_HOSTS=["mi.localhost", "testserver"],
                   DOMINIO_CLIENTE="mi.localhost")
class IdentidadAPITests(TestCase):
    host = {"HTTP_HOST": "mi.localhost"}

    def setUp(self):
        tarifa = Tarifa.objects.get_or_create(pk=1, defaults={
            "nombre": "general", "precio": 130})[0]
        self.cuenta = Cuenta.objects.create(
            nombre="Ana Quispe", documento="71234567", tarifa=tarifa)
        self.tarjeta = Tarjeta.objects.create(
            uid="M12-ACTIVAR", cuenta=self.cuenta, estado=Tarjeta.Estado.ACTIVA)
        self.codigo = CodigoActivacion(tarjeta=self.tarjeta)
        self.codigo.asignar_codigo("ABCD1234")
        self.codigo.save()

    def post(self, ruta, datos, cliente=None):
        return (cliente or self.client).post(
            "/api/v1/" + ruta, datos, content_type="application/json", **self.host)

    def token(self):
        respuesta = self.post("activacion/", {
            "documento": self.cuenta.documento,
            "numero_tarjeta": numero_impreso(self.tarjeta).replace(" ", ""),
            "codigo": "ABCD1234"})
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return respuesta.json()["token_activacion"]

    def activar(self):
        token = self.token()
        with patch("clientes.servicios.secrets.randbelow", return_value=123456):
            respuesta = self.post("activacion/correo/", {
                "token_activacion": token, "correo": "ana@example.test"})
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        respuesta = self.post("activacion/confirmar/", {
            "token_activacion": token, "otp": "123456",
            "contrasena": "ClaveSegura2026"})
        self.assertEqual(respuesta.status_code, 201, respuesta.content)
        return token

    def test_activacion_usa_codigo_y_otp_una_vez_sin_guardar_claro(self):
        token = self.activar()
        self.codigo.refresh_from_db()
        self.assertIsNotNone(self.codigo.usado_en)
        cliente = Cliente.objects.get(cuenta=self.cuenta)
        self.cuenta.refresh_from_db()
        self.assertEqual(cliente.usuario.username, "cli-71234567")
        self.assertTrue(cliente.correo_verificado)
        self.assertEqual(self.cuenta.correo, "ana@example.test")
        self.assertIsNotNone(self.cuenta.correo_verificado_en)
        self.assertTrue(ClienteCuenta.objects.filter(usuario=cliente.usuario,
                                                    cuenta=self.cuenta).exists())
        otp = CodigoOTP.objects.get(proposito="activacion")
        self.assertTrue(otp.usado)
        self.assertNotIn("123456", otp.hash)
        self.assertNotIn("ABCD1234", self.codigo.hash)
        self.assertIn("sp_disp", self.client.cookies)
        self.assertEqual(self.client.get("/api/v1/sesion/", **self.host).json(),
                         {"activa": True})
        self.assertEqual(self.post("activacion/confirmar/", {
            "token_activacion": token, "otp": "123456",
            "contrasena": "ClaveSegura2026"}).status_code, 400)

    def test_activacion_fallos_bloqueo_y_codigo_vencido(self):
        datos = {"documento": self.cuenta.documento,
                 "numero_tarjeta": numero_impreso(self.tarjeta).replace(" ", ""),
                 "codigo": "INVALIDO"}
        for _ in range(4):
            self.assertEqual(self.post("activacion/", datos).status_code, 400)
        quinto = self.post("activacion/", datos)
        self.assertEqual(quinto.status_code, 429)
        self.assertEqual(quinto.json()["error"]["codigo"], "bloqueo_temporal")
        self.codigo.expira = timezone.now() - timedelta(seconds=1)
        self.codigo.save(update_fields=["expira"])
        self.assertEqual(self.post("activacion/", {**datos, "codigo": "ABCD1234"}).status_code, 429)

    def test_otp_de_activacion_no_se_reenvia_antes_de_un_minuto(self):
        token = self.token()
        datos = {"token_activacion": token, "correo": "ana@example.test"}
        self.assertEqual(self.post("activacion/correo/", datos).status_code, 200)
        cantidad = Notificacion.objects.filter(tipo="otp").count()
        self.assertEqual(self.post("activacion/correo/", datos).status_code, 429)
        self.assertEqual(Notificacion.objects.filter(tipo="otp").count(), cantidad)

    def test_sesion_nueva_exige_otp_y_dispositivo_recordado_lo_omite(self):
        self.activar()
        nuevo = Client()
        with patch("clientes.servicios.secrets.randbelow", return_value=654321):
            inicio = self.post("sesion/", {
                "documento": "71234567", "contrasena": "ClaveSegura2026"}, cliente=nuevo)
        self.assertEqual(inicio.status_code, 200, inicio.content)
        self.assertEqual(inicio.json(), {"requiere_otp": True})
        self.assertEqual(nuevo.get("/api/v1/resumen/", **self.host).status_code, 401)
        self.assertEqual(self.post("sesion/otp/", {
            "otp": "000000", "recordar_dispositivo": True}, cliente=nuevo).status_code, 400)
        confirmado = self.post("sesion/otp/", {
            "otp": "654321", "recordar_dispositivo": True}, cliente=nuevo)
        self.assertEqual(confirmado.status_code, 200, confirmado.content)
        self.assertIn("sp_disp", nuevo.cookies)
        self.assertEqual(nuevo.get("/api/v1/resumen/", **self.host).status_code, 200)
        self.assertEqual(nuevo.delete("/api/v1/sesion/", **self.host).status_code, 204)
        repetido = self.post("sesion/", {
            "documento": "71234567", "contrasena": "ClaveSegura2026"}, cliente=nuevo)
        self.assertEqual(repetido.json(), {"requiere_otp": False})

    def test_quinto_fallo_de_clave_bloquea_y_otp_se_anula_en_cinco(self):
        self.activar()
        nuevo = Client()
        for i in range(5):
            respuesta = self.post("sesion/", {
                "documento": "71234567", "contrasena": "incorrecta"}, cliente=nuevo)
            self.assertEqual(respuesta.status_code, 429 if i == 4 else 401)
        self.assertEqual(IntentoAcceso.objects.filter(motivo="clave", exito=False).count(), 5)
        IntentoAcceso.objects.filter(motivo="clave").delete()
        with patch("clientes.servicios.secrets.randbelow", return_value=654321):
            self.post("sesion/", {"documento": "71234567",
                                      "contrasena": "ClaveSegura2026"}, cliente=nuevo)
        for _ in range(5):
            self.assertEqual(self.post("sesion/otp/", {"otp": "000000"}, cliente=nuevo).status_code, 400)
        self.assertTrue(CodigoOTP.objects.get(proposito="inicio").usado)
        self.assertEqual(self.post("sesion/otp/", {"otp": "654321"}, cliente=nuevo).status_code, 400)

    def test_csrf_protege_activacion_anonima(self):
        navegador = Client(enforce_csrf_checks=True)
        datos = {"documento": self.cuenta.documento,
                 "numero_tarjeta": numero_impreso(self.tarjeta).replace(" ", ""),
                 "codigo": "ABCD1234"}
        self.assertEqual(self.post("activacion/", datos, cliente=navegador).status_code, 403)
        pagina = navegador.get("/activar/", **self.host)
        token_csrf = pagina.cookies["csrftoken"].value
        respuesta = navegador.post("/api/v1/activacion/", datos,
                                  content_type="application/json", HTTP_X_CSRFTOKEN=token_csrf,
                                  **self.host)
        self.assertEqual(respuesta.status_code, 200)

    def test_csrf_protege_ingreso_anonimo(self):
        navegador = Client(enforce_csrf_checks=True)
        self.assertEqual(self.post("sesion/", {
            "documento": "71234567", "contrasena": "ClaveSegura2026"
        }, cliente=navegador).status_code, 403)

    def test_recuperacion_no_revela_cuenta_y_token_sirve_una_vez(self):
        self.activar()
        inicial = Notificacion.objects.count()
        self.assertEqual(self.post("contrasena/recuperar/", {
            "documento": "00000000"}).status_code, 202)
        self.assertEqual(Notificacion.objects.count(), inicial)
        respuesta = self.post("contrasena/recuperar/", {"documento": "71234567"})
        self.assertEqual(respuesta.status_code, 202)
        self.assertEqual(self.post("contrasena/recuperar/", {
            "documento": "71234567"}).status_code, 202)
        self.assertEqual(Notificacion.objects.filter(tipo="recuperar_contrasena").count(), 1)
        aviso = Notificacion.objects.filter(tipo="recuperar_contrasena").latest("pk")
        url = re.search(r"https?://\S+", aviso.texto).group()
        self.assertEqual(urlsplit(url).path, "/restablecer/")
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        restablecida = self.post("contrasena/restablecer/", {
            **query, "contrasena": "OtraClave2026"})
        self.assertEqual(restablecida.status_code, 200, restablecida.content)
        self.assertEqual(self.post("contrasena/restablecer/", {
            **query, "contrasena": "TerceraClave2026"}).status_code, 400)
        self.assertEqual(self.client.get("/api/v1/sesion/", **self.host).status_code, 401)
        usuario = get_user_model().objects.get(username="cli-71234567")
        self.assertTrue(usuario.check_password("OtraClave2026"))

    def test_cambio_de_clave_conserva_sesion_actual_y_cierra_otra(self):
        self.activar()
        usuario = get_user_model().objects.get(username="cli-71234567")
        otro = Client()
        otro.force_login(usuario)
        self.assertEqual(self.post("contrasena/cambiar/", {
            "actual": "incorrecta", "nueva": "OtraClave2026"}).status_code, 401)
        self.assertEqual(self.post("contrasena/cambiar/", {
            "actual": "ClaveSegura2026", "nueva": "OtraClave2026"}).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/sesion/", **self.host).status_code, 200)
        self.assertEqual(otro.get("/api/v1/sesion/", **self.host).status_code, 401)

    def test_token_de_recuperacion_vencido(self):
        self.activar()
        usuario = get_user_model().objects.get(username="cli-71234567")
        generador = PasswordResetTokenGenerator()
        token = generador.make_token(usuario)
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        uid = urlsafe_base64_encode(force_bytes(usuario.pk))
        with override_settings(PASSWORD_RESET_TIMEOUT=1), patch.object(
                generador, "_now", return_value=generador._now() + timedelta(seconds=3)):
            with patch("clientes.api_contrasena.generador", generador):
                self.assertEqual(self.post("contrasena/restablecer/", {
                    "uid": uid, "token": token, "contrasena": "OtraClave2026"}).status_code, 400)

    def test_restauracion_rechaza_uid_malformado_sin_error_interno(self):
        self.assertEqual(self.post("contrasena/restablecer/", {
            "uid": "a", "token": "invalido", "contrasena": "OtraClave2026"}).status_code, 400)

    def test_seguridad_muestra_accesos_y_permite_revocar_dispositivo(self):
        self.activar()
        accesos = self.client.get("/api/v1/seguridad/accesos/", **self.host)
        self.assertEqual(accesos.status_code, 200)
        self.assertIsInstance(accesos.json(), list)
        dispositivos = self.client.get("/api/v1/seguridad/dispositivos/", **self.host)
        self.assertEqual(dispositivos.status_code, 200)
        self.assertTrue(dispositivos.json()[0]["actual"])
        identificador = dispositivos.json()[0]["id"]
        self.assertEqual(self.client.delete(
            f"/api/v1/seguridad/dispositivos/{identificador}/", **self.host).status_code, 204)
        self.assertFalse(Dispositivo.objects.filter(pk=identificador).exists())
        self.assertEqual(self.client.get(
            "/api/v1/seguridad/dispositivos/", **self.host).json(), [])

    def test_cambio_de_correo_requiere_otp_en_nuevo_correo(self):
        self.activar()
        with patch("clientes.servicios.secrets.randbelow", return_value=987654):
            self.assertEqual(self.post("perfil/correo/", {
                "correo": "nuevo@example.test"}).status_code, 200)
        self.assertEqual(Cliente.objects.get(cuenta=self.cuenta).correo, "ana@example.test")
        otp = CodigoOTP.objects.get(proposito=CodigoOTP.Proposito.CORREO)
        self.assertEqual(otp.correo_destino, "nuevo@example.test")
        self.assertEqual(Notificacion.objects.filter(tipo="otp").latest("pk").destinatario,
                         "nuevo@example.test")
        self.assertEqual(self.post("perfil/correo/", {
            "correo": "otro@example.test"}).status_code, 429)
        self.assertEqual(self.post("perfil/correo/confirmar/", {"otp": "000000"}).status_code, 400)
        self.assertEqual(self.post("perfil/correo/confirmar/", {"otp": "987654"}).status_code, 200)
        cliente = Cliente.objects.get(cuenta=self.cuenta)
        self.cuenta.refresh_from_db()
        self.assertEqual(cliente.correo, "nuevo@example.test")
        self.assertEqual(cliente.usuario.email, "nuevo@example.test")
        self.assertEqual(self.cuenta.correo, "nuevo@example.test")
        self.assertTrue(CodigoOTP.objects.get(pk=otp.pk).usado)
        self.assertEqual(self.post("perfil/correo/confirmar/", {"otp": "987654"}).status_code, 400)

    def test_seguridad_solo_expone_datos_del_titular(self):
        self.assertEqual(self.client.get("/api/v1/seguridad/accesos/", **self.host).status_code, 401)
        self.assertEqual(self.client.get("/api/v1/seguridad/dispositivos/", **self.host).status_code, 401)
        self.assertEqual(self.post("perfil/correo/", {"correo": "nuevo@example.test"}).status_code, 401)

    def test_inicio_repetido_no_reenvia_otp_antes_de_un_minuto(self):
        self.activar()
        nuevo = Client()
        datos = {"documento": "71234567", "contrasena": "ClaveSegura2026"}
        self.assertEqual(self.post("sesion/", datos, cliente=nuevo).status_code, 200)
        cantidad = Notificacion.objects.filter(tipo="otp").count()
        self.assertEqual(self.post("sesion/", datos, cliente=nuevo).status_code, 200)
        self.assertEqual(Notificacion.objects.filter(tipo="otp").count(), cantidad)

    def test_sesion_se_renueva_por_actividad(self):
        self.activar()
        sesion = self.client.session
        sesion.set_expiry(30)
        sesion.save()
        self.assertLessEqual(self.client.session.get_expiry_age(), 30)
        self.assertEqual(self.client.get("/api/v1/sesion/", **self.host).status_code, 200)
        self.assertGreaterEqual(self.client.session.get_expiry_age(), 890)

    # --- #105 (M12-03): criterios de la activación que faltaban -------------------------

    def datos_activacion(self, **cambios):
        return {"documento": self.cuenta.documento,
                "numero_tarjeta": numero_impreso(self.tarjeta).replace(" ", ""),
                "codigo": "ABCD1234", **cambios}

    def test_activacion_rechaza_numero_con_luhn_invalido(self):
        correcto = numero_impreso(self.tarjeta).replace(" ", "")
        # Mismo id de tarjeta (8 primeros dígitos), dígito de control cambiado
        alterado = correcto[:8] + str((int(correcto[8]) + 1) % 10)
        respuesta = self.post("activacion/", self.datos_activacion(numero_tarjeta=alterado))
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()["error"]["codigo"], "activacion_invalida")
        self.assertNotIn("token_activacion", respuesta.json())
        # El número correcto sí pasa: el rechazo vino del dígito de control
        self.assertEqual(self.post("activacion/", self.datos_activacion()).status_code, 200)

    def test_activacion_rechaza_codigo_vencido_sin_estar_bloqueada(self):
        self.codigo.expira = timezone.now() - timedelta(seconds=1)
        self.codigo.save(update_fields=["expira"])
        respuesta = self.post("activacion/", self.datos_activacion())
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()["error"]["codigo"], "activacion_invalida")

    def test_activacion_rechaza_codigo_ya_usado(self):
        self.activar()
        respuesta = self.post("activacion/", self.datos_activacion(), cliente=Client())
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()["error"]["codigo"], "activacion_invalida")

    def test_activacion_rechaza_cuenta_que_ya_tiene_cliente(self):
        usuario = get_user_model().objects.create_user(username="cli-71234567", password="x")
        Cliente.objects.create(usuario=usuario, cuenta=self.cuenta,
                               correo="ana@example.test", correo_verificado=True)
        respuesta = self.post("activacion/", self.datos_activacion())
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(respuesta.json()["error"]["codigo"], "activacion_invalida")
        self.assertEqual(Cliente.objects.filter(cuenta=self.cuenta).count(), 1)

    def test_token_obtenido_antes_de_que_la_cuenta_tenga_cliente_ya_no_sirve(self):
        token = self.token()
        usuario = get_user_model().objects.create_user(username="cli-71234567", password="x")
        Cliente.objects.create(usuario=usuario, cuenta=self.cuenta,
                               correo="otra@example.test", correo_verificado=True)
        correo = self.post("activacion/correo/", {
            "token_activacion": token, "correo": "ana@example.test"})
        self.assertEqual(correo.status_code, 400)
        self.assertEqual(correo.json()["error"]["codigo"], "activacion_invalida")
        confirmar = self.post("activacion/confirmar/", {
            "token_activacion": token, "otp": "123456", "contrasena": "ClaveSegura2026"})
        self.assertEqual(confirmar.status_code, 400)
        self.assertEqual(Cliente.objects.filter(cuenta=self.cuenta).count(), 1)

    # --- #107 (M12-04): límite por IP y recordar_dispositivo ----------------------------

    def fallar_inicio_desde(self, cliente, veces, **extra):
        """Fallos con documentos distintos: ninguno llega a los 5 por documento."""
        estados = []
        for i in range(veces):
            estados.append(cliente.post("/api/v1/sesion/", {
                "documento": f"{10000000 + i}", "contrasena": "incorrecta"},
                content_type="application/json", **self.host, **extra).status_code)
        return estados

    def test_veinte_fallos_desde_una_ip_bloquean_esa_ip_y_no_las_demas(self):
        self.activar()
        atacante = Client(REMOTE_ADDR="203.0.113.7")
        estados = self.fallar_inicio_desde(atacante, 20)
        self.assertEqual(estados, [401] * 19 + [429])
        self.assertEqual(IntentoAcceso.objects.filter(
            ip="203.0.113.7", motivo="clave", exito=False).count(), 20)
        # Desde esa IP ni la contraseña correcta entra mientras dure el bloqueo
        bloqueado = self.post("sesion/", {"documento": "71234567",
                                          "contrasena": "ClaveSegura2026"}, cliente=atacante)
        self.assertEqual(bloqueado.status_code, 429)
        self.assertEqual(bloqueado.json()["error"]["codigo"], "bloqueo_temporal")
        # Otra IP no se ve afectada
        otra = self.post("sesion/", {"documento": "71234567", "contrasena": "ClaveSegura2026"},
                         cliente=Client(REMOTE_ADDR="198.51.100.9"))
        self.assertEqual(otra.status_code, 200, otra.content)
        # Pasados los 15 minutos, la IP vuelve a poder intentar
        IntentoAcceso.objects.filter(ip="203.0.113.7").update(
            fecha=timezone.now() - timedelta(minutes=16))
        self.assertEqual(self.post("sesion/", {"documento": "71234567",
                                               "contrasena": "ClaveSegura2026"},
                                   cliente=atacante).status_code, 200)

    @override_settings(IP_DESDE_PROXY=True)
    def test_detras_de_caddy_el_limite_usa_la_ip_reenviada(self):
        self.activar()
        # Todas las peticiones llegan desde el contenedor de Caddy (misma REMOTE_ADDR)
        caddy = {"REMOTE_ADDR": "172.18.0.3"}
        atacante = Client(**caddy, HTTP_X_FORWARDED_FOR="203.0.113.7")
        self.assertEqual(self.fallar_inicio_desde(atacante, 20)[-1], 429)
        self.assertTrue(IntentoAcceso.objects.filter(ip="203.0.113.7").exists())
        self.assertFalse(IntentoAcceso.objects.filter(ip="172.18.0.3").exists())
        # Otro cliente detrás del mismo Caddy sigue pudiendo entrar
        otro = Client(**caddy, HTTP_X_FORWARDED_FOR="198.51.100.9")
        respuesta = self.post("sesion/", {"documento": "71234567",
                                          "contrasena": "ClaveSegura2026"}, cliente=otro)
        self.assertEqual(respuesta.status_code, 200, respuesta.content)

    def test_sin_caddy_delante_se_ignora_la_ip_reenviada(self):
        # Sin DJANGO_HTTPS=1 la cabecera la pondría el propio cliente: cambiarla no evita el bloqueo
        self.activar()
        for i in range(20):
            Client(REMOTE_ADDR="203.0.113.7", HTTP_X_FORWARDED_FOR=f"192.0.2.{i}").post(
                "/api/v1/sesion/", {"documento": f"{10000000 + i}", "contrasena": "incorrecta"},
                content_type="application/json", **self.host)
        self.assertEqual(IntentoAcceso.objects.filter(ip="203.0.113.7").count(), 20)
        respuesta = self.post("sesion/", {"documento": "71234567", "contrasena": "ClaveSegura2026"},
                              cliente=Client(REMOTE_ADDR="203.0.113.7",
                                             HTTP_X_FORWARDED_FOR="192.0.2.200"))
        self.assertEqual(respuesta.status_code, 429)

    def iniciar_con_otp(self, cliente, recordar):
        with patch("clientes.servicios.secrets.randbelow", return_value=654321):
            inicio = self.post("sesion/", {"documento": "71234567",
                                           "contrasena": "ClaveSegura2026"}, cliente=cliente)
        self.assertEqual(inicio.json(), {"requiere_otp": True})
        confirmado = self.post("sesion/otp/", {"otp": "654321",
                                               "recordar_dispositivo": recordar}, cliente=cliente)
        self.assertEqual(confirmado.status_code, 200, confirmado.content)
        return confirmado

    def test_sin_recordar_dispositivo_el_siguiente_inicio_vuelve_a_pedir_otp(self):
        self.activar()
        cliente = Client()
        antes = Dispositivo.objects.count()
        confirmado = self.iniciar_con_otp(cliente, recordar=False)
        self.assertNotIn("sp_disp", confirmado.cookies)
        self.assertEqual(Dispositivo.objects.count(), antes)
        self.assertEqual(cliente.delete("/api/v1/sesion/", **self.host).status_code, 204)
        otra_vez = self.post("sesion/", {"documento": "71234567",
                                         "contrasena": "ClaveSegura2026"}, cliente=cliente)
        self.assertEqual(otra_vez.json(), {"requiere_otp": True})

    def test_recordar_dispositivo_lo_hace_confiable_90_dias_y_luego_vuelve_el_otp(self):
        self.activar()
        cliente = Client()
        antes = set(Dispositivo.objects.values_list("pk", flat=True))
        confirmado = self.iniciar_con_otp(cliente, recordar=True)
        cookie = confirmado.cookies["sp_disp"]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        nuevo = Dispositivo.objects.exclude(pk__in=antes).get()
        self.assertAlmostEqual((nuevo.confiable_hasta - timezone.now()).total_seconds(),
                               90 * 86400, delta=60)
        self.assertNotIn(nuevo.huella, cookie.value)  # solo se guarda el hash
        cliente.delete("/api/v1/sesion/", **self.host)
        self.assertEqual(self.post("sesion/", {"documento": "71234567",
                                               "contrasena": "ClaveSegura2026"},
                                   cliente=cliente).json(), {"requiere_otp": False})
        # Vencidos los 90 días deja de ser confiable
        cliente.delete("/api/v1/sesion/", **self.host)
        nuevo.confiable_hasta = timezone.now() - timedelta(seconds=1)
        nuevo.save(update_fields=["confiable_hasta"])
        self.assertEqual(self.post("sesion/", {"documento": "71234567",
                                               "contrasena": "ClaveSegura2026"},
                                   cliente=cliente).json(), {"requiere_otp": True})
