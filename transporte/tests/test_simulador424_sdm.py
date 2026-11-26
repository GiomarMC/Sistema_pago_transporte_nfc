"""Simulador de la NTAG 424 DNA (M1-03, parte b): SDM/SUN del archivo 02.

La configuración de SubePe es la de docs/ntag424-personalizacion.md §8 con la decisión D de #203 (SDMMAC sobre
entrada vacía). El enlace se comprueba con cryptography directamente, sin las funciones del simulador, y con el
ejemplo oficial de AN12196 (§3.4.2.2 y §3.4.4.2.1) byte a byte.
"""

import os
import unittest

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.cmac import CMAC

from tarjeta import apdu424 as a
from tarjeta.simulador424 import Simulador424

from tests.lector424_prueba import FULL, PLAIN, autenticar, cabecera_datos, enviar, seleccionar_aplicacion

h = bytes.fromhex
UID = h("04B3E121CA2A81")
K0, K2, K3, K4 = h("10" * 16), h("22" * 16), h("33" * 16), h("44" * 16)   # solo pruebas

# Archivo 02 de SubePe (§8.1): 76 B, con 'p' y 'm' a '0' hasta que la tarjeta los rellena
PLANTILLA = (h("004A") + h("D1014655") + h("04") + b"mi.subepe.app/t?p=" + b"0" * 32 + b"&m=" + b"0" * 16)
OFFSET_P, OFFSET_M = 0x19, 0x3C
# ChangeFileSettings de SubePe: SDM + Plain, AccessRights 00E0, SDMOptions C1,
# SDMAccessRights RFU F | SDMCtrRet 2 · SDMMetaRead 3 | SDMFileRead 4, PICCDataOffset, SDMMACInputOffset, SDMMACOffset
AJUSTES_SUBEPE = h("4000E0C1F234") + (0x19).to_bytes(3, "little") + (0x3C).to_bytes(3, "little") \
    + (0x3C).to_bytes(3, "little")


def _cmac(clave, datos):
    calculo = CMAC(algorithms.AES(clave))
    calculo.update(datos)
    return calculo.finalize()


def descifrar_p(k3, p):
    descifrador = Cipher(algorithms.AES(k3), modes.CBC(bytes(16))).decryptor()
    claro = descifrador.update(p) + descifrador.finalize()
    return claro[0], claro[1:8], int.from_bytes(claro[8:11], "little")


def mac_esperado(k4, uid, contador, entrada=b""):
    sesion = _cmac(k4, h("3CC300010080") + uid + contador.to_bytes(3, "little"))
    return _cmac(sesion, entrada)[1::2]


def rng_con_relleno(relleno):
    return lambda n: relleno if n == 5 else os.urandom(n)


class Base(unittest.TestCase):
    def tarjeta_subepe(self, **kwargs):
        """Tarjeta con las claves de prueba, la plantilla y el SDM de SubePe."""
        tarjeta = Simulador424(uid=UID, **kwargs)
        seleccionar_aplicacion(tarjeta)
        tarjeta.claves = [K0, bytes(16), K2, K3, K4]
        _, sw, _ = enviar(tarjeta, None, a.INS_WRITE_DATA, cabecera_datos(2, 0, len(PLANTILLA)), PLANTILLA)
        self.assertEqual(sw, 0x9100)
        self.cambiar_ajustes(tarjeta, AJUSTES_SUBEPE)
        return tarjeta

    def cambiar_ajustes(self, tarjeta, ajustes, sw_esperado=0x9100):
        sesion = autenticar(tarjeta, 0, tarjeta.claves[0])
        _, sw, _ = enviar(tarjeta, sesion, a.INS_CHANGE_FILE_SETTINGS, b"\x02", ajustes, FULL)
        self.assertEqual(sw, sw_esperado, f"ChangeFileSettings: SW {sw:04X}")
        tarjeta.acercar()
        seleccionar_aplicacion(tarjeta)

    def leer_como_celular(self, tarjeta, longitud=len(PLANTILLA)):
        """Lo que hace un celular al acercarlo: seleccionar la aplicación y el archivo NDEF y leerlo."""
        seleccionar_aplicacion(tarjeta)
        self.assertEqual(a.separar_sw(tarjeta.transmitir(h("00A4000002E104")))[1], 0x9000)
        return self.leer_binario(tarjeta, 0, longitud)

    def leer_binario(self, tarjeta, offset, longitud):
        datos, sw = a.separar_sw(tarjeta.transmitir(h("00B0") + offset.to_bytes(2, "big") + bytes([longitud])))
        self.assertEqual(sw, 0x9000, f"ISOReadBinary: SW {sw:04X}")
        return datos

    @staticmethod
    def p_y_m(contenido):
        return h(contenido[OFFSET_P:OFFSET_P + 32].decode()), h(contenido[OFFSET_M:OFFSET_M + 16].decode())


