from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from panel import reposiciones_cliente
from clientes import api_resumen
from clientes import api_portal

admin.site.site_header = "Sistema de transporte — administración"
admin.site.site_title = "Transporte"
admin.site.index_title = "Gestión"

urlpatterns = [
    # Quien escribe solo el dominio llega al panel (o a su pantalla de ingreso)
    path("", RedirectView.as_view(pattern_name="panel:inicio")),
    path("panel/", include("panel.urls")),
    path("admin/", admin.site.urls),
    path("api/", include("transporte.urls")),
    path("api/pagos/", include("pagos.urls")),
    path("api/v1/", include("pagos.api_urls")),
    path("api/v1/reposiciones/", reposiciones_cliente.api_reposiciones),
    path("api/v1/resumen/", api_resumen.resumen),
    path("api/v1/movimientos/", api_resumen.movimientos),
    path("api/v1/sesion/", api_portal.sesion),
    path("api/v1/perfil/", api_portal.perfil),
    path("api/v1/tarjetas/<int:tarjeta_id>/bloquear/", reposiciones_cliente.api_bloquear_tarjeta),
    path("mi/reposiciones/", reposiciones_cliente.pagina_reposiciones),
    path("mi/", include("portal.urls")),
    path("pagos/", include("pagos.urls_simulador")),
]
