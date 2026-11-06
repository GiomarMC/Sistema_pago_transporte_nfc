"""Genera la clave ECDSA P-256 del servidor para la sincronización v2 (M0-06).

    python manage.py generar_clave_listas >> .env      # nueva clave
    python manage.py generar_clave_listas --solo-cabecera   # rehace clave_servidor.h

Por la salida estándar solo sale la línea LISTAS_CLAVE_PRIVADA=... (para el .env); los
avisos van por la salida de errores. La clave pública se escribe en clave_servidor.h, que
se compila en el firmware del validador.
"""

import re
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from transporte.sync_v2 import claves

CABECERA = settings.BASE_DIR.parent / "pantalla_esp32" / "src" / "validador" / \
    "clave_servidor.h"
CONFIGURADA = re.compile(r"^#define CLAVE_SERVIDOR_CONFIGURADA 1\b", re.M)


def contenido_cabecera(publica: ec.EllipticCurvePublicKey) -> str:
    punto = claves.punto_publico(publica)
    filas = ",\n".join("    " + ", ".join(f"0x{b:02X}" for b in punto[i:i + 16])
                       for i in range(0, len(punto), 16))
    return f"""\
// Clave pública ECDSA P-256 del servidor (contrato C1, M0-06): la ESP32 la usa para
// verificar los LISTAS, ACUSE y TARIFAS que recibe. Generado por
// "manage.py generar_clave_listas": no editar a mano. Es pública, puede ir en el repo.
#pragma once
#include <stdint.h>

#define CLAVE_SERVIDOR_CONFIGURADA 1

// Huella (SHA-256 del punto, 8 B), para compararla con la del servidor: {claves.huella(publica)}
// Punto sin comprimir: 0x04 | X | Y
static const uint8_t CLAVE_SERVIDOR[65] = {{
{filas},
}};
"""


class Command(BaseCommand):
    help = "Genera la clave ECDSA P-256 del servidor (LISTAS_CLAVE_PRIVADA y clave_servidor.h)"

    def add_arguments(self, parser):
        parser.add_argument("--forzar", action="store_true",
                            help="reemplaza una clave existente (obliga a reflashear TODOS "
                                 "los validadores)")
        parser.add_argument("--solo-cabecera", action="store_true",
                            help="no genera clave: rehace clave_servidor.h con la de "
                                 "LISTAS_CLAVE_PRIVADA")
        parser.add_argument("--cabecera", type=Path, default=CABECERA,
                            help=f"dónde escribir la clave pública (por defecto {CABECERA})")

    def handle(self, forzar, solo_cabecera, cabecera, **_):
        if not cabecera.parent.is_dir():
            raise CommandError(f"no existe la carpeta {cabecera.parent}; indica otra ruta "
                               "con --cabecera")
        if solo_cabecera:
            try:
                publica = claves.clave_publica_listas()
            except ImproperlyConfigured as e:
                raise CommandError(str(e)) from None
            self._escribir(cabecera, publica)
            return

        existentes = []
        if getattr(settings, "LISTAS_CLAVE_PRIVADA", "").strip():
            existentes.append("LISTAS_CLAVE_PRIVADA ya está definida")
        if cabecera.exists() and CONFIGURADA.search(cabecera.read_text()):
            existentes.append(f"{cabecera.name} ya tiene una clave")
        if existentes and not forzar:
            raise CommandError(
                "; ".join(existentes) + ". Cambiar la clave obliga a reflashear todos los "
                "validadores: si es lo que quieres, repite con --forzar.")

        privada = ec.generate_private_key(ec.SECP256R1())
        pem = privada.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
        self._escribir(cabecera, privada.public_key())
        self.stdout.write(f"LISTAS_CLAVE_PRIVADA={claves.pem_en_una_linea(pem)}")
        self.stderr.write(
            "Copia la línea LISTAS_CLAVE_PRIVADA=... en el .env del servidor (sustituye la "
            "anterior si la hay) y reinícialo. No la subas al repositorio ni la compartas.")

    def _escribir(self, cabecera: Path, publica: ec.EllipticCurvePublicKey):
        cabecera.write_text(contenido_cabecera(publica))
        self.stderr.write(f"Clave pública escrita en {cabecera} "
                          f"(huella {claves.huella(publica)}). Recompila y carga el firmware.")
