"""
Comprueba que el libro mayor cuadra (M10-05):

1. la suma de todas las partidas es cero, y
2. el saldo en caché de cada cliente (Cuenta.saldo) es el que dice el libro.

Cada diferencia queda como Alerta "descuadre contable" (se ve en el panel) y el
comando termina con código 1. Sin diferencias, código 0.

En la VM lo lanza el cron cada día a las 03:30 de Perú, después del respaldo
(verificar_libro.sh; ver "Copias de seguridad" en el README).
"""

from django.core.management.base import BaseCommand, CommandError

from libro import libro
from libro.models import Asiento
from panel.models import VerificacionLibro
from transporte.models import Alerta, Cuenta, soles

ALERTA = "descuadre contable"


class Command(BaseCommand):
    help = ("Comprueba que el libro mayor suma cero y que Cuenta.saldo coincide con el libro "
            f"en cada cliente. Cada diferencia crea una alerta \"{ALERTA}\" y sale con código 1.")

    def handle(self, **opciones):
        problemas = []
        suma = libro.suma_global()
        if suma:
            problemas.append(f"el libro no suma cero: las partidas suman {suma:+d} céntimos")
        for id_cuenta, saldo, en_libro in libro.descuadres():
            problemas.append(f"cuenta {id_cuenta}: Cuenta.saldo {soles(saldo)}, "
                             f"libro {soles(en_libro)} (diferencia {soles(saldo - en_libro)})")

        # Cada corrida queda registrada: el panel muestra la última (M19-03)
        VerificacionLibro.objects.create(
            correcto=not problemas, diferencias=problemas,
            cuentas=Cuenta.objects.count(), asientos=Asiento.objects.count())

        if not problemas:
            if opciones["verbosity"]:
                self.stdout.write("Libro mayor: cuadra (suma cero y todos los saldos coinciden)")
            return

        Alerta.objects.bulk_create(Alerta(tipo=ALERTA, detalle=p) for p in problemas)
        for p in problemas:
            self.stderr.write(p)
        raise CommandError(f"libro mayor: {len(problemas)} descuadre(s); "
                           f"ver alertas \"{ALERTA}\"", returncode=1)
