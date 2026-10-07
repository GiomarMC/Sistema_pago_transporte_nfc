from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

admin.site.site_header = "Sistema de transporte — administración"
admin.site.site_title = "Transporte"
admin.site.index_title = "Gestión"

urlpatterns = [
    # Quien escribe solo el dominio llega al panel (o a su pantalla de ingreso)
    path("", RedirectView.as_view(pattern_name="panel:inicio")),
    path("panel/", include("panel.urls")),
    path("admin/", admin.site.urls),
    path("api/", include("transporte.urls")),
]
