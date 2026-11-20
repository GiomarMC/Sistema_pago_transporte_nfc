from django.conf import settings
from django.http import HttpResponseNotFound

# Páginas de cliente que ya sirve el dominio principal bajo /mi/ (M19), aparte del portal
RUTAS_MI_DEL_PANEL = ("/mi/reposiciones/",)


class RuteoPorHost:
    """Separa portal y operaciones usando el host, sin compartir URLconf.

    - DOMINIO_CLIENTE (mi.<dominio>): solo el portal de clientes, con cabeceras de seguridad y
      sesión que vence a los 15 minutos de inactividad.
    - DOMINIOS_PRINCIPALES: solo operaciones (panel, admin, API de validadores); nada del portal.
    - Cualquier otro host (localhost, tests): ROOT_URLCONF, con el portal bajo /mi/ únicamente en
      desarrollo (PORTAL_EN_RUTA_MI).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":", 1)[0].lower()
        if host == settings.DOMINIO_CLIENTE.lower():
            request.urlconf = settings.URLCONF_PORTAL
            request.es_portal_cliente = True
        elif host in settings.DOMINIOS_PRINCIPALES:
            request.urlconf = settings.URLCONF_PRINCIPAL
        elif (request.path.startswith("/mi/") and not settings.PORTAL_EN_RUTA_MI
              and not request.path.startswith(RUTAS_MI_DEL_PANEL)):
            return HttpResponseNotFound()
        respuesta = self.get_response(request)
        if getattr(request, "es_portal_cliente", False):
            if (getattr(request, "user", None) is not None and request.user.is_authenticated
                    and request.session.session_key
                    and request.session.exists(request.session.session_key)):
                # La expiración es por inactividad, no desde el primer ingreso.
                request.session.set_expiry(900)
            respuesta["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
            respuesta["Referrer-Policy"] = "same-origin"
            respuesta["X-Content-Type-Options"] = "nosniff"
            if settings.SESSION_COOKIE_SECURE:
                respuesta["Strict-Transport-Security"] = "max-age=31536000"
        return respuesta
