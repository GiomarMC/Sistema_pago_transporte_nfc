"""M9-08: batería de la sincronización v2 de punta a punta.

Los fraudes de test_api.py (v1) repetidos con paquetes C1, más los casos propios
del contrato: paquetes ajenos, bloqueos urgentes y reconstrucción de listas. Los
detalles de cada pieza ya tienen sus tests (test_sync_v2_servicio, _vistas,
test_puente_mqtt); aquí se prueban los recorridos completos: un validador sube
algo y otro (o el mismo, más tarde) recibe la consecuencia en sus LISTAS.
"""

from datetime import timedelta
from itertools import count

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from transporte import servicios
from transporte.management.commands import puente_mqtt
from transporte.models import (Alerta, CambioLista, Cuenta, EventoRecibido, Movimiento, Recarga,
                               Tarifa, Tarjeta, Validador)
from transporte.sync_v2 import paquetes as p
from transporte.sync_v2 import servicio

CLAVE = ec.generate_private_key(ec.SECP256R1())
PEM = CLAVE.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
_secuencias = count(1)


def ahora_unix(desplazamiento=0):
    return int(timezone.now().timestamp()) + desplazamiento


def cliente_con_token(usuario):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=usuario).key}")
    return c


@override_settings(LISTAS_CLAVE_PRIVADA=PEM)
class Base(TestCase):
    def setUp(self):
        cache.clear()   # límite de frecuencia del endpoint
        Usuario = get_user_model()
        self.v = {n: Validador.objects.create(numero=n, usuario=Usuario.objects.create(
            username=f"validador-{n}")) for n in (101, 102, 103)}
        tarifa = Tarifa.objects.order_by("codigo").first()
        self.cuenta = Cuenta.objects.create(nombre="Ana", documento="45678912", tarifa=tarifa,
                                            saldo=1000)
        self.tarjeta = Tarjeta.objects.create(uid="04AABBCCDDEEFF", cuenta=self.cuenta,
                                              estado=Tarjeta.Estado.ACTIVA)

    # --- Lo que manda un validador ---

    def paquete(self, cuerpo, origen=101):
        cab = p.Cabecera(cuerpo.TIPO, origen=origen, destino=p.SERVIDOR,
                         secuencia=next(_secuencias), creado=ahora_unix())
        return p.empaquetar(cab, cuerpo, clave_hmac=self.v[origen].clave_hmac_bytes)

    def viaje(self, id_evento, operacion, contador, fecha=None, tarjeta=None, monto=-80,
              recarga_hasta=0):
        t = tarjeta or self.tarjeta
        return p.Evento(id_evento, p.VIAJE, operacion, fecha or ahora_unix(-3600), t.id,
                        bytes.fromhex(t.uid), monto=monto, contador=contador,
                        recarga_hasta=recarga_hasta)

    def fraude(self, id_evento, detalle):
        return p.Evento(id_evento, p.FRAUDE, 0, ahora_unix(), self.tarjeta.id,
                        bytes.fromhex(self.tarjeta.uid), detalle=detalle)

    def eventos(self, *eventos, origen=101):
        return self.paquete(p.Eventos(eventos), origen=origen)

    def hola(self, version_listas=0, origen=101):
        return self.paquete(p.Hola(version_listas, servicio.tarifas_actuales().version,
                                   ultimo_evento=0, pendientes=0, ajenos=0, enlace=p.WIFI,
                                   senal=-40, bytes_tx_hoy=0, bytes_rx_hoy=0,
                                   version_firmware=20261115), origen=origen)

    def subir(self, *eventos, origen=101, via="mqtt", portador=None):
        return servicio.procesar(self.eventos(*eventos, origen=origen), via, portador)

    # --- Lo que responde el servidor ---

    def leer(self, respuestas):
        return [p.desempaquetar(r, lambda n: None, CLAVE.public_key()) for r in respuestas]

    def entradas(self, version_listas=0, origen=101):
        """Las entradas de LISTAS que recibe el validador en respuesta a su HOLA."""
        respuestas = self.leer(servicio.procesar(self.hola(version_listas, origen), "mqtt"))
        return [e for x in respuestas if x.cabecera.tipo == p.LISTAS for e in x.cuerpo.entradas]

    def version_actual(self):
        return CambioLista.objects.order_by("-version").values_list("version", flat=True).first()

    def estado(self):
        self.tarjeta.refresh_from_db()
        return self.tarjeta.estado

    def saldo(self):
        self.cuenta.refresh_from_db()
        return self.cuenta.saldo

    def alertas(self, tipo):
        return Alerta.objects.filter(tipo=tipo)


