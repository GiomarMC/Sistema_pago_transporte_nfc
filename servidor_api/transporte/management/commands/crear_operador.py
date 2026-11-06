from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from rest_framework.authtoken.models import Token

from clientes.models import PREFIJO_USUARIO, es_usuario_cliente
from transporte.permisos import GRUPO_OPERADORES


class Command(BaseCommand):
    help = ("Crea un operador (emisión, recarga, atención) y muestra su token. Para entrar "
            "también al panel web, asígnale contraseña con: manage.py changepassword <usuario>")

    def add_arguments(self, parser):
        parser.add_argument("usuario")

    def handle(self, usuario, **_):
        if es_usuario_cliente(usuario):
            raise CommandError(f"Los nombres que empiezan con «{PREFIJO_USUARIO}» están reservados "
                               "para los clientes")
        u, creado = get_user_model().objects.get_or_create(username=usuario)
        if creado:
            u.set_unusable_password()
            u.save()
        u.groups.add(Group.objects.get(name=GRUPO_OPERADORES))
        token, _ = Token.objects.get_or_create(user=u)
        self.stdout.write(token.key)
