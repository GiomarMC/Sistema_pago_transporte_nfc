"""Alerta de las tarjetas que saltan una operación (#237). La lanza el cron de la VM una vez al día
(verificar_libro.sh). A mano:  python manage.py revisar_operaciones [--horas 24]"""

from datetime import timedelta

from django.core.management.base import BaseCommand

from transporte import servicios


class Command(BaseCommand):
    help = "Alerta de las operaciones de tarjeta que faltan en el servidor"

    def add_arguments(self, parser):
        parser.add_argument("--horas", type=int, default=24,
                            help="un hueco cuenta solo si la operación siguiente lleva más de esto en el servidor")

    def handle(self, *args, horas, **opciones):
        nuevas = servicios.revisar_operaciones(espera=timedelta(hours=horas))
        for tarjeta, operacion in nuevas:
            self.stdout.write(f"Tarjeta {tarjeta}: falta la operación {operacion}")
        self.stdout.write(f"{len(nuevas)} operaciones sin registrar (alertas nuevas)")
