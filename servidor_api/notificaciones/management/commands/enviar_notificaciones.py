from django.core.management.base import BaseCommand

from notificaciones.models import Notificacion
from notificaciones.servicios import LIMITE_POR_EJECUCION, procesar_pendientes


class Command(BaseCommand):
    help = (
        "Envía las notificaciones pendientes que ya están disponibles para reintento "
        f"(máximo {LIMITE_POR_EJECUCION} por ejecución). Pensado para cron cada 5 minutos."
    )

    def handle(self, *args, **options):
        intentadas = procesar_pendientes()
        estados = list(Notificacion.objects.values_list("estado", flat=True))
        self.stdout.write(
            f"Intentadas: {intentadas}. Pendientes: {estados.count(Notificacion.PENDIENTE)}, "
            f"enviadas: {estados.count(Notificacion.ENVIADA)}, "
            f"fallidas: {estados.count(Notificacion.FALLIDA)}"
        )
