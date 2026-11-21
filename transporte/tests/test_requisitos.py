"""Las dependencias que comparten transporte/ y servidor_api/ deben fijarse igual.

Ambos lados calculan la misma criptografía (diversificación AN10922, SUN), así
que una versión distinta de una biblioteca común podría dar resultados distintos
sin que ninguna de las dos suites lo note.
"""

import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
REQUISITOS_TRANSPORTE = RAIZ / "transporte" / "requirements.txt"
REQUISITOS_SERVIDOR = RAIZ / "servidor_api" / "requirements.txt"

_NOMBRE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(.*)$")


def leer_requisitos(texto: str) -> dict[str, str]:
    """Devuelve {paquete normalizado: especificador sin espacios}.

    Ignora comentarios, líneas vacías y opciones de pip (``-r``, ``--hash``...).
    """
    requisitos = {}
    for linea in texto.splitlines():
        linea = linea.split("#", 1)[0].strip()
        if not linea or linea.startswith("-"):
            continue
        coincidencia = _NOMBRE.match(linea)
        if coincidencia is None:
            raise ValueError(f"línea de requirements no reconocida: {linea!r}")
        nombre = re.sub(r"[-_.]+", "-", coincidencia.group(1)).lower()
        requisitos[nombre] = re.sub(r"\s+", "", coincidencia.group(2))
    return requisitos


def diferencias(a: dict[str, str], b: dict[str, str]) -> dict[str, tuple[str, str]]:
    """Paquetes presentes en ambos con especificadores distintos."""
    return {nombre: (a[nombre], b[nombre])
            for nombre in sorted(a.keys() & b.keys())
            if a[nombre] != b[nombre]}


class LeerRequisitosTests(unittest.TestCase):
    def test_normaliza_nombre_y_quita_espacios_y_comentarios(self):
        texto = "# comentario\n\nCryptography == 50.0.2  # fijada\nPy_Scard>=2.0\n-r otro.txt\n"
        self.assertEqual(leer_requisitos(texto),
                         {"cryptography": "==50.0.2", "py-scard": ">=2.0"})

    def test_linea_irreconocible_falla(self):
        with self.assertRaises(ValueError):
            leer_requisitos("==1.0\n")

    def test_detecta_version_distinta_en_paquete_comun(self):
        a = leer_requisitos("cryptography==50.0.2\npyscard>=2.0\n")
        b = leer_requisitos("cryptography==49.0.0\nDjango==6.1.1\n")
        self.assertEqual(diferencias(a, b),
                         {"cryptography": ("==50.0.2", "==49.0.0")})

    def test_paquetes_no_compartidos_no_cuentan(self):
        self.assertEqual(diferencias({"pyscard": ">=2.0"}, {"django": "==6.1.1"}), {})


class RequisitosDelRepositorioTests(unittest.TestCase):
    def test_paquetes_comunes_fijados_igual_en_transporte_y_servidor(self):
        transporte = leer_requisitos(REQUISITOS_TRANSPORTE.read_text(encoding="utf-8"))
        servidor = leer_requisitos(REQUISITOS_SERVIDOR.read_text(encoding="utf-8"))
        self.assertEqual(diferencias(transporte, servidor), {},
                         "transporte/requirements.txt y servidor_api/requirements.txt "
                         "fijan versiones distintas del mismo paquete")


if __name__ == "__main__":
    unittest.main()
