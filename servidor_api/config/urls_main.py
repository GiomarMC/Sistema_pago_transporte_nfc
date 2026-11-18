"""Rutas del dominio principal; excluye páginas y API de pasajeros."""
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="panel:inicio")),
    path("panel/", include("panel.urls")),
    path("admin/", admin.site.urls),
    path("api/", include("transporte.urls")),
    path("api/pagos/", include("pagos.urls")),
]
