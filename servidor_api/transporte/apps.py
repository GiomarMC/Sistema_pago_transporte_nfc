from django.apps import AppConfig


class TransporteConfig(AppConfig):
    name = 'transporte'

    def ready(self):
        from . import signals  # noqa: F401
