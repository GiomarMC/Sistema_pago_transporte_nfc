from django.conf import settings
from django.core.management.base import BaseCommand

from transporte.sync_v2 import mqtt


class Command(BaseCommand):
    help = ("Imprime el password_file de Mosquitto: el usuario puente (MQTT_PUENTE_CLAVE) y "
            "cada validador activo con contraseña MQTT. Lo usa mosquitto/credenciales.sh")

    def handle(self, **_):
        if not settings.MQTT_PUENTE_CLAVE:
            self.stderr.write("Aviso: MQTT_PUENTE_CLAVE está vacía en el .env; el puente MQTT "
                              "no podrá conectarse.")
        self.stdout.write(mqtt.archivo_contrasenas(settings.MQTT_PUENTE_CLAVE), ending="")
