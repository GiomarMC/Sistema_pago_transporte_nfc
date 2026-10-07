from functools import wraps
from urllib.parse import urlencode

from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse

from transporte.permisos import GRUPO_OPERADORES, GRUPO_SOLICITUDES


def es_operador(usuario):
    return usuario.is_authenticated and (
        usuario.is_staff or usuario.groups.filter(name=GRUPO_OPERADORES).exists()
    )


def es_pendiente(usuario):
    return usuario.is_authenticated and usuario.groups.filter(name=GRUPO_SOLICITUDES).exists()


def _ir_a_login(request):
    login = reverse("panel:login")
    return redirect(f"{login}?{urlencode({'next': request.get_full_path()})}")


def operador_requerido(vista):
    @wraps(vista)
    def protegida(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _ir_a_login(request)
        if not es_operador(request.user):
            if es_pendiente(request.user):
                return render(request, "panel/pendiente.html", status=403)
            return HttpResponseForbidden("Esta cuenta no tiene acceso al panel de operaciones.")
        return vista(request, *args, **kwargs)

    return protegida


def superusuario_requerido(vista):
    @wraps(vista)
    def protegida(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _ir_a_login(request)
        if not request.user.is_superuser:
            return HttpResponseForbidden("Solo los administradores gestionan usuarios.")
        return vista(request, *args, **kwargs)

    return protegida
