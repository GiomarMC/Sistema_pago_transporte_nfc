from django.conf import settings
from django.http import HttpResponse, HttpResponsePermanentRedirect, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie


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


def verificar_sun(request):
    # Verificación del mensaje SUN (URL que genera la tarjeta al acercarla a un celular)
    return JsonResponse({"error": {"codigo": "no_implementado",
                                   "mensaje": "Verificación SUN aún no disponible"}}, status=501)


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
