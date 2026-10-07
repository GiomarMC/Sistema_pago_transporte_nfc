from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm


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
