"""Diversificación AN10922 (M1-05, #39): vectores oficiales, RFC 4493, contrato y entradas inválidas."""

import json
import unittest
from pathlib import Path

import generar_vectores_an10922
from tarjeta import an10922

H = bytes.fromhex
VECTORES = Path(__file__).resolve().parents[2] / "docs" / "contratos" / "vectores_an10922.json"


class VectoresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v = json.loads(VECTORES.read_text(encoding="utf-8"))

    def test_rfc_4493(self):
        self.assertEqual(len(self.v["cmac"]), 4)
        for caso in self.v["cmac"]:
            with self.subTest(largo=len(caso["mensaje"]) // 2):
                self.assertEqual(an10922.cmac_aes128(H(caso["clave"]), H(caso["mensaje"])).hex(), caso["cmac"])

    def test_ejemplo_oficial_de_an10922(self):
        (caso,) = self.v["oficiales"]
        self.assertEqual(caso["clave"], "a8dd63a3b89d54b37ca802473fda9175")
        self.assertEqual(an10922.an10922_aes128(H(caso["maestra"]), H(caso["m"])).hex(), caso["clave"])

    def test_propios(self):
        for caso in self.v["propios"]:
            with self.subTest(caso["nombre"]):
                self.assertEqual(an10922.entrada_m(H(caso["uid"]), caso["ranura"], caso["version"]).hex(), caso["m"])
                self.assertEqual(an10922.diversificar_aes128(H(caso["maestra"]), H(caso["uid"]), caso["ranura"],
                                                             caso["version"]).hex(), caso["clave"])

    def test_invalidos(self):
        for caso in self.v["invalidos"]:
            with self.subTest(caso["motivo"]), self.assertRaises(ValueError):
                an10922.diversificar_aes128(H(caso["maestra"]), H(caso["uid"]), caso["ranura"], caso["version"])

    def test_el_generador_reproduce_el_archivo(self):
        self.assertEqual(generar_vectores_an10922.texto(), VECTORES.read_text(encoding="utf-8"))


class ContratoTests(unittest.TestCase):
    def test_ejemplo_del_contrato(self):
        # Issue #39: maestra 22…22, UID 04b3e121ca2a81, ranura 1, versión 1
        self.assertEqual(an10922.entrada_m(H("04b3e121ca2a81"), 1, 1).hex(), "04b3e121ca2a81537562655065010100")
        self.assertEqual(an10922.diversificar_aes128(H("22" * 16), H("04b3e121ca2a81"), 1, 1).hex(),
                         "e771c37f331393a1017eb46917603f09")

    def test_k4_coincide_con_el_servidor(self):
        # El mismo valor que comprueban servidor_api/transporte/tests/test_sun.py (k4_v1) y la página /t
        self.assertEqual(an10922.diversificar_aes128(H("44" * 16), H("04b3e121ca2a81"), 4, 1).hex(),
                         "7635fed287aa54870ea237fd72dfed45")

    def test_m_mide_16_y_termina_en_cero(self):
        m = an10922.entrada_m(H("04b3e121ca2a81"), 2, 0xFF)
        self.assertEqual((len(m), m[7:13], m[13], m[14], m[15]), (16, b"SubePe", 2, 0xFF, 0))

    def test_cambia_con_cada_entrada(self):
        base = an10922.diversificar_aes128(H("22" * 16), H("04b3e121ca2a81"), 1, 1)
        for otra in (an10922.diversificar_aes128(H("23" + "22" * 15), H("04b3e121ca2a81"), 1, 1),
                     an10922.diversificar_aes128(H("22" * 16), H("04b3e121ca2a80"), 1, 1),
                     an10922.diversificar_aes128(H("22" * 16), H("04b3e121ca2a81"), 2, 1),
                     an10922.diversificar_aes128(H("22" * 16), H("04b3e121ca2a81"), 1, 2)):
            self.assertNotEqual(otra, base)

    def test_m_fuera_de_rango_en_an10922(self):
        # Con |M| < 16 AN10922 ya no coincide con el CMAC estándar: se rechaza
        for largo in (13, 15, 32):
            with self.subTest(largo=largo), self.assertRaises(ValueError):
                an10922.an10922_aes128(H("22" * 16), bytes(largo))

    def test_tipos_invalidos(self):
        with self.assertRaises(ValueError):
            an10922.entrada_m("04b3e121ca2a81", 1, 1)       # texto en vez de bytes
        with self.assertRaises(ValueError):
            an10922.entrada_m(H("04b3e121ca2a81"), 1, 1.0)  # versión no entera


if __name__ == "__main__":
    unittest.main()
