from django.urls import path
from . import views

app_name = "conductor"
urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("sw.js", views.service_worker, name="sw"),
    path("guia/", views.guia, name="guia"),
]
