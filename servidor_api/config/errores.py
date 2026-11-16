import re

from django.views.debug import SafeExceptionReporterFilter


class FiltroErrores(SafeExceptionReporterFilter):
    """Además de lo que Django ya oculta (KEY, SECRET, PASS...), oculta los ajustes en
    español con CLAVE o SECRETO, como LISTAS_CLAVE_PRIVADA, en las páginas e informes de
    error."""

    hidden_settings = re.compile(
        SafeExceptionReporterFilter.hidden_settings.pattern + "|CLAVE|SECRETO", flags=re.I)
