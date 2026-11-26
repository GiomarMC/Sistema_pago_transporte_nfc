"""#42 (M1-08): la página /t con enlaces generados por el simulador de la NTAG 424 (transporte/, M1-03).

Se personaliza el simulador como pide docs/ntag424-personalizacion.md §8 (plantilla NDEF de 76 B, K3 común, K4
diversificada, SDMMAC sobre entrada vacía), se lee como lo haría un celular (ISOSelectFile + ISOReadBinary) y la URL
que sale se abre en el portal. Los programas de PC no están en la imagen de Docker: sin transporte/ se omiten.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings

from transporte.models import Cuenta, Tarifa, Tarjeta
from transporte.tests import tarjeta_sun

TRANSPORTE = Path(settings.BASE_DIR).parent / "transporte"
HAY_SIMULADOR = (TRANSPORTE / "tarjeta" / "simulador424.py").exists()

K0, K3, MAESTRA_K4 = bytes.fromhex("10" * 16), bytes.fromhex("33" * 16), bytes.fromhex("44" * 16)
UID = bytes.fromhex("04B3E121CA2A81")
PLANTILLA = (bytes.fromhex("004AD1014655") + b"\x04" + b"mi.subepe.app/t?p=" + b"0" * 32 + b"&m=" + b"0" * 16)
# 40 00E0 C1 · SDMAccessRights F? | MetaRead 3, FileRead 4 · PICCData 19h · SDMMACInput = SDMMAC = 3Ch
AJUSTES = bytes.fromhex("4000E0C1F234" "190000" "3C0000" "3C0000")


def _cargar():
    if str(TRANSPORTE) not in sys.path:
        sys.path.append(str(TRANSPORTE))  # al final: no tapa ningún módulo del servidor
    from tarjeta import apdu424
    from tarjeta.simulador424 import Simulador424
    spec = importlib.util.spec_from_file_location("lector424_prueba", TRANSPORTE / "tests" / "lector424_prueba.py")
    lector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lector)
    return apdu424, Simulador424, lector


@unittest.skipUnless(HAY_SIMULADOR, "sin transporte/ (imagen de Docker)")
@override_settings(PORTAL_EN_RUTA_MI=True, SUN_K3=K3.hex(), SUN_K4_MAESTRAS="1:" + MAESTRA_K4.hex())
class EnlacesDelSimuladorTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.a, cls.Simulador424, cls.lector = _cargar()

    def setUp(self):
        tarifa = Tarifa.objects.get_or_create(codigo=1, defaults={"nombre": "General", "precio": 100})[0]
        cuenta = Cuenta.objects.create(nombre="Ana Pérez", documento="45678912", tarifa=tarifa, saldo=1234)
        self.tarjeta = Tarjeta.objects.create(uid=UID.hex().upper(), cuenta=cuenta, estado=Tarjeta.Estado.ACTIVA)
        self.nfc = self.personalizar()

    def personalizar(self, uid=UID):
        """La parte de M1-06 que toca el SUN: claves, plantilla y ChangeFileSettings con SDM."""
        a, lector = self.a, self.lector
        nfc = self.Simulador424(uid=uid)
        lector.seleccionar_aplicacion(nfc)
        nfc.claves = [K0, bytes(16), bytes(16), K3, tarjeta_sun.k4_de_tarjeta(MAESTRA_K4, uid)]
        _, sw, _ = lector.enviar(nfc, None, a.INS_WRITE_DATA, lector.cabecera_datos(2, 0, len(PLANTILLA)), PLANTILLA)
        self.assertEqual(sw, 0x9100)
        sesion = lector.autenticar(nfc, 0, K0)
        _, sw, _ = lector.enviar(nfc, sesion, a.INS_CHANGE_FILE_SETTINGS, b"\x02", AJUSTES, lector.FULL)
        self.assertEqual(sw, 0x9100)
        nfc.acercar()
        return nfc

    def acercar_celular(self, nfc=None):
        """URL que abre el celular: lee el NDEF del archivo 02 y arma la dirección."""
        nfc = nfc or self.nfc
        nfc.acercar()
        self.lector.seleccionar_aplicacion(nfc)
        for apdu in ("00A4000002E104", "00B0000000"):
            respuesta = nfc.transmitir(bytes.fromhex(apdu))
            self.assertEqual(respuesta[-2:], b"\x90\x00", apdu)
        ndef = respuesta[:-2]
        self.assertEqual(ndef[6], 0x04)  # prefijo https://
        url = ndef[7:2 + int.from_bytes(ndef[:2], "big")].decode()
        self.assertTrue(url.startswith("mi.subepe.app/t?p="), url)
        return "/mi/" + url[len("mi.subepe.app/"):]

    def assertAutentica(self, respuesta):
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Tarjeta SubePe auténtica")

    def assertRechazada(self, respuesta):
        self.assertEqual(respuesta.status_code, 400)
        self.assertContains(respuesta, "Enlace no válido", status_code=400)

    def test_url_valida(self):
        self.assertAutentica(self.client.get(self.acercar_celular()))
        self.tarjeta.refresh_from_db()
        self.assertEqual(self.tarjeta.contador_sdm, 1)

    def test_cada_toque_da_un_enlace_nuevo_y_valido(self):
        for contador in (1, 2, 3):
            with self.subTest(toque=contador):
                self.assertAutentica(self.client.get(self.acercar_celular()))
        self.tarjeta.refresh_from_db()
        self.assertEqual(self.tarjeta.contador_sdm, 3)

    def test_contador_repetido(self):
        url = self.acercar_celular()
        self.assertAutentica(self.client.get(url))
        self.assertRechazada(self.client.get(url))  # el mismo enlace, otra vez

    def test_enlace_viejo_tras_uno_nuevo(self):
        viejo = self.acercar_celular()
        self.assertAutentica(self.client.get(self.acercar_celular()))
        self.assertRechazada(self.client.get(viejo))

    def test_cmac_alterado(self):
        url = self.acercar_celular()
        ultimo = url[-1]
        alterado = url[:-1] + ("0" if ultimo != "0" else "1")
        self.assertRechazada(self.client.get(alterado))
        self.tarjeta.refresh_from_db()
        self.assertEqual(self.tarjeta.contador_sdm, 0)

    def test_tarjeta_inexistente(self):
        otra = self.personalizar(uid=bytes.fromhex("04112233445566"))
        self.assertRechazada(self.client.get(self.acercar_celular(otra)))

    def test_tarjeta_bloqueada(self):
        self.tarjeta.estado = Tarjeta.Estado.BLOQUEADA
        self.tarjeta.save(update_fields=["estado"])
        respuesta = self.client.get(self.acercar_celular())
        self.assertAutentica(respuesta)
        self.assertContains(respuesta, "Bloqueada")
