"""Rutas de mi.<dominio> (ver config.middleware.RuteoPorHost).

Las mismas rutas se sirven bajo /mi/ del dominio principal solo en desarrollo
(PORTAL_EN_RUTA_MI)."""

from django.urls import include, path

from . import views

# La misma API C4 funciona en el host dedicado y en las rutas locales heredadas.
api_v1 = [path("", include("clientes.api_urls")),
          path("", include("pagos.api_urls"))]

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("ingresar/", views.ingresar, name="ingresar"),
    path("activar/", views.activar, name="activar"),
    path("recuperar/", views.recuperar, name="recuperar"),
    path("restablecer/", views.restablecer, name="restablecer"),
    path("movimientos/", views.movimientos, name="movimientos"),
    path("recargar/resultado/", views.resultado, name="resultado"),
    path("recargar/", views.recargar, name="recargar"),
    path("tarjeta/", views.tarjeta, name="tarjeta"),
    path("seguridad/", views.seguridad, name="seguridad"),
    path("perfil/", views.perfil, name="perfil"),
    path("api/v1/", include(api_v1)),
    path("pagos/", include("pagos.urls_simulador")),
    path("t", views.verificar_sun, name="verificar_sun"),
]

handler404 = "portal.views.no_encontrada"
handler500 = "portal.views.error_interno"