class VectorOficialTests(Base):
    def test_an12196_picc_data_y_sdmmac_byte_a_byte(self):
        # AN12196 §3.4.2.2 y §3.4.4.2.1: K_SDMMetaRead = K_SDMFileRead = 00…00, UID 04DE5F1EACC040, contador 61
        tarjeta = Simulador424(uid=h("04DE5F1EACC040"), rng=rng_con_relleno(h("DA5CF60941")))
        seleccionar_aplicacion(tarjeta)
        # El ChangeFileSettings de AN12196 Tabla 18: PICCData en 20h y SDMMAC en 43h sobre entrada vacía
        self.cambiar_ajustes(tarjeta, h("4000E0C1F121200000430000430000"))
        tarjeta.contador_sdm = 60
        contenido = self.leer_como_celular(tarjeta, 0x53)
        self.assertEqual(contenido[0x20:0x40], b"EF963FF7828658A599F3041510671E88")
        self.assertEqual(contenido[0x43:0x53], b"94EED9EE65337086")
        self.assertEqual(tarjeta.contador_sdm, 61)

    def test_get_file_settings_devuelve_los_campos_sdm(self):
        tarjeta = self.tarjeta_subepe()
        respuesta, sw, _ = enviar(tarjeta, None, a.INS_GET_FILE_SETTINGS, b"\x02")
        self.assertEqual(sw, 0x9100)
        self.assertEqual(respuesta, h("004000E0") + (256).to_bytes(3, "little") + AJUSTES_SUBEPE[3:])


class SunDeSubePeTests(Base):
    def test_el_celular_lee_un_enlace_valido(self):
        contenido = self.leer_como_celular(self.tarjeta_subepe())
        self.assertTrue(contenido.startswith(h("004AD1014655") + b"\x04mi.subepe.app/t?p="))
        self.assertEqual(contenido[OFFSET_P + 32:OFFSET_M], b"&m=")
        p, m = self.p_y_m(contenido)
        etiqueta, uid, contador = descifrar_p(K3, p)
        self.assertEqual((etiqueta, uid, contador), (0xC7, UID, 1))
        self.assertEqual(m, mac_esperado(K4, UID, 1))

    def test_el_enlace_va_en_hexadecimal_mayusculas(self):
        contenido = self.leer_como_celular(self.tarjeta_subepe())
        texto = contenido[OFFSET_P:OFFSET_P + 32] + contenido[OFFSET_M:OFFSET_M + 16]
        self.assertTrue(all(c in b"0123456789ABCDEF" for c in texto))

    def test_read_data_sin_autenticar_tambien_lleva_el_sun(self):
        tarjeta = self.tarjeta_subepe()
        contenido, sw, _ = enviar(tarjeta, None, a.INS_READ_DATA, cabecera_datos(2, 0, len(PLANTILLA)))
        self.assertEqual(sw, 0x9100)
        p, m = self.p_y_m(contenido)
        self.assertEqual(descifrar_p(K3, p)[2], 1)
        self.assertEqual(m, mac_esperado(K4, UID, 1))

    def test_el_contador_sube_una_vez_por_seleccion(self):
        tarjeta = self.tarjeta_subepe()
        primera = self.leer_como_celular(tarjeta)
        # El celular lee por partes en el mismo toque: mismo espejo, mismo contador
        self.assertEqual(self.leer_binario(tarjeta, 0, 2) + self.leer_binario(tarjeta, 2, 74), primera)
        self.assertEqual(tarjeta.contador_sdm, 1)
        segunda = self.leer_como_celular(tarjeta)
        self.assertEqual(tarjeta.contador_sdm, 2)
        self.assertNotEqual(self.p_y_m(segunda), self.p_y_m(primera))
        tarjeta.acercar()
        p, m = self.p_y_m(self.leer_como_celular(tarjeta))
        self.assertEqual(descifrar_p(K3, p)[2], 3)
        self.assertEqual(m, mac_esperado(K4, UID, 3))

    def test_activar_sdm_reinicia_el_contador(self):
        tarjeta = Simulador424(uid=UID)
        seleccionar_aplicacion(tarjeta)
        tarjeta.claves = [K0, bytes(16), K2, K3, K4]
        tarjeta.contador_sdm = 41
        self.cambiar_ajustes(tarjeta, AJUSTES_SUBEPE)
        self.assertEqual(tarjeta.contador_sdm, 0)
        self.leer_como_celular(tarjeta)
        self.assertEqual(tarjeta.contador_sdm, 1)
        # Volver a dar los mismos ajustes con SDM ya activo no lo reinicia
        self.cambiar_ajustes(tarjeta, AJUSTES_SUBEPE)
        self.assertEqual(tarjeta.contador_sdm, 1)

    def test_con_sesion_se_lee_lo_grabado_sin_gastar_el_contador(self):
        tarjeta = self.tarjeta_subepe()
        sesion = autenticar(tarjeta, 0, K0)
        contenido, sw, _ = enviar(tarjeta, sesion, a.INS_READ_DATA, cabecera_datos(2, 0, len(PLANTILLA)), b"", PLAIN)
        self.assertEqual((sw, contenido), (0x9100, PLANTILLA))
        self.assertEqual(tarjeta.contador_sdm, 0)

    def test_desactivar_sdm_deja_el_archivo_en_claro(self):
        tarjeta = self.tarjeta_subepe()
        self.cambiar_ajustes(tarjeta, h("0000E0"))
        self.assertEqual(self.leer_como_celular(tarjeta), PLANTILLA)
        self.assertIsNone(tarjeta.archivos[2].sdm)


