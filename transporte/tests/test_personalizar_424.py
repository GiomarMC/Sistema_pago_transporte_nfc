"""personalizar_424.py (M1-06, #40) con el simulador de la NTAG 424: ajustes exactos, restablecer e idempotencia
cortando en cada APDU.

    cd transporte && python3 -m unittest tests.test_personalizar_424
"""

import json
import tempfile
import unittest
from pathlib import Path

import personalizar_424 as p424
from tarjeta import apdu424 as a
from tarjeta import ntag424
from tarjeta.base import ErrorTarjeta
from tarjeta.ntag424 import NTAG424
from tarjeta.simulador424 import Simulador424
from tests.apoyo215 import EntornoTarjetas

UID = bytes.fromhex("04B3E121CA2A81")
MAESTRAS = p424.Maestras({n: bytes([0x10 + n]) * 16 for n in range(5)})  # solo para pruebas
# GetFileSettings que pide la especificación (docs/ntag424-personalizacion.md §8.3 y §9), escritos a mano
AJUSTES_02 = bytes.fromhex("00" "40" "00E0" "000100" "C1" "FF34" "190000" "3C0000" "3C0000")
AJUSTES_03 = bytes.fromhex("00" "03" "2012" "800000")


def silencio(*_):
    pass


class Corte:
    """transmitir() que retira la tarjeta en la APDU número `en` (desde 0): antes de que llegue o, con
    `aplicada`, después de que la tarjeta la ejecute (se pierde solo la respuesta)."""

    def __init__(self, sim, en=None, aplicada=False):
        self.sim, self.en, self.aplicada, self.enviadas = sim, en, aplicada, 0

    def __call__(self, apdu):
        n, self.enviadas = self.enviadas, self.enviadas + 1
        if n == self.en and not self.aplicada:
            self.sim.presente = False
            raise a.TarjetaRetirada("retirada antes de la APDU")
        respuesta = self.sim.transmitir(apdu)
        if n == self.en:
            self.sim.presente = False
            raise a.TarjetaRetirada("retirada antes de la respuesta")
        return respuesta


def chip(sim, transmitir=None):
    sim.acercar()
    return NTAG424(transmitir or sim.transmitir, sim.uid)


def ajustes(sim):
    c = chip(sim)
    c.seleccionar_aplicacion()
    return {n: c.ajustes_archivo(n) for n in (1, 2, 3)}


def huella(sim):
    """Todo lo que la personalización puede cambiar en la tarjeta."""
    return (list(sim.claves), list(sim.versiones), ajustes(sim),
            {n: bytes(f.datos) for n, f in sim.archivos.items()})


class Personalizar(EntornoTarjetas):
    def setUp(self):
        super().setUp()
        self.sim = Simulador424(uid=UID)

    def personalizar(self, sim=None):
        sim = sim or self.sim
        return p424.personalizar(chip(sim), MAESTRAS, silencio)

    def comprobar_personalizada(self, sim):
        self.assertEqual(sim.claves, MAESTRAS.claves(UID))
        self.assertEqual(sim.versiones, [1] * 5)
        leidos = ajustes(sim)
        self.assertEqual((leidos[2], leidos[3]), (AJUSTES_02, AJUSTES_03))
        self.assertEqual(bytes(sim.archivos[3].datos), bytes(128))  # el área la graba la emisión

    def test_ajustes_exactos(self):
        self.assertEqual(self.personalizar(), 1)  # la lectura de comprobación del SUN
        self.comprobar_personalizada(self.sim)
        self.assertEqual(ajustes(self.sim)[1], ajustes(Simulador424())[1])  # el CC no se toca

    def test_k3_comun_y_el_resto_diversificadas(self):
        otra = Simulador424(uid=bytes.fromhex("0412BD11CE2A81"))
        self.personalizar()
        self.personalizar(otra)
        iguales = [self.sim.claves[n] == otra.claves[n] for n in range(5)]
        self.assertEqual(iguales, [False, False, False, True, False])

    def test_el_validador_lee_con_k1_y_escribe_con_k2(self):
        self.personalizar()
        claves = ntag424.claves_validador(bytes([0x11]) * 16, bytes([0x12]) * 16)
        sim = self.sim
        sim.acercar()
        t = ntag424.Tarjeta424(NTAG424(sim.transmitir, UID), claves)
        t.autenticar()
        self.assertEqual(t.leer_area(), bytes(72))
        t.escribir_registro(0, bytes(range(20)))
        self.assertEqual(t.leer_area()[32:52], bytes(range(20)))

    def test_el_sun_se_comprueba_en_cada_toque(self):
        self.personalizar()
        c = chip(self.sim)
        claves = MAESTRAS.claves(UID)
        for esperado in (2, 3):
            self.sim.acercar()
            self.assertEqual(p424.comprobar_sun(c.leer_ndef(), UID, claves[3], claves[4]), esperado)

    def test_sun_alterado(self):
        self.personalizar()
        claves = MAESTRAS.claves(UID)
        ndef = bytearray(chip(self.sim).leer_ndef())
        ndef[p424.OFFSET_M] = ord("0") if ndef[p424.OFFSET_M] != ord("0") else ord("1")
        with self.assertRaises(p424.ErrorPersonalizacion):
            p424.comprobar_sun(bytes(ndef), UID, claves[3], claves[4])

    def test_volver_a_personalizar_no_cambia_nada(self):
        self.personalizar()
        antes = huella(self.sim)
        self.personalizar()
        self.assertEqual(huella(self.sim)[:3], antes[:3])

    def test_tarjeta_de_otras_maestras(self):
        p424.personalizar(chip(self.sim), p424.Maestras.aleatorias(), silencio)
        with self.assertRaises(p424.ErrorPersonalizacion):
            self.personalizar()
        with self.assertRaises(p424.ErrorPersonalizacion):
            p424.restablecer(chip(self.sim), MAESTRAS, silencio)

    def test_version_de_clave_desconocida(self):
        self.sim.versiones = [0, 0, 7, 0, 0]
        with self.assertRaises(p424.ErrorPersonalizacion):
            self.personalizar()


