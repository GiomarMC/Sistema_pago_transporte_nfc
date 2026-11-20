from django.urls import path

from . import api_cliente

urlpatterns = [
    path("recargas/ordenes/", api_cliente.ordenes),
    path("recargas/ordenes/<uuid:orden_id>/", api_cliente.detalle),
]