class GetFileCountersTests(Base):
    def test_con_la_clave_sdmctrret_en_full(self):
        tarjeta = self.tarjeta_subepe()
        self.leer_como_celular(tarjeta)
        self.leer_como_celular(tarjeta)
        seleccionar_aplicacion(tarjeta)
        sesion = autenticar(tarjeta, 2, K2)
        respuesta, sw, _ = enviar(tarjeta, sesion, a.INS_GET_FILE_COUNTERS, b"\x02", b"", FULL)
        self.assertEqual((sw, respuesta), (0x9100, (2).to_bytes(3, "little") + bytes(2)))

    def test_sin_autenticar_o_con_otra_clave(self):
        tarjeta = self.tarjeta_subepe()
        _, sw, _ = enviar(tarjeta, None, a.INS_GET_FILE_COUNTERS, b"\x02")
        self.assertEqual(sw, 0x91AE)
        sesion = autenticar(tarjeta, 0, K0)
        _, sw, _ = enviar(tarjeta, sesion, a.INS_GET_FILE_COUNTERS, b"\x02", b"", FULL)
        self.assertEqual(sw, 0x91AE)

    def test_archivo_sin_sdm(self):
        tarjeta = Simulador424()
        seleccionar_aplicacion(tarjeta)
        _, sw, _ = enviar(tarjeta, None, a.INS_GET_FILE_COUNTERS, b"\x02")
        self.assertEqual(sw, 0x919D)


class ErroresTests(Base):
    def test_ajustes_sdm_invalidos(self):
        def offsets(picc, entrada, mac):
            return b"".join(x.to_bytes(3, "little") for x in (picc, entrada, mac))
        casos = {
            "sin modo ASCII": (h("4000E0C0F234") + offsets(0x19, 0x3C, 0x3C), 0x919E),
            "bits RFU en SDMOptions": (h("4000E0C3F234") + offsets(0x19, 0x3C, 0x3C), 0x919E),
            "clave 5 inexistente": (h("4000E0C1F254") + offsets(0x19, 0x3C, 0x3C), 0x919E),
            "PICCData y SDMMAC se pisan": (h("4000E0C1F234") + offsets(0x19, 0x20, 0x20), 0x919E),
            "SDMMAC pasa del final": (h("4000E0C1F234") + offsets(0x19, 0xF8, 0xF8), 0x919E),
            "entrada del MAC tras el MAC": (h("4000E0C1F234") + offsets(0x19, 0x40, 0x3C), 0x919E),
            "falta un offset": (h("4000E0C1F234") + offsets(0x19, 0x3C, 0x3C)[:-3], 0x917E),
        }
        for nombre, (ajustes, sw) in casos.items():
            with self.subTest(nombre):
                tarjeta = Simulador424(uid=UID)
                seleccionar_aplicacion(tarjeta)
                tarjeta.claves = [K0, bytes(16), K2, K3, K4]
                self.cambiar_ajustes(tarjeta, ajustes, sw_esperado=sw)
                self.assertIsNone(tarjeta.archivos[2].sdm)

    def test_sdm_en_otro_archivo_no_se_emula(self):
        tarjeta = Simulador424()
        seleccionar_aplicacion(tarjeta)
        sesion = autenticar(tarjeta, 0, bytes(16))
        with self.assertRaises(NotImplementedError):
            enviar(tarjeta, sesion, a.INS_CHANGE_FILE_SETTINGS, b"\x03", AJUSTES_SUBEPE, FULL)

    def test_iso_read_binary(self):
        tarjeta = Simulador424()
        seleccionar_aplicacion(tarjeta)
        sw = lambda apdu: a.separar_sw(tarjeta.transmitir(h(apdu)))[1]  # noqa: E731
        self.assertEqual(sw("00B0000010"), 0x6986)              # ningún archivo seleccionado
        self.assertEqual(sw("00A4000002E106"), 0x6A82)          # FileID inexistente
        self.assertEqual(sw("00A4000002E105"), 0x9000)
        self.assertEqual(sw("00B0000010"), 0x6982)              # archivo 03: lectura con K2
        self.assertEqual(sw("00A4000002E104"), 0x9000)
        self.assertEqual(sw("00B0010000"), 0x6B00)              # offset fuera del archivo
        self.assertEqual(sw("00B000F020"), 0x6B00)              # pasa del final
        self.assertEqual(sw("00B0000010"), 0x9000)

    def test_seleccionar_por_file_id_exige_la_aplicacion(self):
        tarjeta = Simulador424()
        self.assertEqual(a.separar_sw(tarjeta.transmitir(h("00A4000002E104")))[1], 0x6A82)


if __name__ == "__main__":
    unittest.main()
