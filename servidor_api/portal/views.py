from django.conf import settings
from django.http import HttpResponse, HttpResponsePermanentRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_safe

from . import enlace_sun


@never_cache
@ensure_csrf_cookie
def _pagina(request, nombre, titulo, *, publica=False):
    base = "/mi/" if request.path.startswith("/mi/") else "/"
    return render(request, f"portal/{nombre}.html", {
        "pagina": nombre, "titulo": titulo, "publica": publica,
        "portal_base": base, "script_pagina": "portal/js/paginas.js",
    })


def inicio(request):
    return _pagina(request, "inicio", "Inicio")


def ingresar(request):
    return _pagina(request, "ingresar", "Ingresar", publica=True)


def activar(request):
    return _pagina(request, "activar", "Activar mi tarjeta", publica=True)


def recuperar(request):
    return _pagina(request, "recuperar", "Recuperar contraseña", publica=True)


def restablecer(request):
    return _pagina(request, "restablecer", "Nueva contraseña", publica=True)


def movimientos(request):
    return _pagina(request, "movimientos", "Movimientos")


def recargar(request):
    return _pagina(request, "recargar", "Recargar")


def resultado(request):
    return _pagina(request, "resultado", "Resultado del pago")


def tarjeta(request):
    return _pagina(request, "tarjeta", "Mi tarjeta")


def seguridad(request):
    return _pagina(request, "seguridad", "Seguridad")


def perfil(request):
    return _pagina(request, "perfil", "Mi perfil")


@require_safe
@never_cache
def verificar_sun(request):
    """Página pública a la que lleva la tarjeta al acercarla a un celular (C3).
    Nunca muestra saldo ni nombre: para eso está «Ver mi saldo» (M20-02)."""
    try:
        resultado = enlace_sun.verificar_enlace(enlace_sun.leer_enlace(request.GET))
    except (enlace_sun.EnlaceInvalido, enlace_sun.EnlaceRechazado):
        return _verificacion(request, "invalido", status=400)
    except enlace_sun.VerificacionNoDisponible:
        return _verificacion(request, "no_disponible", status=501)
    return _verificacion(request, "autentica", estado=resultado.estado)


def _verificacion(request, resultado, *, estado=None, status=200):
    base = "/mi/" if request.path.startswith("/mi/") else "/"
    respuesta = render(request, "portal/verificacion.html", {
        "pagina": "verificacion", "titulo": "Verificar tarjeta", "publica": True,
        "portal_base": base, "script_pagina": "portal/js/paginas.js",
        "resultado": resultado, "estado": estado,
    }, status=status)
    # El enlace lleva p y m: que no se filtre al pulsar «Ver mi saldo» ni lo indexen
    respuesta["Referrer-Policy"] = "no-referrer"
    respuesta["X-Robots-Tag"] = "noindex, nofollow"
    return respuesta


def no_encontrada(request, exception):
    if request.path.startswith("/api/v1/"):
        return JsonResponse({"error": {"codigo": "no_encontrado",
                                       "mensaje": "Ruta no encontrada", "campos": {}}}, status=404)
    return HttpResponse("Página no encontrada", status=404)


def error_interno(request):
    if request.path.startswith("/api/v1/"):
        return JsonResponse({"error": {"codigo": "servidor",
                                       "mensaje": "Error interno", "campos": {}}}, status=500)
    return HttpResponse("Error interno", status=500)


def reposiciones_en_el_portal(request):
    """La página /mi/reposiciones/ (M19) se mudó al portal: ahora es "Mi tarjeta". Se redirige
    al host del cliente, que es el único que sirve páginas de cliente."""
    puerto = request.get_host().partition(":")[2]
    host = settings.DOMINIO_CLIENTE + (f":{puerto}" if puerto else "")
    return HttpResponsePermanentRedirect(f"{request.scheme}://{host}/tarjeta/")
