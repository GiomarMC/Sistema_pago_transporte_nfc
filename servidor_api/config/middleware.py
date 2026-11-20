from django.conf import settings
from django.http import HttpResponseNotFound

# Páginas de cliente que ya sirve el dominio principal bajo /mi/ (M19), aparte del portal
RUTAS_MI_DEL_PANEL = ("/mi/reposiciones/",)


class RuteoPorHost:
    """En mi.<dominio> solo existe el portal de clientes: ni el panel, ni el admin, ni la
    API de operadores y validadores. El resto de hosts usa ROOT_URLCONF."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.get_host().lower().startswith("mi."):
            request.urlconf = settings.URLCONF_PORTAL
        elif (request.path.startswith("/mi/") and not settings.PORTAL_EN_RUTA_MI
              and not request.path.startswith(RUTAS_MI_DEL_PANEL)):
            return HttpResponseNotFound()
        return self.get_response(request)
