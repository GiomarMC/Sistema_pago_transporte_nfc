"""Rutas del dominio principal; excluye páginas y API de pasajeros."""
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from portal import views as portal_views

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="panel:inicio")),
    path("panel/", include("panel.urls")),
    path("conductor/", include("conductor.urls")),
    path("admin/", admin.site.urls),
    path("api/", include("transporte.urls")),
    path("api/pagos/", include("pagos.urls")),
    # Página de cliente que se mudó al portal (M19 → M14): solo se redirige
    path("mi/reposiciones/", portal_views.reposiciones_en_el_portal),
]