# --- Fraudes de la v1, ahora con paquetes C1 -------------------------------------------

class Fraudes(Base):
    def test_misma_operacion_en_otro_bus_bloquea_por_clon(self):
        self.subir(self.viaje(1, operacion=2, contador=5), origen=101)
        self.subir(self.viaje(1, operacion=2, contador=9), origen=102)
        self.assertEqual(self.estado(), Tarjeta.Estado.BLOQUEADA)
        (alerta,) = self.alertas("bloqueo automático")
        self.assertIn("V102-1 repite operación/contador de V101-1", alerta.detalle)
        # El pasajero viajó dos veces: los dos cobros quedan
        self.assertEqual(self.saldo(), 1000 - 2 * 80)

    def test_mismo_contador_nfc_bloquea_por_clon(self):
        self.subir(self.viaje(1, operacion=2, contador=5), origen=101)
        self.subir(self.viaje(1, operacion=3, contador=5), origen=102)
        self.assertEqual(self.estado(), Tarjeta.Estado.BLOQUEADA)

    def test_copia_restaurada_repite_una_operacion_vieja_en_el_mismo_bus(self):
        self.subir(self.viaje(1, 2, 5), self.viaje(2, 3, 6), self.viaje(3, 4, 7))
        self.assertEqual(self.estado(), Tarjeta.Estado.ACTIVA)
        # Alguien volcó la tarjeta cuando iba por la operación 3 y la restauró después
        self.subir(self.viaje(4, 3, 6))
        self.assertEqual(self.estado(), Tarjeta.Estado.BLOQUEADA)

    def test_fraude_reportado_por_el_validador_bloquea(self):
        self.subir(self.fraude(1, "copia restaurada"))
        self.assertEqual(self.estado(), Tarjeta.Estado.BLOQUEADA)
        self.assertTrue(self.alertas("fraude (validador 101)").filter(
            detalle__contains="copia restaurada").exists())

    def test_tarjeta_bloqueada_usada_sin_conexion_alerta_y_cobra(self):
        servicios.bloquear(self.tarjeta, "robo", tipo="bloqueo manual")
        # El bus 102 aún no tenía el bloqueo y la aceptó después
        self.subir(self.viaje(1, 2, 5, fecha=ahora_unix(60)), origen=102)
        (alerta,) = self.alertas("uso de tarjeta no activa")
        self.assertIn("V102-1", alerta.detalle)
        self.assertEqual(self.saldo(), 1000 - 80)

    def test_viaje_anterior_al_bloqueo_no_alerta(self):
        servicios.bloquear(self.tarjeta, "robo", tipo="bloqueo manual")
        # Ocurrió antes del bloqueo; el bus lo sube recién ahora
        self.subir(self.viaje(1, 2, 5, fecha=ahora_unix(-600)))
        self.assertFalse(self.alertas("uso de tarjeta no activa").exists())

    def test_saldo_negativo_alerta_y_registra_el_viaje(self):
        Cuenta.objects.filter(pk=self.cuenta.pk).update(saldo=50)
        self.subir(self.viaje(1, 2, 5))
        self.assertEqual(self.saldo(), 50 - 80)
        (alerta,) = self.alertas("saldo negativo")
        self.assertIn("-S/ 0.30", alerta.detalle)
        self.assertEqual(Movimiento.objects.get(tipo="viaje").saldo_final, -30)

    def test_monto_positivo_en_un_viaje_nunca_abona(self):
        # Un validador manipulado no puede "cobrar" en positivo para recargar
        self.subir(self.viaje(1, 2, 5, monto=500))
        self.assertEqual(self.saldo(), 1000 - 500)

    def test_recarga_grabada_en_tarjeta_bloqueada_sigue_pendiente(self):
        recarga = servicios.crear_recarga(self.cuenta.id, 500, "web")
        servicios.bloquear(self.tarjeta, "robo", tipo="bloqueo manual")
        self.subir(self.viaje(1, 2, 5, fecha=ahora_unix(60), recarga_hasta=recarga.seq))
        recarga.refresh_from_db()
        self.assertIsNone(recarga.aplicada_en)
        self.assertIn(p.EntradaLista(p.RECARGA, p.PENDIENTE, self.cuenta.id, recarga.seq, 500),
                      self.entradas(origen=103))


