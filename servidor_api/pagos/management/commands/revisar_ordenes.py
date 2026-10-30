from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from pagos.models import OrdenRecarga
from pagos.pasarelas.base import ErrorPasarela
from pagos.servicios import revisar_orden


class Command(BaseCommand):
    help = "Reconsulta cobros sin webhook y expira órdenes vencidas"

    def handle(self, *args, **options):
        limite = timezone.now() - timedelta(minutes=2)
        revisadas = errores = 0
        for orden in OrdenRecarga.objects.filter(estado__in=["creada", "pendiente"], creada__lte=limite):
            try:
                revisar_orden(orden)
                revisadas += 1
            except ErrorPasarela as exc:
                errores += 1
                self.stderr.write(f"Orden {orden.pk}: {exc}")
        self.stdout.write(f"Órdenes revisadas: {revisadas}; errores: {errores}")
        if errores:
            raise SystemExit(1)