class Restablecer(EntornoTarjetas):
    def setUp(self):
        super().setUp()
        self.sim = Simulador424(uid=UID)
        self.fabrica = huella(Simulador424(uid=UID))

    def test_vuelve_exactamente_a_fabrica(self):
        p424.personalizar(chip(self.sim), MAESTRAS, silencio)
        p424.restablecer(chip(self.sim), MAESTRAS, silencio)
        self.assertEqual(huella(self.sim), self.fabrica)
        self.assertEqual(ajustes(self.sim)[3], bytes.fromhex("00033023800000"))  # DS Tabla 8
        self.assertEqual(ajustes(self.sim)[2], bytes.fromhex("0000E0EE000100"))

    def test_borra_un_area_emitida(self):
        p424.personalizar(chip(self.sim), MAESTRAS, silencio)
        c = chip(self.sim)
        c.seleccionar_aplicacion()
        c.autenticar(2, MAESTRAS.claves(UID)[2])
        c.escribir_datos(3, 0, bytes([0xAB]) * 72)
        p424.restablecer(chip(self.sim), MAESTRAS, silencio)
        self.assertEqual(huella(self.sim), self.fabrica)

    def test_tarjeta_de_fabrica(self):
        p424.restablecer(chip(self.sim), MAESTRAS, silencio)
        self.assertEqual(huella(self.sim), self.fabrica)

    def test_personalizar_otra_vez(self):
        p424.personalizar(chip(self.sim), MAESTRAS, silencio)
        p424.restablecer(chip(self.sim), MAESTRAS, silencio)
        p424.personalizar(chip(self.sim), MAESTRAS, silencio)
        self.assertEqual(self.sim.versiones, [1] * 5)


