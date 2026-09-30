from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from transporte.models import Alerta, Cuenta, Movimiento, Recarga, Tarifa, Tarjeta, Validador


def cliente_con_token(usuario):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=usuario).key}")
    return c


class Base(TestCase):
    def setUp(self):
        User = get_user_model()
        operador = User.objects.create(username="op")
        operador.groups.add(Group.objects.get(name="operadores"))
        self.op = cliente_con_token(operador)
        self.bus = {}
        for n in (101, 102):
            u = User.objects.create(username=f"validador-{n}")
            Validador.objects.create(numero=n, usuario=u)
            self.bus[n] = cliente_con_token(u)

    # Arrange común: cuenta estudiante con una tarjeta activa y S/ 10.00
    def emitir(self, uid="04AABBCCDDEEFF", documento="71234567", saldo=1000):
        r = self.op.post("/api/emisiones/", {"uid": uid, "nombre": "Ana", "documento": documento,
                                             "tarifa": 2}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        id_tarjeta, id_cuenta = r.data["tarjeta"], r.data["cuenta"]["id"]
        self.op.post(f"/api/emisiones/{id_tarjeta}/confirmar/", {"contador": 1}, format="json")
        if saldo:
            self.op.post("/api/recargas/", {"documento": documento, "monto": saldo,
                                            "origen": "emision"}, format="json")
            Recarga.objects.filter(cuenta_id=id_cuenta).update(aplicada_en=timezone.now())
        return Tarjeta.objects.get(pk=id_tarjeta)

    def viaje(self, tarjeta, id_evento, operacion, contador, fecha=None, recarga_hasta=None):
        return {"id": id_evento, "tipo": "viaje", "fecha": (fecha or timezone.now()).isoformat(),
                "tarjeta_id": tarjeta.id, "uid": tarjeta.uid, "monto": 80, "operacion": operacion,
                "contador": contador, "recarga_hasta": recarga_hasta}

    def sync(self, bus, eventos):
        r = self.bus[bus].post("/api/sync/", {"eventos": eventos}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        return r.data


class PermisosTests(Base):
    def test_sin_token_rechaza(self):
        self.assertEqual(APIClient().post("/api/sync/", {"eventos": []}, format="json")
                         .status_code, 401)

    def test_validador_no_puede_recargar(self):
        r = self.bus[101].post("/api/recargas/", {"documento": "1", "monto": 500}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_operador_no_puede_sincronizar(self):
        self.assertEqual(self.op.post("/api/sync/", {"eventos": []}, format="json").status_code, 403)

    def test_validador_desactivado_no_sincroniza(self):
        Validador.objects.filter(numero=101).update(activo=False)
        r = self.bus[101].post("/api/sync/", {"eventos": []}, format="json")
        self.assertEqual(r.status_code, 403)


class SyncTests(Base):
    def test_viaje_descuenta_de_la_cuenta(self):
        t = self.emitir()
        datos = self.sync(101, [self.viaje(t, 1, 2, 5)])
        self.assertEqual(datos["aceptados"], [1])
        self.assertEqual(Cuenta.objects.get().saldo, 920)

    def test_reenvio_es_idempotente(self):
        t = self.emitir()
        ev = [self.viaje(t, 1, 2, 5)]
        self.sync(101, ev)
        datos = self.sync(101, ev)
        self.assertEqual(datos["aceptados"], [1])
        self.assertEqual(Cuenta.objects.get().saldo, 920)
        self.assertEqual(Movimiento.objects.filter(tipo="viaje").count(), 1)

    def test_numero_de_validador_sale_del_token(self):
        t = self.emitir()
        self.sync(102, [self.viaje(t, 1, 2, 5)])
        self.assertEqual(Movimiento.objects.get(tipo="viaje").ref, "V102-1")

    def test_operacion_repetida_bloquea_por_clon(self):
        t = self.emitir()
        self.sync(101, [self.viaje(t, 1, 2, 5)])
        self.sync(102, [self.viaje(t, 1, 2, 9)])  # misma operación desde otro bus
        t.refresh_from_db()
        self.assertEqual(t.estado, "bloqueada")
        self.assertTrue(Alerta.objects.filter(tipo="bloqueo automático").exists())

    def test_contador_repetido_bloquea_por_clon(self):
        t = self.emitir()
        self.sync(101, [self.viaje(t, 1, 2, 5)])
        self.sync(102, [self.viaje(t, 1, 3, 5)])
        t.refresh_from_db()
        self.assertEqual(t.estado, "bloqueada")

    def test_fraude_reportado_por_validador_bloquea(self):
        t = self.emitir()
        self.sync(101, [{"id": 1, "tipo": "fraude", "fecha": timezone.now().isoformat(),
                         "tarjeta_id": t.id, "uid": t.uid, "detalle": "copia restaurada"}])
        t.refresh_from_db()
        self.assertEqual(t.estado, "bloqueada")

    def test_viaje_anterior_al_bloqueo_no_alerta(self):
        t = self.emitir()
        antes = timezone.now() - timedelta(minutes=5)
        self.op.post(f"/api/tarjetas/{t.id}/bloquear/", {}, format="json")
        self.sync(101, [self.viaje(t, 1, 2, 5, fecha=antes)])
        self.assertFalse(Alerta.objects.filter(tipo="uso de tarjeta no activa").exists())

    def test_viaje_posterior_al_bloqueo_alerta(self):
        t = self.emitir()
        self.op.post(f"/api/tarjetas/{t.id}/bloquear/", {}, format="json")
        datos = self.sync(101, [self.viaje(t, 1, 2, 5)])
        self.assertTrue(Alerta.objects.filter(tipo="uso de tarjeta no activa").exists())
        self.assertIn(t.id, datos["lista_negra"])

    def test_evento_invalido_se_confirma_con_alerta(self):
        datos = self.sync(101, [{"id": 7, "tipo": "viaje"}])
        self.assertEqual(datos["aceptados"], [7])
        self.assertTrue(Alerta.objects.filter(tipo="evento inválido").exists())

    def test_devuelve_tarifas(self):
        Tarifa.objects.filter(codigo=2).update(precio=90)
        datos = self.sync(101, [])
        self.assertEqual(datos["tarifas"]["2"], ["estudiante", 90])


class RecargaTests(Base):
    def test_remota_abona_a_la_cuenta_y_queda_pendiente(self):
        self.emitir()
        r = self.op.post("/api/recargas/", {"documento": "71234567", "monto": 500},
                         format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Cuenta.objects.get().saldo, 1500)
        pendientes = self.sync(101, [])["recargas"]
        self.assertEqual([(p["seq"], p["monto"]) for p in pendientes], [(2, 500)])

    def test_entregada_por_bus_deja_de_estar_pendiente(self):
        t = self.emitir()
        self.op.post("/api/recargas/", {"documento": "71234567", "monto": 500}, format="json")
        datos = self.sync(101, [self.viaje(t, 1, 2, 5, recarga_hasta=2)])
        self.assertEqual(datos["recargas"], [])

    def test_entregada_a_tarjeta_bloqueada_sigue_pendiente(self):
        t = self.emitir()
        self.op.post("/api/recargas/", {"documento": "71234567", "monto": 500}, format="json")
        self.op.post(f"/api/tarjetas/{t.id}/bloquear/", {}, format="json")
        datos = self.sync(101, [self.viaje(t, 1, 2, 5, recarga_hasta=2)])
        self.assertEqual([p["seq"] for p in datos["recargas"]], [2])

    def test_monto_fuera_de_limites(self):
        self.emitir()
        r = self.op.post("/api/recargas/", {"documento": "71234567", "monto": 50}, format="json")
        self.assertEqual(r.status_code, 409)

    def test_punto_incluye_pendientes_remotas(self):
        t = self.emitir()
        self.op.post("/api/recargas/", {"documento": "71234567", "monto": 100}, format="json")
        r = self.op.post("/api/recargas/", {"tarjeta": t.id, "uid": t.uid, "monto": 300,
                                            "recarga_en_tarjeta": 1}, format="json")
        self.assertEqual((r.data["abono"], r.data["hasta_seq"]), (400, 3))
        self.op.post("/api/recargas/entregas/", {"tarjeta": t.id, "hasta_seq": 3, "operacion": 2,
                                                 "contador": 4, "recarga": r.data["recarga"]["id"]},
                     format="json")
        self.assertFalse(Recarga.objects.filter(aplicada_en__isnull=True).exists())
        self.assertEqual(Movimiento.objects.get(ref=f"R{r.data['recarga']['id']}").operacion, 2)

    def test_punto_rechaza_tarjeta_bloqueada(self):
        t = self.emitir()
        self.op.post(f"/api/tarjetas/{t.id}/bloquear/", {}, format="json")
        r = self.op.post("/api/recargas/", {"tarjeta": t.id, "uid": t.uid, "monto": 300,
                                            "recarga_en_tarjeta": 1}, format="json")
        self.assertEqual(r.status_code, 409)


class EmisionTests(Base):
    def test_documento_repetido(self):
        self.emitir()
        r = self.op.post("/api/emisiones/", {"uid": "04112233445566", "nombre": "Otro",
                                             "documento": "71234567", "tarifa": 1}, format="json")
        self.assertEqual(r.status_code, 409)

    def test_reemplazo_bloquea_la_anterior_y_conserva_saldo(self):
        vieja = self.emitir()
        r = self.op.post("/api/emisiones/", {"uid": "04112233445566", "cuenta": vieja.cuenta_id},
                         format="json")
        self.assertEqual(r.data["saldo"], 1000)
        c = self.op.post(f"/api/emisiones/{r.data['tarjeta']}/confirmar/", {}, format="json")
        self.assertEqual(c.data["bloqueadas"], [{"id": vieja.id, "uid": vieja.uid}])
        vieja.refresh_from_db()
        self.assertEqual(vieja.estado, "bloqueada")

    def test_reemitir_misma_tarjeta_fisica_anula_la_anterior(self):
        vieja = self.emitir()
        r = self.op.post("/api/emisiones/", {"uid": vieja.uid, "cuenta": vieja.cuenta_id},
                         format="json")
        self.op.post(f"/api/emisiones/{r.data['tarjeta']}/confirmar/", {}, format="json")
        vieja.refresh_from_db()
        self.assertEqual(vieja.estado, "anulada")

    def test_cancelar_elimina_cuenta_nueva_vacia(self):
        r = self.op.post("/api/emisiones/", {"uid": "04AABBCCDDEEFF", "nombre": "Ana",
                                             "documento": "1", "tarifa": 2}, format="json")
        self.op.post(f"/api/emisiones/{r.data['tarjeta']}/cancelar/", format="json")
        self.assertFalse(Cuenta.objects.exists())
        self.assertFalse(Tarjeta.objects.exists())

    def test_recarga_pendiente_incluida_en_emision_se_marca_entregada(self):
        vieja = self.emitir()
        self.op.post("/api/recargas/", {"documento": "71234567", "monto": 500}, format="json")
        r = self.op.post("/api/emisiones/", {"uid": "04112233445566", "cuenta": vieja.cuenta_id},
                         format="json")
        self.assertEqual((r.data["saldo"], r.data["recarga_inicial"]), (1500, 2))
        self.op.post(f"/api/emisiones/{r.data['tarjeta']}/confirmar/", {}, format="json")
        self.assertFalse(Recarga.objects.filter(aplicada_en__isnull=True).exists())
