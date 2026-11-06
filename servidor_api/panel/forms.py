from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError

from clientes.models import PREFIJO_USUARIO, es_usuario_cliente


class SolicitudAccesoForm(UserCreationForm):
    """Registro desde el panel: la cuenta queda pendiente hasta que un superusuario la aprueba."""

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("username", "first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # El nombre es lo que permite al administrador saber quién pide acceso
        for campo in ("first_name", "last_name"):
            self.fields[campo].required = True

    def clean_username(self):
        username = super().clean_username()  # None si ya existe (el error ya está registrado)
        if username and es_usuario_cliente(username):
            raise ValidationError(f"Los nombres que empiezan con «{PREFIJO_USUARIO}» están reservados.")
        return username