class Idempotencia(EntornoTarjetas):
    """Criterio de #40: si se interrumpe a mitad, volver a ejecutarlo completa la configuración. Se corta en cada
    APDU de una ejecución completa, antes de que llegue y después de aplicarla."""

    def apdus(self, funcion, preparar=None):
        sim = Simulador424(uid=UID)
        if preparar:
            preparar(sim)
        corte = Corte(sim)
        funcion(chip(sim, corte), MAESTRAS, silencio)
        return corte.enviadas

    def cortes(self, funcion, preparar=None):
        total = self.apdus(funcion, preparar)
        self.assertGreater(total, 30)
        for en in range(total):
            for aplicada in (False, True):
                sim = Simulador424(uid=UID)
                if preparar:
                    preparar(sim)
                with self.assertRaises(ErrorTarjeta, msg=f"APDU {en}"):
                    funcion(chip(sim, Corte(sim, en, aplicada)), MAESTRAS, silencio)
                yield en, aplicada, sim

    def test_personalizar_cortada_en_cada_apdu(self):
        esperado = huella(_personalizada())
        for en, aplicada, sim in self.cortes(p424.personalizar):
            with self.subTest(apdu=en, aplicada=aplicada):
                p424.personalizar(chip(sim), MAESTRAS, silencio)
                self.assertEqual(huella(sim), esperado)

    def test_restablecer_cortado_en_cada_apdu(self):
        fabrica = huella(Simulador424(uid=UID))
        preparar = lambda sim: p424.personalizar(chip(sim), MAESTRAS, silencio)  # noqa: E731
        for en, aplicada, sim in self.cortes(p424.restablecer, preparar):
            with self.subTest(apdu=en, aplicada=aplicada):
                p424.restablecer(chip(sim), MAESTRAS, silencio)
                self.assertEqual(huella(sim), fabrica)

    def test_restablecer_una_personalizacion_a_medias(self):
        fabrica = huella(Simulador424(uid=UID))
        for en, aplicada, sim in self.cortes(p424.personalizar):
            with self.subTest(apdu=en, aplicada=aplicada):
                p424.restablecer(chip(sim), MAESTRAS, silencio)
                self.assertEqual(huella(sim), fabrica)


def _personalizada():
    sim = Simulador424(uid=UID)
    p424.personalizar(chip(sim), MAESTRAS, silencio)
    return sim


class Programa(EntornoTarjetas):
    def test_simulador_personaliza_y_restablece(self):
        salida, codigo = self.ejecutar(p424.main, "--simulador", "--restablecer", "--maestras", "/no/existe.json")
        self.assertIsNone(codigo)
        self.assertIn("Tarjeta personalizada", salida)
        self.assertIn("Tarjeta restablecida", salida)
        self.assertIn("maestras aleatorias", salida)

    def test_sin_maestras_no_toca_el_lector(self):
        _, codigo = self.ejecutar(p424.main, "--maestras", "/no/existe.json")
        self.assertIn("Falta /no/existe.json", codigo)

    def test_maestras_desde_archivo(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "maestras_424.json"
            ruta.write_text(json.dumps({"version": 1, **{f"k{n}": (bytes([0x10 + n]) * 16).hex() for n in range(5)}}))
            ruta.chmod(0o600)
            self.assertEqual(p424.Maestras.desde_archivo(ruta).claves(UID), MAESTRAS.claves(UID))
            salida, codigo = self.ejecutar(p424.main, "--simulador", "--maestras", str(ruta))
            self.assertIsNone(codigo)
            self.assertNotIn("aleatorias", salida)
            ruta.chmod(0o644)
            salida, _ = self.ejecutar(lambda: p424.Maestras.desde_archivo(ruta))
            self.assertIn("chmod 600", salida)

    def test_lector_con_corte_y_reintento(self):
        """El camino del ACR122U, con el simulador en lugar de la conexión PC/SC: un corte avisa de que se
        puede repetir, y la segunda ejecución termina."""
        from unittest import mock
        sim = Simulador424(uid=UID)
        cortes = [Corte(sim, en=20), sim.transmitir]

        def chip_pcsc(_conexion):
            sim.acercar()
            return NTAG424(cortes.pop(0), UID)

        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "m.json"
            ruta.write_text(json.dumps({f"k{n}": (bytes([0x10 + n]) * 16).hex() for n in range(5)}))
            ruta.chmod(0o600)
            with mock.patch("tarjeta.lector.esperar_conexion"), mock.patch.object(ntag424, "chip_pcsc", chip_pcsc):
                _, codigo = self.ejecutar(p424.main, "--maestras", str(ruta))
                self.assertIn("retoma donde quedó", codigo)
                salida, codigo = self.ejecutar(p424.main, "--maestras", str(ruta))
        self.assertIsNone(codigo)
        self.assertIn("1-2. Autenticada con la K0", salida)
        self.assertEqual(huella(sim), huella(_personalizada()))

    def test_maestras_invalidas(self):
        with self.assertRaises(ValueError):
            p424.Maestras({n: bytes(16) for n in range(4)})
        with self.assertRaises(ValueError):
            p424.Maestras({n: bytes(15) for n in range(5)})
        with self.assertRaises(ValueError):
            p424.Maestras({n: bytes(16) for n in range(5)}, version=0)


if __name__ == "__main__":
    unittest.main()
