import unittest
from unittest.mock import patch
from urllib.request import urlopen

from comprobante import abrir_comprobante


class ComprobanteLocalTests(unittest.TestCase):
    def test_hoja_se_sirve_una_vez_sin_archivo_ni_codigo_en_url(self):
        html = b"<html><body>ABCD2345</body></html>"
        vistas = []

        def navegador(url):
            self.assertNotIn("ABCD2345", url)
            with urlopen(url, timeout=2) as respuesta:
                vistas.append(respuesta.read())
                self.assertEqual(respuesta.headers["Cache-Control"], "no-store")
            return True

        with patch("comprobante.webbrowser.open", side_effect=navegador):
            self.assertTrue(abrir_comprobante(html, espera_segundos=2))
        self.assertEqual(vistas, [html])


if __name__ == "__main__":
    unittest.main()
