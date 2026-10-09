from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from transporte import servicios
from transporte.models import (Alerta, CambioSync, Cuenta, EstadoSync, Movimiento,
                               Recarga, Tarifa, Tarjeta, Validador)


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
            servicios.entregar_recargas(Tarjeta.objects.get(pk=id_tarjeta), 1, 0)
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
        tarifa = Tarifa.objects.get(codigo=2)
        tarifa.precio = 90
        tarifa.save(update_fields=["precio"])
        datos = self.sync(101, [])
        self.assertEqual(datos["tarifas"]["2"], ["estudiante", 90])


class SyncV2Tests(Base):
    def v2(self, bus=101, **datos):
        respuesta = self.bus[bus].post("/api/sync/v2/", datos, format="json")
        self.assertEqual(respuesta.status_code, 200, respuesta.data)
        return respuesta.data

    def test_estado_inicial_y_paginas_acotadas(self):
        primera = self.v2(limite=1)
        self.assertEqual(primera["version"], 2)
        self.assertEqual(len(primera["cambios"]), 1)
        self.assertTrue(primera["hay_mas"])
        segunda = self.v2(cursor=primera["siguiente_cursor"],
                          hasta_version=primera["hasta_version"], limite=1)
        self.assertEqual(len(segunda["cambios"]), 1)
        self.assertFalse(segunda["hay_mas"])
        self.assertEqual(segunda["siguiente_cursor"], segunda["hasta_version"])
        self.assertEqual({c["codigo"] for c in primera["cambios"] + segunda["cambios"]}, {1, 2})

    def test_bloqueo_recarga_y_entrega_generan_cambios(self):
        tarjeta = self.emitir()
        base = self.v2()["siguiente_cursor"]
        self.op.post("/api/recargas/", {"documento": tarjeta.cuenta.documento, "monto": 500},
                     format="json")
        self.op.post(f"/api/tarjetas/{tarjeta.id}/bloquear/", {}, format="json")
        cambios = self.v2(cursor=base)["cambios"]
        self.assertIn({"tipo": "recarga", "accion": "poner", "cuenta_id": tarjeta.cuenta_id,
                       "seq": 2, "monto": 500}, [{k: v for k, v in c.items() if k != "version"}
                                                  for c in cambios])
        self.assertTrue(any(c["tipo"] == "tarjeta" and c["accion"] == "poner"
                            and c["tarjeta_id"] == tarjeta.id for c in cambios))

        # La tarjeta bloqueada no recibe la recarga; se entrega a otra activa.
        nueva = self.op.post("/api/emisiones/", {"uid": "04112233445566",
                                                    "cuenta": tarjeta.cuenta_id}, format="json")
        self.op.post(f"/api/emisiones/{nueva.data['tarjeta']}/confirmar/", {}, format="json")
        self.assertTrue(any(c["tipo"] == "recarga" and c["accion"] == "quitar"
                            and c["seq"] == 2 for c in self.v2(cursor=cambios[-1]["version"])["cambios"]))

    def test_corte_estable_y_reintento(self):
        primera = self.v2(limite=1)
        repetida = self.v2(limite=1)
        self.assertEqual(primera["cambios"], repetida["cambios"])
        self.assertEqual(primera["hasta_version"], repetida["hasta_version"])

        tarjeta = self.emitir()
        self.op.post(f"/api/tarjetas/{tarjeta.id}/bloquear/", {}, format="json")
        segunda = self.v2(cursor=primera["siguiente_cursor"],
                          hasta_version=primera["hasta_version"], limite=1)
        self.assertTrue(all(c["version"] <= primera["hasta_version"] for c in segunda["cambios"]))
        nuevos = self.v2(cursor=segunda["siguiente_cursor"])["cambios"]
        self.assertTrue(any(c["tipo"] == "tarjeta" and c["tarjeta_id"] == tarjeta.id
                            for c in nuevos))

    def test_evento_reenviado_no_duplica_cobro(self):
        tarjeta = self.emitir()
        evento = self.viaje(tarjeta, 7, 2, 5)
        primera = self.v2(eventos=[evento])
        repetida = self.v2(eventos=[evento], cursor=primera["siguiente_cursor"])
        self.assertEqual(primera["aceptados"], [7])
        self.assertEqual(repetida["aceptados"], [7])
        self.assertEqual(Cuenta.objects.get().saldo, 920)
        self.assertEqual(Movimiento.objects.filter(tipo="viaje").count(), 1)

    def test_cursores_invalidos_y_lote_grande_no_procesan_eventos(self):
        ultima = self.v2()["hasta_version"]
        for datos in ({"cursor": -1}, {"cursor": ultima + 1},
                      {"cursor": 2, "hasta_version": 1},
                      {"hasta_version": ultima + 1}, {"limite": 101},
                      {"eventos": [{"id": i} for i in range(41)]}):
            respuesta = self.bus[101].post("/api/sync/v2/", datos, format="json")
            self.assertEqual(respuesta.status_code, 400, respuesta.data)
        self.assertFalse(Alerta.objects.filter(tipo="evento inválido").exists())
        grande = self.bus[101].post("/api/sync/v2/", {"relleno": "x" * 70000}, format="json")
        self.assertEqual(grande.status_code, 413)

    def test_dos_validadores_y_tarifa_editada(self):
        tarifa = Tarifa.objects.get(pk=2)
        tarifa.precio = 90
        tarifa.save(update_fields=["precio"])
        cambios_101 = self.v2(101)["cambios"]
        cambios_102 = self.v2(102)["cambios"]
        self.assertEqual(cambios_101, cambios_102)
        self.assertTrue(any(c["tipo"] == "tarifa" and c["codigo"] == 2
                            and c["precio"] == 90 for c in cambios_101))

    def test_permiso_de_la_nueva_ruta(self):
        self.assertEqual(APIClient().post("/api/sync/v2/", {}, format="json").status_code, 401)
        self.assertEqual(self.op.post("/api/sync/v2/", {}, format="json").status_code, 403)

    def test_borrados_emiten_tombstones(self):
        cuenta = Cuenta.objects.create(nombre="Prueba", documento="99999999",
                                       tarifa=Tarifa.objects.get(pk=1))
        tarjeta = Tarjeta.objects.create(uid="04112233445566", cuenta=cuenta,
                                         estado=Tarjeta.Estado.ACTIVA)
        servicios.bloquear(tarjeta, "prueba")
        recarga = servicios.crear_recarga(cuenta.id, 500, "web")
        base = EstadoSync.objects.get(pk=1).ultima_version
        tarjeta_id = tarjeta.id
        tarjeta.delete()
        recarga.delete()
        cambios = self.v2(cursor=base)["cambios"]
        self.assertTrue(any(c["tipo"] == "tarjeta" and c["accion"] == "quitar"
                            and c["tarjeta_id"] == tarjeta_id for c in cambios))
        self.assertTrue(any(c["tipo"] == "recarga" and c["accion"] == "quitar"
                            and c["seq"] == recarga.seq for c in cambios))

    def test_migracion_carga_estado_preexistente(self):
        tarjeta = self.emitir()
        self.op.post(f"/api/tarjetas/{tarjeta.id}/bloquear/", {}, format="json")
        servicios.crear_recarga(tarjeta.cuenta_id, 500, "web")
        CambioSync.objects.all().delete()
        EstadoSync.objects.all().delete()
        migracion = import_module("transporte.migrations.0006_sync_incremental")
        migracion.cargar_estado_actual(apps, SimpleNamespace(connection=connection))
        cambios = self.v2()["cambios"]
        self.assertEqual(len(cambios), 4)  # 2 tarifas, 1 tarjeta bloqueada, 1 recarga
        self.assertEqual([c["version"] for c in cambios], [1, 2, 3, 4])


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