# --- Paquetes ajenos (C1: el portador solo transporta bytes) -----------------------------

class Ajenos(Base):
    def test_clon_detectado_aunque_el_paquete_llegue_por_otro_bus(self):
        self.subir(self.viaje(1, 2, 5), origen=101)
        # El 102 viajó sin señal; su paquete lo sube el 103 por ESP-NOW
        respuestas = self.subir(self.viaje(1, 2, 9), origen=102, via="espnow", portador=103)
        self.assertEqual(self.estado(), Tarjeta.Estado.BLOQUEADA)
        (acuse,) = self.leer(respuestas)
        self.assertEqual((acuse.cabecera.destino, acuse.cuerpo.hasta_evento), (102, 1))
        self.assertEqual(Movimiento.objects.filter(validador=102).count(), 1)
        self.assertEqual(Movimiento.objects.filter(validador=103).count(), 0)

    def test_mismo_paquete_por_tres_caminos_cobra_una_vez(self):
        datos = self.eventos(self.viaje(1, 2, 5), self.viaje(2, 3, 6), origen=102)
        servicio.procesar(datos, "espnow", portador=101)
        servicio.procesar(datos, "ble")
        servicio.procesar(datos, "mqtt")
        self.assertEqual(self.saldo(), 1000 - 2 * 80)
        self.assertEqual(self.estado(), Tarjeta.Estado.ACTIVA, "un reenvío no es un clon")
        self.assertEqual(set(EventoRecibido.objects.values_list("via", flat=True)), {"espnow"})

    def test_portador_que_altera_el_paquete_no_logra_nada(self):
        datos = bytearray(self.eventos(self.viaje(1, 2, 5), origen=102))
        datos[-40] ^= 0xFF          # dentro del cuerpo: cambia el evento
        self.assertEqual(servicio.procesar(bytes(datos), "espnow", portador=101), [])
        self.assertEqual(self.saldo(), 1000)
        self.assertFalse(EventoRecibido.objects.exists())
        (alerta,) = self.alertas("paquete inválido")
        self.assertIn("portador 101", alerta.detalle)

    def test_hola_ajeno_actualiza_al_origen_y_le_responde_a_el(self):
        CambioLista.objects.create(tipo=p.BLOQUEO, op=p.AGREGAR, a=77)
        respuestas = self.leer(servicio.procesar(self.hola(origen=102), "espnow", portador=101))
        self.assertTrue(respuestas)
        self.assertTrue(all(x.cabecera.destino == 102 for x in respuestas))
        self.v[102].refresh_from_db()
        self.assertEqual((self.v[102].ultimo_hola["via"], self.v[102].ultimo_hola["portador"]),
                         ("espnow", 101))
        self.v[101].refresh_from_db()
        self.assertIsNone(self.v[101].ultima_sync, "el portador no cuenta como sincronizado")


# --- Bloqueos urgentes ------------------------------------------------------------------

class Urgentes(Base):
    def test_clon_genera_un_listas_urgente_para_todos(self):
        self.subir(self.viaje(1, 2, 5), origen=101)
        self.subir(self.viaje(1, 2, 9), origen=102)
        (cambio,) = CambioLista.objects.filter(urgente=True, difundido_en__isnull=True)
        (urgente,) = self.leer(puente_mqtt.paquetes_urgentes([cambio]))
        self.assertEqual((urgente.cabecera.destino, urgente.cuerpo.urgente), (p.TODOS, True))
        self.assertEqual((urgente.cuerpo.version_desde, urgente.cuerpo.version_hasta),
                         (cambio.version - 1, cambio.version))
        self.assertEqual(urgente.cuerpo.entradas,
                         (p.EntradaLista(p.BLOQUEO, p.AGREGAR, self.tarjeta.id),))

    def test_bus_que_no_recibio_el_urgente_lo_recibe_en_su_hola(self):
        version = self.version_actual() or 0
        self.subir(self.fraude(1, "clon"), origen=101)
        # El 103 estaba apagado cuando salió el urgente: lo recibe al volver
        self.assertIn(p.EntradaLista(p.BLOQUEO, p.AGREGAR, self.tarjeta.id),
                      self.entradas(version, origen=103))

    def test_solo_los_bloqueos_son_urgentes(self):
        servicios.crear_recarga(self.cuenta.id, 500, "web")
        self.subir(self.viaje(1, 2, 5, recarga_hasta=1))
        self.assertFalse(CambioLista.objects.filter(urgente=True).exists())
        self.subir(self.fraude(2, "clon"))
        self.assertEqual(list(CambioLista.objects.filter(urgente=True).values_list("tipo", "a")),
                         [(CambioLista.Tipo.BLOQUEO, self.tarjeta.id)])

    def test_dos_buses_detectan_el_mismo_clon_y_sale_un_solo_urgente(self):
        self.subir(self.fraude(1, "clon"), origen=101)
        self.subir(self.fraude(1, "clon"), origen=102)
        self.assertEqual(CambioLista.objects.filter(urgente=True).count(), 1)
        self.assertEqual(self.alertas("bloqueo automático").count(), 2)


