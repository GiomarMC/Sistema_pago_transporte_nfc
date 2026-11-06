from django.conf import settings


class RuteoPorHost:
    """En mi.<dominio> solo existe el portal de clientes: ni el panel, ni el admin, ni la
    API de operadores y validadores. El resto de hosts usa ROOT_URLCONF."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.get_host().lower().startswith("mi."):
            request.urlconf = settings.URLCONF_PORTAL
        return self.get_response(request)
