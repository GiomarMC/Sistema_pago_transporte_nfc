from functools import wraps
from urllib.parse import urlencode

from django.http import HttpResponseForbidden
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import Resolver404, resolve, reverse
from pathlib import Path
from transporte.permisos import GRUPO_CONDUCTORES



def conductor_requerido(vista):
    @wraps(vista)
    def protegida(request, *args, **kwargs):
        if not request.user.is_authenticated:
            login = reverse("panel:login")
            return redirect(f"{login}?{urlencode({'next': request.get_full_path()})}")
        if not request.user.is_active or not request.user.groups.filter(name=GRUPO_CONDUCTORES).exists():
            return HttpResponseForbidden("Esta cuenta no tiene acceso al relé del conductor.")
        return vista(request, *args, **kwargs)
    return protegida


@conductor_requerido
def inicio(request):
    try:
        resolve("/api/v2/paquetes/")
        disponible = True
    except Resolver404:
        disponible = False
    return render(request, "conductor/inicio.html", {"disponible": disponible})


@conductor_requerido
def service_worker(request):
    ruta = Path(__file__).resolve().parent / "static/conductor/sw.js"
    respuesta = HttpResponse(ruta.read_bytes(), content_type="application/javascript")
    respuesta["Cache-Control"] = "no-cache"
    return respuesta


@conductor_requerido
def guia(request):
    return render(request, "conductor/guia.html")