# --- Reconstrucción de listas -----------------------------------------------------------

class Reconstruccion(Base):
    def aplicar(self, version, origen=103):
        """Simula el validador: aplica los LISTAS y devuelve su lista negra y recargas."""
        negra, recargas, al_final = set(), set(), version
        respuestas = self.leer(servicio.procesar(self.hola(version, origen), "mqtt"))
        for x in [r.cuerpo for r in respuestas if r.cabecera.tipo == p.LISTAS]:
            if x.version_desde == 0:
                negra, recargas = set(), set()          # foto completa: se parte de cero
            for e in x.entradas:
                if e.tipo == p.BLOQUEO:
                    (negra.add if e.op == p.AGREGAR else negra.discard)(e.a)
                else:
                    (recargas.add if e.op == p.PENDIENTE else recargas.discard)((e.a, e.b))
            al_final = x.version_hasta
        return negra, recargas, al_final

    def test_atraso_grande_recibe_la_foto_del_estado_actual(self):
        otra = Tarjeta.objects.create(uid="04BB", cuenta=self.cuenta,
                                      estado=Tarjeta.Estado.ACTIVA)
        entregada = servicios.crear_recarga(self.cuenta.id, 500, "web")
        pendiente = servicios.crear_recarga(self.cuenta.id, 300, "web")
        self.subir(self.viaje(1, 2, 5, recarga_hasta=entregada.seq))
        self.subir(self.fraude(2, "clon"))
        CambioLista.objects.bulk_create([CambioLista(tipo=p.BLOQUEO, op=p.QUITAR, a=10_000 + i)
                                         for i in range(servicio.ATRASO_FOTO + 1)])
        negra, recargas, version = self.aplicar(1)
        self.assertEqual(negra, {self.tarjeta.id})
        self.assertNotIn(otra.id, negra)
        self.assertEqual(recargas, {(self.cuenta.id, pendiente.seq)},
                         "la entregada ya no está pendiente")
        self.assertEqual(version, self.version_actual())

    def test_despues_de_la_foto_queda_al_dia(self):
        CambioLista.objects.bulk_create([CambioLista(tipo=p.BLOQUEO, op=p.AGREGAR, a=i)
                                         for i in range(servicio.ATRASO_FOTO + 1)])
        _, _, version = self.aplicar(1)
        self.assertEqual(self.entradas(version, origen=103), [])
        self.v[103].refresh_from_db()
        self.assertEqual(self.v[103].version_listas, version)

    def test_foto_y_cambios_llegan_al_mismo_estado(self):
        for i in range(3):
            t = Tarjeta.objects.create(uid=f"04C{i}", cuenta=self.cuenta,
                                       estado=Tarjeta.Estado.ACTIVA)
            servicios.bloquear(t, "robo", tipo="bloqueo manual")
        servicios.crear_recarga(self.cuenta.id, 400, "web")
        self.subir(self.fraude(1, "clon"))
        por_cambios = self.aplicar(0, origen=102)
        por_foto = self.aplicar(self.version_actual() + 1, origen=103)  # versión del futuro
        self.assertEqual(por_cambios, por_foto)

    def test_validador_sin_nada_que_poner_recibe_una_foto_vacia(self):
        CambioLista.objects.create(tipo=p.BLOQUEO, op=p.AGREGAR, a=1)
        actual = self.version_actual()
        respuestas = self.leer(servicio.procesar(self.hola(actual + 5), "mqtt"))
        (foto,) = [x.cuerpo for x in respuestas if x.cabecera.tipo == p.LISTAS]
        self.assertEqual((foto.version_desde, foto.version_hasta, foto.entradas), (0, actual, ()))


