from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from pagos.models import OrdenRecarga
from pagos.pasarelas import obtener_pasarela
from transporte.models import Alerta


class Command(BaseCommand):
    help = "Compara cobros de pasarela y órdenes acreditadas de un día"

    def add_arguments(self, parser):
        parser.add_argument("--dia", default=None, help="AAAA-MM-DD; por defecto ayer")

    def handle(self, *args, **options):
        try:
            dia = date.fromisoformat(options["dia"]) if options["dia"] else timezone.localdate() - timedelta(days=1)
        except ValueError as exc:
            raise CommandError("--dia debe ser AAAA-MM-DD") from exc
        transacciones = {t.id_externo: t for t in obtener_pasarela().listar_transacciones(dia)}
        ordenes = {o.id_externo: o for o in OrdenRecarga.objects.filter(
            pagada_en__date=dia).exclude(id_externo__isnull=True)}
        diferencias = []
        for id_externo, transaccion in transacciones.items():
            orden = ordenes.get(id_externo)
            if transaccion.estado in ("pagado", "reembolsado") and (not orden or orden.estado not in ("acreditada", "reembolsada")):
                diferencias.append(f"{id_externo}: pagada en pasarela sin acreditación")
            if orden and transaccion.monto != orden.monto:
                diferencias.append(f"{id_externo}: montos distintos")
        for id_externo, orden in ordenes.items():
            if orden.estado in ("acreditada", "reembolsada") and id_externo not in transacciones:
                diferencias.append(f"{id_externo}: acreditada sin transacción")
        for diferencia in diferencias:
            Alerta.objects.get_or_create(tipo="pago inconsistente", detalle=f"{dia}: {diferencia}")
            self.stderr.write(diferencia)
        self.stdout.write(f"{dia}: {len(transacciones)} transacciones, {len(ordenes)} órdenes, {len(diferencias)} diferencias")
        if diferencias:
            raise CommandError("conciliación con diferencias")
