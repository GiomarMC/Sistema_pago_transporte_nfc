"""M9-06: lógica del puente MQTT (sin broker: el cliente MQTT es simulado)."""

from types import SimpleNamespace
from unittest import mock

import paho.mqtt.client as mqtt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from transporte import servicios
from transporte.management.commands import puente_mqtt
from transporte.models import CambioLista, Cuenta, Tarifa, Tarjeta, Validador
from transporte.sync_v2 import paquetes as p

CLAVE = ec.generate_private_key(ec.SECP256R1())
PEM = CLAVE.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()


def leer(datos):
    return p.desempaquetar(datos, lambda n: None, CLAVE.public_key())


class ClienteFalso:
    def __init__(self, rc=mqtt.MQTT_ERR_SUCCESS):
        self.publicados, self.rc = [], rc

    def publish(self, topico, datos, qos=0):
        if self.rc == mqtt.MQTT_ERR_SUCCESS:
            self.publicados.append((topico, datos))
        return SimpleNamespace(rc=self.rc)


@override_settings(LISTAS_CLAVE_PRIVADA=PEM)
class Puente(TestCase):
    def setUp(self):
        # close_old_connections() cierra la conexión si está dentro de una transacción
        # (cada TestCase lo está); en el puente real cada mensaje va fuera de una.
        parche = mock.patch.object(puente_mqtt, "close_old_connections")
        parche.start()
        self.addCleanup(parche.stop)
        self.puente = puente_mqtt.Puente("mosquitto", 1883, "clave")
        self.puente.cliente = ClienteFalso()
        tarifa = Tarifa.objects.order_by("codigo").first()
        self.cuenta = Cuenta.objects.create(nombre="Ana", documento="45678912", tarifa=tarifa)
        self.v101 = Validador.objects.create(
            numero=101, usuario=get_user_model().objects.create(username="validador-101"))

    def tarjeta(self, uid):
        return Tarjeta.objects.create(uid=uid, cuenta=self.cuenta, estado=Tarjeta.Estado.ACTIVA)

    # --- Urgentes ---

    def test_bloqueo_se_difunde_como_listas_urgente_a_todos(self):
        t = self.tarjeta("04AA")
        servicios.bloquear(t, "robo")
        cambio = CambioLista.objects.get()
        self.assertEqual(self.puente.difundir_urgentes(), 1)
        ((topico, datos),) = self.puente.cliente.publicados
        self.assertEqual(topico, "sp/todos/urgente")
        paquete = leer(datos)
        self.assertEqual((paquete.cabecera.tipo, paquete.cabecera.origen, paquete.cabecera.destino),
                         (p.LISTAS, p.SERVIDOR, p.TODOS))
        self.assertEqual(paquete.cuerpo, p.Listas(cambio.version - 1, cambio.version,
                                                  (p.EntradaLista(p.BLOQUEO, p.AGREGAR, t.id),),
                                                  urgente=True))
        cambio.refresh_from_db()
        self.assertIsNotNone(cambio.difundido_en)
        # Ya difundido: no se repite
        self.assertEqual(self.puente.difundir_urgentes(), 0)
        self.assertEqual(len(self.puente.cliente.publicados), 1)

    def test_solo_los_urgentes(self):
        servicios.crear_recarga(self.cuenta.id, 500, "web")           # no urgente
        CambioLista.objects.create(tipo=1, op=1, a=9, urgente=False)
        self.assertEqual(self.puente.difundir_urgentes(), 0)
        self.assertEqual(self.puente.cliente.publicados, [])

    def test_sin_conexion_no_marca_como_difundido(self):
        servicios.bloquear(self.tarjeta("04BB"), "robo")
        self.puente.cliente = ClienteFalso(rc=mqtt.MQTT_ERR_NO_CONN)
        self.assertEqual(self.puente.difundir_urgentes(), 0)
        self.assertIsNone(CambioLista.objects.get().difundido_en)
        self.puente.cliente = ClienteFalso()
        self.assertEqual(self.puente.difundir_urgentes(), 1)

    def test_maximo_por_vuelta(self):
        CambioLista.objects.bulk_create([CambioLista(tipo=1, op=1, a=i, urgente=True)
                                         for i in range(1, 61)])
        self.assertEqual(self.puente.difundir_urgentes(), puente_mqtt.MAX_URGENTES)
        self.assertEqual(self.puente.difundir_urgentes(), 60 - puente_mqtt.MAX_URGENTES)
        versiones = [leer(d).cuerpo.version_hasta for _, d in self.puente.cliente.publicados]
        self.assertEqual(versiones, sorted(versiones))

    # --- Subida ---

    def mensaje(self, topico, payload):
        return SimpleNamespace(topic=topico, payload=payload)

    def test_procesa_lo_que_sube_y_responde_al_portador(self):
        cab = p.Cabecera(p.HOLA, origen=101, destino=0, secuencia=1, creado=1793000000)
        hola = p.empaquetar(cab, p.Hola(0, 0, 0, 0, 0, p.WIFI, 0, 0, 0, 20261114),
                            clave_hmac=self.v101.clave_hmac_bytes)
        with mock.patch.object(puente_mqtt.servicio, "procesar",
                               wraps=puente_mqtt.servicio.procesar) as procesar:
            self.puente._al_recibir(self.puente.cliente, None, self.mensaje("sp/v/102/sube", hola))
        procesar.assert_called_once_with(hola, "mqtt", portador=102)
        topicos = {t for t, _ in self.puente.cliente.publicados}
        self.assertEqual(topicos, {"sp/v/102/baja"}, "todo va al portador")
        self.assertEqual({leer(d).cabecera.destino for _, d in self.puente.cliente.publicados},
                         {101}, "las respuestas son para el origen")

    def test_ignora_topicos_ajenos(self):
        with mock.patch.object(puente_mqtt.servicio, "procesar") as procesar:
            for topico in ("sp/v/abc/sube", "sp/v/101/baja", "sp/todos/urgente", "otro"):
                self.puente._al_recibir(self.puente.cliente, None, self.mensaje(topico, b"x"))
        procesar.assert_not_called()

    def test_un_fallo_al_procesar_no_tumba_el_puente(self):
        with mock.patch.object(puente_mqtt.servicio, "procesar", side_effect=RuntimeError("bd")):
            self.puente._al_recibir(self.puente.cliente, None, self.mensaje("sp/v/101/sube", b"x"))
        self.assertEqual(self.puente.cliente.publicados, [])

    def test_paquete_invalido_no_responde(self):
        self.puente._al_recibir(self.puente.cliente, None,
                                self.mensaje("sp/v/101/sube", b"basura"))
        self.assertEqual(self.puente.cliente.publicados, [])
