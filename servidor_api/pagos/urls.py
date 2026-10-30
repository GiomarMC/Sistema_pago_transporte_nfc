from django.urls import path

from . import vistas

app_name = "pagos"
urlpatterns = [
    path("webhook/<str:pasarela>/", vistas.webhook, name="webhook"),
]

simulador_urlpatterns = [
    path("simulador/<uuid:id_externo>/", vistas.simulador, name="simulador"),
]
