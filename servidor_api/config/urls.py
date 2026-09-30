from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Sistema de transporte — administración"
admin.site.site_title = "Transporte"
admin.site.index_title = "Gestión"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("transporte.urls")),
]
