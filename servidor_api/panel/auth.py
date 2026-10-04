from functools import wraps
from urllib.parse import urlencode

from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse

from transporte.permisos import GRUPO_OPERADORES


def es_operador(usuario):
    return usuario.is_authenticated and (
        usuario.is_staff or usuario.groups.filter(name=GRUPO_OPERADORES).exists()
    )


def operador_requerido(vista):
    @wraps(vista)
    def protegida(request, *args, **kwargs):
        if not request.user.is_authenticated:
            login = reverse("panel:login")
            return redirect(f"{login}?{urlencode({'next': request.get_full_path()})}")
        if not es_operador(request.user):
            return HttpResponseForbidden("Esta cuenta no tiene acceso al panel de operaciones.")
        return vista(request, *args, **kwargs)

    return protegida
