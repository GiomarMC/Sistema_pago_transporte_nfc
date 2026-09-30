import json
from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = ("Crea (si no existen) un operador y los validadores indicados, y muestra el "
            "contenido de transporte/api.json para los programas del lector")

    def add_arguments(self, parser):
        parser.add_argument("--url", required=True,
                            help="URL con la que los clientes llegan al servidor, "
                                 "p. ej. http://192.168.1.50:8000")
        parser.add_argument("--operador", default="operador1")
        parser.add_argument("--validadores", type=int, nargs="*", default=[101, 102])

    def handle(self, url, operador, validadores, **_):
        def token(*args, **kwargs):
            salida = StringIO()
            call_command(*args, stdout=salida, **kwargs)
            return salida.getvalue().strip()

        config = {
            "url": url,
            "token_operador": token("crear_operador", operador),
            "validadores": {str(n): token("crear_validador", n, descripcion=f"Bus {n}")
                            for n in validadores},
        }
        self.stdout.write(json.dumps(config, indent=2))