# --- Por HTTP y junto con la v1 ---------------------------------------------------------

class HttpYV1(Base):
    URL = "/api/v2/paquetes/"

    def setUp(self):
        super().setUp()
        self.bus = {n: cliente_con_token(v.usuario) for n, v in self.v.items()}

    def post(self, numero, cuerpo):
        r = self.bus[numero].generic("POST", self.URL, cuerpo,
                                     content_type="application/octet-stream")
        self.assertEqual(r.status_code, 200, r.content)
        return self.leer(p.separar(r.content))

    def test_clon_por_http_y_el_otro_bus_recibe_el_bloqueo(self):
        self.post(101, self.eventos(self.viaje(1, 2, 5), origen=101))
        self.post(102, self.eventos(self.viaje(1, 2, 9), origen=102))
        self.assertEqual(self.estado(), Tarjeta.Estado.BLOQUEADA)
        respuestas = self.post(103, self.hola(0, origen=103))
        entradas = [e for x in respuestas if x.cabecera.tipo == p.LISTAS
                    for e in x.cuerpo.entradas]
        self.assertIn(p.EntradaLista(p.BLOQUEO, p.AGREGAR, self.tarjeta.id), entradas)

    def test_clon_entre_un_bus_v1_y_uno_v2(self):
        r = self.bus[101].post("/api/sync/", {"eventos": [{
            "id": 1, "tipo": "viaje", "fecha": (timezone.now() - timedelta(hours=1)).isoformat(),
            "tarjeta_id": self.tarjeta.id, "uid": self.tarjeta.uid, "monto": 80,
            "operacion": 2, "contador": 5}]}, format="json")
        self.assertEqual(r.status_code, 200)
        self.post(102, self.eventos(self.viaje(1, 2, 9), origen=102))
        self.assertEqual(self.estado(), Tarjeta.Estado.BLOQUEADA)
        # Y el bus v1 ve el bloqueo en su lista negra
        r = self.bus[101].post("/api/sync/", {"eventos": []}, format="json")
        self.assertIn(self.tarjeta.id, r.data["lista_negra"])

    def test_evento_subido_por_v1_y_reenviado_por_v2_no_se_cobra_dos_veces(self):
        self.bus[101].post("/api/sync/", {"eventos": [{
            "id": 1, "tipo": "viaje", "fecha": (timezone.now() - timedelta(hours=1)).isoformat(),
            "tarjeta_id": self.tarjeta.id, "uid": self.tarjeta.uid, "monto": 80,
            "operacion": 2, "contador": 5}]}, format="json")
        # El mismo bus, ya con firmware v2, vuelve a subir su evento 1
        (acuse,) = self.post(101, self.eventos(self.viaje(1, 2, 5), origen=101))
        self.assertEqual(acuse.cuerpo.hasta_evento, 1)
        self.assertEqual(self.saldo(), 1000 - 80)
        self.assertEqual(self.estado(), Tarjeta.Estado.ACTIVA)

    def test_recarga_web_llega_al_bus_v2_y_se_entrega(self):
        recarga = servicios.crear_recarga(self.cuenta.id, 500, "web")
        respuestas = self.post(101, self.hola(0, origen=101))
        entradas = [e for x in respuestas if x.cabecera.tipo == p.LISTAS
                    for e in x.cuerpo.entradas]
        self.assertIn(p.EntradaLista(p.RECARGA, p.PENDIENTE, self.cuenta.id, recarga.seq, 500),
                      entradas)
        self.post(101, self.eventos(self.viaje(1, 2, 5, recarga_hasta=recarga.seq)))
        recarga.refresh_from_db()
        self.assertEqual(recarga.aplicada_por, 101)
        self.assertNotIn((self.cuenta.id, recarga.seq), {
            (r.cuenta_id, r.seq) for r in Recarga.objects.filter(aplicada_en__isnull=True)})
