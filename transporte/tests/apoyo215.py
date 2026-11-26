"""Utilidades comunes de las pruebas con la NTAG215 virtual (sin lector)."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import validador_local
from tarjeta import claves
from tarjeta.virtual215 import ConexionVirtual215

MAESTRA_PRUEBA = bytes(range(32))  # solo para pruebas
UID_PRUEBA = bytes.fromhex("04A1B2C3D4E5F6")


def abrir(conexion):
    """Acerca la tarjeta virtual (toque nuevo) y la abre como los programas."""
    conexion.acercar()
    try:
        from tarjeta.base import abrir_tarjeta
    except ImportError:  # código anterior a la interfaz TarjetaFisica
        from tarjeta.ntag215 import NTAG215
        return NTAG215(conexion)
    return abrir_tarjeta(conexion)


@contextlib.contextmanager
def con_tarjeta(modulo, conexion):
    """Hace que el programa `modulo` encuentre `conexion` en el lector."""
    def esperar(*_, **__):
        return abrir(conexion)

    if hasattr(modulo, "esperar_tarjeta"):
        with patch.object(modulo, "esperar_tarjeta", side_effect=esperar), \
                patch.object(modulo, "esperar_retirada", create=True):
            yield
    else:
        with patch.object(modulo.NTAG215, "esperar", side_effect=esperar), \
                patch.object(modulo.NTAG215, "esperar_retirada"):
            yield


def forma(registro):
    """Resume las APDU enviadas sin los datos: qué comando y sobre qué páginas."""
    pasos = []
    for apdu in registro:
        if apdu[:2] == b"\xFF\xCA":
            pasos.append("UID")
        elif apdu[:2] == b"\xFF\xD6":
            pasos.append(f"W{apdu[3]}")
        elif apdu[:2] == b"\xFF\x00" and apdu[5:7] == b"\xD4\x42":
            op, args = apdu[7], apdu[8:]
            pasos.append({0x60: "V", 0x1B: "PWD", 0x39: "CNT"}.get(op)
                         or (f"R{args[0]}" if op == 0x30 else None)
                         or (f"F{args[0]}-{args[1]}" if op == 0x3A else f"N{op:02X}"))
        elif apdu[5:7] == b"\xD4\x52":
            pasos.append("REL")
        elif apdu[5:7] == b"\xD4\x4A":
            pasos.append("SEL")
        else:
            pasos.append(apdu.hex())
    return " ".join(pasos)


class EntornoTarjetas(unittest.TestCase):
    """Clave maestra de prueba y base del validador en un directorio temporal."""

    def setUp(self):
        super().setUp()
        maestra = patch.object(claves, "_maestra", MAESTRA_PRUEBA)
        maestra.start()
        self.addCleanup(maestra.stop)
        directorio = tempfile.TemporaryDirectory()
        self.addCleanup(directorio.cleanup)
        raiz = patch.object(validador_local, "RAIZ", Path(directorio.name))
        raiz.start()
        self.addCleanup(raiz.stop)

    def tarjeta_de_fabrica(self, uid=UID_PRUEBA):
        return ConexionVirtual215(uid)

    def ejecutar(self, funcion, *argv):
        """Ejecuta el main() de un programa; devuelve (salida, código de sys.exit)."""
        salida = io.StringIO()
        codigo = None
        with patch("sys.argv", ["programa", *argv]), contextlib.redirect_stdout(salida):
            try:
                funcion()
            except SystemExit as e:
                codigo = e.code
        return salida.getvalue(), codigo
