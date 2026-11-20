from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.urls import reverse


class AccesoConductorTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(username="admin-m16", password="clave-segura-123")
        self.operador = User.objects.create_user(username="operador-m16", password="clave-segura-123")
        self.operador.groups.add(Group.objects.get(name="operadores"))
        self.conductor = User.objects.create_user(username="conductor-m16", password="clave-segura-123")
        self.conductor.groups.add(Group.objects.get(name="conductores"))

    def test_conductor_usa_login_del_panel_pero_no_entra_al_panel(self):
        url = reverse("conductor:inicio")
        respuesta = self.client.get(url)
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn("/panel/ingresar/?next=%2Fconductor%2F", respuesta.url)
        self.client.force_login(self.conductor)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(reverse("panel:inicio")).status_code, 403)
        # El endpoint /api/v2/paquetes/ (M9) ya existe: el botón de conexión está habilitado
        pagina = self.client.get(url)
        self.assertContains(pagina, "Conectar validador")
        self.assertNotContains(pagina, "todavía no está disponible")

    def test_operador_no_usa_el_rele_ni_la_guia(self):
        self.client.force_login(self.operador)
        for nombre in ("inicio", "sw", "guia"):
            self.assertEqual(self.client.get(reverse(f"conductor:{nombre}")).status_code, 403)

    def test_superusuario_asigna_rol_sin_permisos_de_operador(self):
        self.client.force_login(self.admin)
        url = reverse("panel:gestionar_usuario", args=[self.operador.pk])
        self.assertEqual(self.client.post(url, {"accion": "conductor"}).status_code, 302)
        self.operador.refresh_from_db()
        self.assertFalse(self.operador.is_staff)
        self.assertTrue(self.operador.groups.filter(name="conductores").exists())
        self.assertFalse(self.operador.groups.filter(name="operadores").exists())
        cliente = Client()
        cliente.force_login(self.operador)
        self.assertEqual(cliente.get(reverse("conductor:inicio")).status_code, 200)
        self.assertEqual(cliente.get(reverse("panel:usuarios")).status_code, 403)
        self.assertEqual(cliente.get(reverse("panel:inicio")).status_code, 403)
        self.assertEqual(self.client.post(url, {"accion": "operador"}).status_code, 302)
        self.operador.refresh_from_db()
        self.assertTrue(self.operador.groups.filter(name="operadores").exists())
        self.assertFalse(self.operador.groups.filter(name="conductores").exists())

    def test_operador_no_puede_asignar_roles(self):
        self.client.force_login(self.operador)
        url = reverse("panel:gestionar_usuario", args=[self.conductor.pk])
        self.assertEqual(self.client.post(url, {"accion": "operador"}).status_code, 403)
        self.assertFalse(self.conductor.groups.filter(name="operadores").exists())

    def test_worker_y_guia_solo_para_conductor(self):
        self.client.force_login(self.conductor)
        self.assertEqual(self.client.get(reverse("conductor:sw")).status_code, 200)
        self.assertContains(self.client.get(reverse("conductor:guia")), "Sincronizar el bus")
