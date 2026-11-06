from django.core.management.base import BaseCommand, CommandError

from transporte.models import Validador


class Command(BaseCommand):
    help = ("Muestra la clave HMAC de un validador (64 hex) para cargarla en su ESP32, "
            "o le da una nueva con --rotar")

    def add_arguments(self, parser):
        parser.add_argument("numero", type=int, help="n.º de validador")
        parser.add_argument("--rotar", action="store_true",
                            help="genera una clave nueva; la anterior deja de valer al momento")

    def handle(self, numero, rotar, **_):
        try:
            validador = Validador.objects.get(numero=numero)
        except Validador.DoesNotExist:
            raise CommandError(f"no existe el validador {numero} "
                               "(se registra con manage.py crear_validador)") from None
        if rotar:
            validador.rotar_clave_hmac()
            self.stderr.write(
                f"Clave del validador {numero} rotada. Sus paquetes con la clave anterior "
                "se rechazan desde ahora: carga la nueva en su ESP32.")
        self.stdout.write(validador.clave_hmac)
