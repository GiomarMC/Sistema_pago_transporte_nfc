"""Rutas de mi.<dominio> (ver config.middleware.RuteoPorHost)."""

from django.urls import include, path

from . import views

# API de clientes y de pagos: cada app agrega aquí su include (p. ej. pagos.api_urls)
api_v1 = []

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("api/v1/", include(api_v1)),
    path("t", views.verificar_sun, name="verificar_sun"),
]
