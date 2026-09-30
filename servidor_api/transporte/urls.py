from django.urls import path

from . import views

urlpatterns = [
    path("sync/", views.sync),
    path("emisiones/", views.emision_reservar),
    path("emisiones/<int:id_tarjeta>/confirmar/", views.emision_confirmar),
    path("emisiones/<int:id_tarjeta>/cancelar/", views.emision_cancelar),
    path("tarjetas/<int:id_tarjeta>/", views.tarjeta_detalle),
    path("tarjetas/<int:id_tarjeta>/bloquear/", views.tarjeta_bloquear),
    path("tarjetas/uid/<str:uid>/anular/", views.tarjeta_anular_uid),
    path("cuentas/", views.cuentas_buscar),
    path("cuentas/<int:id_cuenta>/", views.cuenta_detalle),
    path("cuentas/<int:id_cuenta>/bloquear/", views.cuenta_bloquear),
    path("recargas/", views.recargas),
    path("recargas/entregas/", views.recargas_entrega),
]
