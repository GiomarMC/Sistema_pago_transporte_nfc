from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from rest_framework.authtoken.models import Token

from transporte.models import Validador


class Command(BaseCommand):
    help = "Registra un validador (bus) y muestra su token de acceso a la API"

    def add_arguments(self, parser):
        parser.add_argument("numero", type=int, help="n.º de validador (1-899)")
        parser.add_argument("--descripcion", default="", help="bus, placa, ruta...")

    def handle(self, numero, descripcion, **_):
        if not 1 <= numero <= 899:
            raise CommandError("el número debe estar entre 1 y 899 (900 es el punto de recarga)")
        usuario, _ = get_user_model().objects.get_or_create(username=f"validador-{numero}")
        usuario.set_unusable_password()  # solo entra con token
        usuario.save()
        Validador.objects.update_or_create(numero=numero, defaults={
            "usuario": usuario, "descripcion": descripcion})
        token, _ = Token.objects.get_or_create(user=usuario)
        self.stdout.write(token.key)
