from django.apps import AppConfig
from django.db.models.signals import post_save


class LibroConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "libro"
    verbose_name = "Libro mayor"

    def ready(self):
        from .models import crear_saldo_cliente

        # Cada cuenta de cliente nueva tiene su SALDO_CLIENTE_<id> desde el primer
        # momento, en la misma transacción que la crea
        post_save.connect(crear_saldo_cliente, sender="transporte.Cuenta",
                          dispatch_uid="libro_saldo_cliente")
