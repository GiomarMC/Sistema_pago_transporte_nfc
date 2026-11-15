"""Puente MQTT ↔ Django de la sincronización v2 (M9-06, contrato C1 5.8).

- sp/v/{n}/sube → procesar(paquete, "mqtt", portador=n) → cada respuesta a
  sp/v/{n}/baja. Las que son para otros buses (acuses de paquetes ajenos) se
  publican igual al portador, que las reenvía por ESP-NOW.
- Cada 2 s: los CambioLista urgentes sin difundir → LISTAS urgente firmado a
  sp/todos/urgente → difundido_en.
- Se reconecta solo (espera exponencial hasta 60 s) si se cae Mosquitto, sigue
  aunque se caiga PostgreSQL (renueva la conexión en cada mensaje) y termina
  limpio con SIGTERM (docker stop).

Todo corre en un solo hilo (client.loop() a mano), así el acceso a la base de
datos no se mezcla entre hilos.
"""

import logging
import re
import signal
import time

import paho.mqtt.client as mqtt
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections, transaction
from django.utils import timezone

from transporte.models import CambioLista
from transporte.sync_v2 import servicio
from transporte.sync_v2.mqtt import USUARIO_PUENTE
from transporte.sync_v2.paquetes import LISTAS, TODOS, EntradaLista, Listas

log = logging.getLogger("puente_mqtt")

SUBE = "sp/v/+/sube"
URGENTE = "sp/todos/urgente"
_TOPICO_SUBE = re.compile(r"^sp/v/(\d{1,5})/sube$")
CADA_URGENTES = 2.0         # s
MAX_URGENTES = 50           # por vuelta, para no tapar la subida tras una caída larga
ESPERA_MAXIMA = 60          # s entre reintentos de conexión


def baja(numero: int) -> str:
    return f"sp/v/{numero}/baja"


def paquetes_urgentes(cambios) -> list[bytes]:
    """Un LISTAS urgente por cambio, para todos los buses (como el vector listas_urgente).

    version_desde = version - 1: el validador aplica los bloqueos urgentes aunque
    no sean contiguos y no cambia su version_listas (C1 5.5)."""
    return [servicio.firmar(LISTAS, TODOS, Listas(
        c.version - 1, c.version, (EntradaLista(c.tipo, c.op, c.a, c.b, c.c),), urgente=True))
        for c in cambios]


class Puente:
    def __init__(self, host, puerto, clave):
        self.host, self.puerto = host, puerto
        self.parar = False
        self.conectado = False
        self.cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="puente",
                                   clean_session=True)
        self.cliente.username_pw_set(USUARIO_PUENTE, clave)
        self.cliente.on_connect = self._al_conectar
        self.cliente.on_disconnect = self._al_desconectar
        self.cliente.on_message = self._al_recibir

    # --- MQTT ---

    def _al_conectar(self, cliente, _datos, _flags, codigo, _props):
        if codigo.is_failure:
            log.error("Mosquitto rechazó la conexión: %s", codigo)
            return
        self.conectado = True
        cliente.subscribe(SUBE, qos=0)
        log.info("conectado a %s:%s, suscrito a %s", self.host, self.puerto, SUBE)

    def _al_desconectar(self, _cliente, _datos, _flags, codigo, _props):
        if self.conectado:
            log.warning("desconectado de Mosquitto: %s", codigo)
        self.conectado = False

    def _al_recibir(self, cliente, _datos, mensaje):
        m = _TOPICO_SUBE.match(mensaje.topic)
        if not m:
            return
        portador = int(m.group(1))
        close_old_connections()
        try:
            respuestas = servicio.procesar(mensaje.payload, "mqtt", portador=portador)
        except Exception:
            # Base de datos caída u otro fallo: el validador no recibe ACUSE y lo reenvía
            log.exception("no se pudo procesar un paquete de %s", portador)
            close_old_connections()
            return
        for r in respuestas:
            cliente.publish(baja(portador), r, qos=0)

    # --- Urgentes ---

    def difundir_urgentes(self) -> int:
        close_old_connections()
        try:
            with transaction.atomic():
                cambios = list(CambioLista.objects.select_for_update(skip_locked=True)
                               .filter(urgente=True, difundido_en__isnull=True)
                               .order_by("version")[:MAX_URGENTES])
                publicados = []
                for c, paquete in zip(cambios, paquetes_urgentes(cambios)):
                    if self.cliente.publish(URGENTE, paquete, qos=0).rc != mqtt.MQTT_ERR_SUCCESS:
                        break   # sin conexión: los que faltan van en la próxima vuelta
                    publicados.append(c.pk)
                CambioLista.objects.filter(pk__in=publicados).update(difundido_en=timezone.now())
        except Exception:
            log.exception("no se pudieron difundir los bloqueos urgentes")
            close_old_connections()
            return 0
        if publicados:
            log.info("%d bloqueos urgentes difundidos", len(publicados))
        return len(publicados)

    # --- Bucle ---

    def correr(self):
        espera, proxima_urgente = 1, 0.0
        while not self.parar:
            if not self.conectado:
                try:
                    self.cliente.connect(self.host, self.puerto, keepalive=60)
                except OSError as e:
                    log.warning("Mosquitto no responde (%s); reintento en %d s", e, espera)
                    self._dormir(espera)
                    espera = min(espera * 2, ESPERA_MAXIMA)
                    continue
                # El CONNACK llega en loop(): hasta entonces no está conectado
                limite = time.monotonic() + 10
                while not self.conectado and not self.parar and time.monotonic() < limite:
                    if self.cliente.loop(timeout=0.5) != mqtt.MQTT_ERR_SUCCESS:
                        break
                if not self.conectado:
                    self.cliente.disconnect()
                    log.warning("sin respuesta de Mosquitto; reintento en %d s", espera)
                    self._dormir(espera)
                    espera = min(espera * 2, ESPERA_MAXIMA)
                    continue
                espera = 1

            if self.cliente.loop(timeout=0.5) != mqtt.MQTT_ERR_SUCCESS:
                self.conectado = False
                continue
            if time.monotonic() >= proxima_urgente:
                self.difundir_urgentes()
                proxima_urgente = time.monotonic() + CADA_URGENTES

        if self.conectado:
            self.cliente.disconnect()
            self.cliente.loop(timeout=0.5)
        close_old_connections()
        log.info("puente detenido")

    def _dormir(self, segundos):
        fin = time.monotonic() + segundos
        while not self.parar and time.monotonic() < fin:
            time.sleep(min(0.2, fin - time.monotonic()))


class Command(BaseCommand):
    help = ("Puente entre Mosquitto y Django: procesa lo que suben los validadores por MQTT "
            "y difunde los bloqueos urgentes (M9-06)")

    def handle(self, **_):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        if not settings.MQTT_PUENTE_CLAVE:
            log.error("Falta MQTT_PUENTE_CLAVE en el .env")
            return
        puente = Puente(settings.MQTT_HOST, settings.MQTT_PUERTO, settings.MQTT_PUENTE_CLAVE)

        def detener(signum, _frame):
            log.info("señal %s: deteniendo", signal.Signals(signum).name)
            puente.parar = True

        signal.signal(signal.SIGTERM, detener)
        signal.signal(signal.SIGINT, detener)
        puente.correr()
