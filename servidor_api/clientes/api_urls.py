"""Rutas C4 compartidas por el portal local y mi.subepe.app."""
from django.urls import path

from panel import reposiciones_cliente

from . import api_contrasena, api_identidad, api_portal, api_resumen, api_seguridad

urlpatterns = [
    path("resumen/", api_resumen.resumen),
    path("movimientos/", api_resumen.movimientos),
    path("sesion/", api_portal.sesion),
    path("sesion/otp/", api_identidad.confirmar_otp),
    path("sesion/otp/reenviar/", api_identidad.reenviar_otp),
    path("activacion/", api_identidad.activar_identidad),
    path("activacion/correo/", api_identidad.activar_correo),
    path("activacion/confirmar/", api_identidad.activar_confirmar),
    path("contrasena/recuperar/", api_contrasena.recuperar),
    path("contrasena/restablecer/", api_contrasena.restablecer),
    path("contrasena/cambiar/", api_contrasena.cambiar),
    path("seguridad/accesos/", api_seguridad.accesos),
    path("seguridad/dispositivos/", api_seguridad.dispositivos),
    path("seguridad/dispositivos/<int:dispositivo_id>/", api_seguridad.dispositivos),
    path("perfil/correo/", api_seguridad.pedir_cambio_correo),
    path("perfil/correo/confirmar/", api_seguridad.confirmar_cambio_correo),
    path("perfil/", api_portal.perfil),
    path("reposiciones/", reposiciones_cliente.api_reposiciones),
    path("tarjetas/<int:tarjeta_id>/bloquear/", reposiciones_cliente.api_bloquear_tarjeta),
]
