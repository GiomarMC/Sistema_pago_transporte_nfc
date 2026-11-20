from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.template import Context
from django.template.loader import get_template

from notificaciones.muestras import MUESTRAS


class Command(BaseCommand):
    help = (
        "Escribe en un directorio el HTML y el texto de cada plantilla con datos de ejemplo, "
        "para revisarlas en el navegador o enviarlas a una cuenta de prueba."
    )

    def add_arguments(self, parser):
        parser.add_argument("directorio", help="Carpeta de salida (se crea si no existe).")

    def handle(self, *args, directorio, **options):
        salida = Path(directorio)
        salida.mkdir(parents=True, exist_ok=True)
        for tipo, contexto in MUESTRAS.items():
            try:
                html = get_template(f"notificaciones/{tipo}.html").render(contexto)
                txt = get_template(f"notificaciones/{tipo}.txt").template.render(
                    Context(contexto, autoescape=False))
            except Exception as exc:
                raise CommandError(f"Falla la plantilla «{tipo}»: {exc}") from exc
            (salida / f"{tipo}.html").write_text(html, encoding="utf-8")
            (salida / f"{tipo}.txt").write_text(txt, encoding="utf-8")
        self.stdout.write(f"{len(MUESTRAS)} plantillas escritas en {salida.resolve()}")
