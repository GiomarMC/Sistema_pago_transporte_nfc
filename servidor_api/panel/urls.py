from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "panel"

urlpatterns = [
    path("ingresar/", auth_views.LoginView.as_view(template_name="panel/login.html", redirect_authenticated_user=True), name="login"),
    path("salir/", auth_views.LogoutView.as_view(next_page="panel:login"), name="logout"),
    path("", views.inicio, name="inicio"),
    path("cuentas/", views.cuentas, name="cuentas"),
    path("cuentas/<int:cuenta_id>/", views.cuenta_detalle, name="cuenta_detalle"),
    path("tarjetas/<int:tarjeta_id>/bloquear/", views.bloquear_tarjeta, name="bloquear_tarjeta"),
    path("viajes/", views.viajes, name="viajes"),
    path("validadores/", views.validadores, name="validadores"),
    path("alertas/", views.alertas, name="alertas"),
    path("alertas/<int:alerta_id>/revisar/", views.revisar_alerta, name="revisar_alerta"),
]
