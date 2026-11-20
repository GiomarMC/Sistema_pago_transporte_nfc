"""Espera de tarjeta y de retirada en el lector PC/SC, con pyscard simulado."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from smartcard.Exceptions import NoCardException

from tarjeta import lector
from tarjeta.base import ErrorTarjeta


def lector_con(*resultados_connect):
    """Lector cuyas conexiones sucesivas devuelven o lanzan lo indicado."""
    conexiones = []
    for resultado in resultados_connect:
        conexion = Mock()
        if isinstance(resultado, Exception):
            conexion.connect.side_effect = resultado
        conexiones.append(conexion)
    dispositivo = Mock()
    dispositivo.createConnection.side_effect = conexiones
    return dispositivo, conexiones


class LectorTests(unittest.TestCase):
    def setUp(self):
        dormir = patch("tarjeta.lector.time.sleep")
        dormir.start()
        self.addCleanup(dormir.stop)

    def test_sin_lector_lanza_error_tarjeta(self):
        with patch("smartcard.System.readers", return_value=[]):
            with self.assertRaises(ErrorTarjeta):
                lector.esperar_conexion()

    def test_espera_hasta_que_hay_tarjeta_y_avisa_una_vez(self):
        sin_tarjeta = NoCardException("sin tarjeta", 0)
        dispositivo, conexiones = lector_con(sin_tarjeta, sin_tarjeta, None)
        salida = io.StringIO()
        with patch("smartcard.System.readers", return_value=[dispositivo]), redirect_stdout(salida):
            conexion = lector.esperar_conexion("Acerca una tarjeta")
        self.assertIs(conexion, conexiones[2])
        self.assertEqual(salida.getvalue(), "Acerca una tarjeta\n")

    def test_con_timeout_devuelve_none_si_no_aparece(self):
        dispositivo, _ = lector_con(*[NoCardException("sin tarjeta", 0)] * 3)
        with patch("smartcard.System.readers", return_value=[dispositivo]), \
                patch("tarjeta.lector.time.monotonic", side_effect=[0.0, 0.5, 2.0, 3.0]):
            self.assertIsNone(lector.esperar_conexion(mensaje=None, timeout=1.0))

    def test_esperar_retirada_vuelve_cuando_no_hay_tarjeta(self):
        dispositivo, conexiones = lector_con(None, NoCardException("retirada", 0))
        with patch("smartcard.System.readers", return_value=[dispositivo]):
            lector.esperar_retirada()
        conexiones[0].disconnect.assert_called_once()
        self.assertEqual(dispositivo.createConnection.call_count, 2)


if __name__ == "__main__":
    unittest.main()
