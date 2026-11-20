"""Simulador de la NTAG 424 DNA (M1-03, parte a): EV2, mensajería segura y accesos."""

import unittest

from tarjeta import apdu424 as a
from tarjeta import ev2
from tarjeta.simulador424 import Simulador424

from tests.lector424_prueba import (FULL, MAC, PLAIN, autenticar, cabecera_datos, enviar,
                                    seleccionar_aplicacion)
from tests.test_ev2 import (E_RESPUESTA_AUTH, E_RND_A_RND_B, E_RND_B, ENC_T25, MAC_T25, RND_B,
                            TI_T14, TI_T25)

h = bytes.fromhex
CLAVES_PRUEBA = [bytes([0x10 * (i + 1)]) * 16 for i in range(5)]  # solo pruebas


def rng_fijo(*valores):
    pendientes = list(valores)

    def rng(n):
        valor = pendientes.pop(0)
        assert len(valor) == n
        return valor
    return rng


class Base(unittest.TestCase):
    def nueva(self, **kwargs):
        tarjeta = Simulador424(**kwargs)
        seleccionar_aplicacion(tarjeta)
        return tarjeta

    def con_claves_de_prueba(self):
        tarjeta = self.nueva()
        tarjeta.claves = list(CLAVES_PRUEBA)
        return tarjeta

    def sw(self, respuesta):
        return a.separar_sw(respuesta)[1]


class VectoresOficialesTests(Base):
    def test_tabla_14_authenticate_ev2_first_byte_a_byte(self):
        tarjeta = self.nueva(rng=rng_fijo(RND_B, TI_T14))
        self.assertEqual(tarjeta.transmitir(h("9071000002000000")), E_RND_B + h("91AF"))
        respuesta = tarjeta.transmitir(h("90AF000020") + E_RND_A_RND_B + h("00"))
        self.assertEqual(respuesta, E_RESPUESTA_AUTH + h("9100"))
        self.assertEqual(tarjeta.sesion.ti, TI_T14)

    def test_tabla_25_change_key_k2(self):
        tarjeta = self.nueva()
        tarjeta.con_sesion(ev2.Sesion(ENC_T25, MAC_T25, TI_T25, cmd_ctr=2), clave=0)
        apdu = h("90C4000029022CF362B7BF4311FF3BE1DAA295E8C68DE09050560D19B9"
                 "E16C2393AE9CD1FAC75D0CE20BCD1D06E600")
        self.assertEqual(tarjeta.transmitir(apdu), h("203BB55D1089D5879100"))
        self.assertEqual(tarjeta.claves[2], h("F3847D627727ED3BC9C4CC050489B966"))
        self.assertEqual(tarjeta.versiones[2], 1)
        self.assertEqual(tarjeta.sesion.cmd_ctr, 3)

    def test_tabla_26_change_key_de_la_clave_de_la_sesion(self):
        tarjeta = self.nueva()
        tarjeta.con_sesion(ev2.Sesion(ENC_T25, MAC_T25, TI_T25, cmd_ctr=3), clave=0)
        apdu = h("90C400002900C0EB4DEEFEDDF0B513A03A95A75491818580503190D4D05053"
                 "FF75668A01D6FDA6610234BDED643200")
        self.assertEqual(tarjeta.transmitir(apdu), h("9100"))  # sin MAC
        self.assertEqual(tarjeta.claves[0], h("5004BF991F408672B1EF00F08F9E8647"))
        self.assertIsNone(tarjeta.sesion)

    def test_get_file_settings_de_fabrica(self):
        tarjeta = self.nueva()
        esperado = {1: "000000E0200000", 2: "0000E0EE000100", 3: "00033023800000"}
        for archivo, valor in esperado.items():
            datos, sw, _ = enviar(tarjeta, None, a.INS_GET_FILE_SETTINGS, bytes([archivo]))
            self.assertEqual((datos.hex().upper(), sw), (valor, 0x9100), archivo)


class AccesoArchivo03Tests(Base):
    """Archivo 03 de fábrica: Read = K2, Write = K3, ReadWrite = K3, Change = K0, Full."""

    def test_leer_sin_autenticar_da_authentication_error(self):
        tarjeta = self.nueva()
        _, sw, _ = enviar(tarjeta, None, a.INS_READ_DATA, cabecera_datos(3, 0, 72))
        self.assertEqual(sw, 0x91AE)

    def test_leer_con_k1_cuando_se_requiere_k2_da_authentication_error(self):
        tarjeta = self.nueva()
        sesion = autenticar(tarjeta, 1, bytes(16))
        _, sw, _ = enviar(tarjeta, sesion, a.INS_READ_DATA, cabecera_datos(3, 0, 72), modo=FULL)
        self.assertEqual(sw, 0x91AE)
        self.assertIsNone(tarjeta.sesion)  # el error corta la autenticación

    def test_leer_con_k2_en_full(self):
        tarjeta = self.nueva()
        tarjeta.archivos[3].datos[:4] = b"SUBE"
        sesion = autenticar(tarjeta, 2, bytes(16))
        datos, sw, _ = enviar(tarjeta, sesion, a.INS_READ_DATA, cabecera_datos(3, 0, 72), modo=FULL)
        self.assertEqual((sw, datos), (0x9100, b"SUBE" + bytes(68)))

    def test_escribir_con_k3_y_leer_con_k2(self):
        tarjeta = self.con_claves_de_prueba()
        registro = bytes(range(20))
        sesion = autenticar(tarjeta, 3, CLAVES_PRUEBA[3])
        _, sw, _ = enviar(tarjeta, sesion, a.INS_WRITE_DATA, cabecera_datos(3, 32, 20), registro, FULL)
        self.assertEqual(sw, 0x9100)
        sesion = autenticar(tarjeta, 2, CLAVES_PRUEBA[2])
        datos, _, _ = enviar(tarjeta, sesion, a.INS_READ_DATA, cabecera_datos(3, 32, 20), modo=FULL)
        self.assertEqual(datos, registro)

    def test_escribir_con_k2_da_authentication_error(self):
        tarjeta = self.nueva()
        sesion = autenticar(tarjeta, 2, bytes(16))
        _, sw, _ = enviar(tarjeta, sesion, a.INS_WRITE_DATA, cabecera_datos(3, 0, 4), b"ABCD", FULL)
        self.assertEqual(sw, 0x91AE)
        self.assertEqual(bytes(tarjeta.archivos[3].datos[:4]), bytes(4))

    def test_change_file_settings_con_k0(self):
        # Derechos arbitrarios de prueba (no son la decisión B, aún pendiente)
        tarjeta = self.con_claves_de_prueba()
        sesion = autenticar(tarjeta, 0, CLAVES_PRUEBA[0])
        nuevos = (0x1 << 12) | (0x2 << 8) | (0xF << 4) | 0x0
        _, sw, _ = enviar(tarjeta, sesion, a.INS_CHANGE_FILE_SETTINGS, b"\x03",
                          b"\x03" + nuevos.to_bytes(2, "little"), FULL)
        self.assertEqual(sw, 0x9100)
        sesion = autenticar(tarjeta, 1, CLAVES_PRUEBA[1])
        _, sw, _ = enviar(tarjeta, sesion, a.INS_READ_DATA, cabecera_datos(3, 0, 8), modo=FULL)
        self.assertEqual(sw, 0x9100)

    def test_change_file_settings_va_en_full_aunque_el_archivo_sea_plain(self):
        tarjeta = self.nueva()
        sesion = autenticar(tarjeta, 0, bytes(16))
        nuevos = (0x0 << 12) | (0x0 << 8) | (0x0 << 4) | 0x0  # archivo 02 solo con K0 (prueba)
        _, sw, sesion = enviar(tarjeta, sesion, a.INS_CHANGE_FILE_SETTINGS, b"\x02",
                               b"\x03" + nuevos.to_bytes(2, "little"), FULL)
        self.assertEqual(sw, 0x9100)
        self.assertEqual(tarjeta.archivos[2].derechos, 0)
        # Leer los 256 B en Full no cabe en una respuesta corta: no se emula el encadenamiento
        with self.assertRaises(NotImplementedError):
            enviar(tarjeta, sesion, a.INS_READ_DATA, cabecera_datos(2, 0, 0), modo=FULL)

    def test_change_file_settings_rechaza_claves_inexistentes(self):
        tarjeta = self.nueva()
        sesion = autenticar(tarjeta, 0, bytes(16))
        _, sw, _ = enviar(tarjeta, sesion, a.INS_CHANGE_FILE_SETTINGS, b"\x03",
                          b"\x03" + (0x5230).to_bytes(2, "little"), FULL)
        self.assertEqual(sw, 0x919E)

    def test_apdu_mal_formada_o_comando_desconocido_cortan_la_sesion(self):
        for apdu in (h("90AD0000050300"), a.nativo(0x51)):
            with self.subTest(apdu=apdu.hex()):
                tarjeta = self.nueva()
                autenticar(tarjeta, 2, bytes(16))
                tarjeta.transmitir(apdu)
                self.assertIsNone(tarjeta.sesion)

    def test_change_file_settings_sin_la_clave_de_cambio(self):
        tarjeta = self.nueva()
        sesion = autenticar(tarjeta, 2, bytes(16))
        _, sw, _ = enviar(tarjeta, sesion, a.INS_CHANGE_FILE_SETTINGS, b"\x03", b"\x00\x00\xE0", FULL)
        self.assertEqual(sw, 0x91AE)


class MensajeriaTests(Base):
    def test_cmdctr_sincronizado_en_varios_comandos_y_modos(self):
        tarjeta = self.nueva()
        sesion = autenticar(tarjeta, 2, bytes(16))
        pasos = [(a.INS_GET_FILE_SETTINGS, b"\x03", b"", MAC),
                 (a.INS_READ_DATA, cabecera_datos(2, 0, 8), b"", PLAIN),  # libre: Plain
                 (a.INS_READ_DATA, cabecera_datos(3, 0, 16), b"", FULL),
                 (a.INS_GET_FILE_SETTINGS, b"\x02", b"", MAC),
                 (a.INS_READ_DATA, cabecera_datos(3, 16, 32), b"", FULL)]
        for ins, cabecera, datos, modo in pasos:
            _, sw, sesion = enviar(tarjeta, sesion, ins, cabecera, datos, modo)
            self.assertEqual(sw, 0x9100, hex(ins))
        self.assertEqual((sesion.cmd_ctr, tarjeta.sesion.cmd_ctr), (5, 5))

    def test_mac_alterado_da_integrity_error_y_pierde_la_sesion(self):
        tarjeta = self.nueva()
        sesion = autenticar(tarjeta, 2, bytes(16))
        cabecera = cabecera_datos(3, 0, 8)
        mac = bytearray(sesion.mac_comando(a.INS_READ_DATA, cabecera))
        mac[0] ^= 1
        self.assertEqual(self.sw(tarjeta.transmitir(a.nativo(a.INS_READ_DATA, cabecera + mac))), 0x911E)
        _, sw, _ = enviar(tarjeta, sesion, a.INS_READ_DATA, cabecera, modo=FULL)
        self.assertEqual(sw, 0x91AE)

    def test_relleno_invalido_da_integrity_error(self):
        tarjeta = self.con_claves_de_prueba()
        sesion = autenticar(tarjeta, 3, CLAVES_PRUEBA[3])
        cabecera = cabecera_datos(3, 0, 4)
        cifrado = ev2.aes_cbc_cifrar(sesion.clave_enc, sesion._iv(h("A55A")), bytes(16))  # sin 80h
        mac = sesion.mac_comando(a.INS_WRITE_DATA, cabecera, cifrado)
        self.assertEqual(self.sw(tarjeta.transmitir(a.nativo(a.INS_WRITE_DATA, cabecera + cifrado + mac))),
                         0x911E)

    def test_cmdctr_agotado(self):
        tarjeta = self.nueva()
        sesion = autenticar(tarjeta, 2, bytes(16))
        tarjeta.sesion = ev2.Sesion(sesion.clave_enc, sesion.clave_mac, sesion.ti, ev2.CMDCTR_MAXIMO)
        agotada = tarjeta.sesion
        _, sw, _ = enviar(tarjeta, agotada, a.INS_GET_FILE_SETTINGS, b"\x03", modo=MAC)
        self.assertEqual(sw, 0x911E)


class ErroresTests(Base):
    def test_limites_archivo_y_longitud(self):
        tarjeta = self.nueva()
        sesion = autenticar(tarjeta, 2, bytes(16))
        _, sw, _ = enviar(tarjeta, sesion, a.INS_READ_DATA, cabecera_datos(3, 120, 16), modo=FULL)
        self.assertEqual(sw, 0x919E)  # ReadData fuera de rango: DS Tabla 80
        _, sw, _ = enviar(tarjeta, None, a.INS_WRITE_DATA, cabecera_datos(2, 250, 8), bytes(8))
        self.assertEqual(sw, 0x91BE)  # WriteData más allá del final: DS Tabla 83
        _, sw, _ = enviar(tarjeta, None, a.INS_WRITE_DATA, cabecera_datos(2, 0, 0))
        self.assertEqual(sw, 0x919E)  # WriteData exige Length >= 1
        _, sw, _ = enviar(tarjeta, None, a.INS_READ_DATA, cabecera_datos(7, 0, 1))
        self.assertEqual(sw, 0x91F0)
        _, sw, _ = enviar(tarjeta, None, a.INS_WRITE_DATA, cabecera_datos(2, 0, 4), b"AB")
        self.assertEqual(sw, 0x917E)

    def test_sin_seleccionar_la_aplicacion(self):
        tarjeta = Simulador424()
        _, sw, _ = enviar(tarjeta, None, a.INS_READ_DATA, cabecera_datos(2, 0, 4))
        self.assertEqual(sw, 0x919D)

    def test_autenticacion_con_rndb_incorrecto_e_interrumpida(self):
        tarjeta = self.nueva()
        tarjeta.transmitir(a.nativo(a.INS_AUTH_EV2_FIRST, b"\x00\x00"))
        self.assertEqual(self.sw(tarjeta.transmitir(a.nativo(a.INS_FRAME_ADICIONAL, bytes(32)))), 0x91AE)
        tarjeta.transmitir(a.nativo(a.INS_AUTH_EV2_FIRST, b"\x00\x00"))
        self.assertEqual(self.sw(tarjeta.transmitir(a.nativo(a.INS_GET_FILE_SETTINGS, b"\x02"))), 0x91CA)

    def test_clave_inexistente(self):
        tarjeta = self.nueva()
        self.assertEqual(self.sw(tarjeta.transmitir(a.nativo(a.INS_AUTH_EV2_FIRST, b"\x05\x00"))), 0x9140)

    def test_change_key_exige_k0(self):
        tarjeta = self.nueva()
        sesion = autenticar(tarjeta, 2, bytes(16))
        datos = ev2.datos_change_key(1, bytes([1]) * 16, bytes(16), 1)
        _, sw, _ = enviar(tarjeta, sesion, a.INS_CHANGE_KEY, b"\x01", datos, FULL)
        self.assertEqual(sw, 0x91AE)

    def test_change_key_con_crc_incorrecto(self):
        tarjeta = self.nueva()
        sesion = autenticar(tarjeta, 0, bytes(16))
        datos = bytearray(ev2.datos_change_key(1, bytes([1]) * 16, bytes(16), 1))
        datos[-1] ^= 0xFF
        _, sw, _ = enviar(tarjeta, sesion, a.INS_CHANGE_KEY, b"\x01", bytes(datos), FULL)
        self.assertEqual(sw, 0x911E)
        self.assertEqual(tarjeta.claves[1], bytes(16))

    def test_change_key_y_autenticacion_con_la_clave_nueva(self):
        tarjeta = self.nueva()
        sesion = autenticar(tarjeta, 0, bytes(16))
        nueva = bytes([0xA5]) * 16
        datos = ev2.datos_change_key(4, nueva, bytes(16), 7)
        _, sw, _ = enviar(tarjeta, sesion, a.INS_CHANGE_KEY, b"\x04", datos, FULL)
        self.assertEqual((sw, tarjeta.versiones[4]), (0x9100, 7))
        autenticar(tarjeta, 4, nueva)
        with self.assertRaises(AssertionError):
            autenticar(tarjeta, 4, bytes(16))


class ArchivoLibreYVersionTests(Base):
    def test_ndef_libre_sin_autenticar(self):
        tarjeta = self.nueva()
        _, sw, _ = enviar(tarjeta, None, a.INS_WRITE_DATA, cabecera_datos(2, 0, 4), b"\x00\x4A\xD1\x01")
        datos, sw2, _ = enviar(tarjeta, None, a.INS_READ_DATA, cabecera_datos(2, 0, 4))
        self.assertEqual((sw, sw2, datos), (0x9100, 0x9100, b"\x00\x4A\xD1\x01"))

    def test_get_version_en_tres_tramas(self):
        tarjeta = Simulador424(uid=h("04968CAA5C5E80"))
        tramas = [tarjeta.transmitir(h("9060000000"))]
        tramas += [tarjeta.transmitir(h("90AF000000")) for _ in range(2)]
        self.assertEqual(tramas[0], h("0404083000110591AF"))
        self.assertEqual(tramas[1], h("0404020101110591AF"))
        self.assertEqual(tramas[2], h("04968CAA5C5E80CD65935D4021189100"))  # AN12196 Tabla 12


class TearingTests(Base):
    def test_trama_cortada_no_escribe_nada(self):
        tarjeta = self.con_claves_de_prueba()
        sesion = autenticar(tarjeta, 3, CLAVES_PRUEBA[3])
        tarjeta.cortar_en_byte = 20
        with self.assertRaises(a.TarjetaRetirada):
            enviar(tarjeta, sesion, a.INS_WRITE_DATA, cabecera_datos(3, 32, 20), bytes([7]) * 20, FULL)
        self.assertEqual(bytes(tarjeta.archivos[3].datos[32:52]), bytes(20))
        with self.assertRaises(a.TarjetaRetirada):
            tarjeta.transmitir(a.seleccionar_df(a.DF_APLICACION_NDEF))
        tarjeta.cortar_en_byte = None
        tarjeta.acercar()
        seleccionar_aplicacion(tarjeta)
        _, sw, _ = enviar(tarjeta, None, a.INS_READ_DATA, cabecera_datos(3, 0, 4))
        self.assertEqual(sw, 0x91AE)  # hay que volver a autenticar


class NoImplementadoTests(Base):
    def test_sdm_y_contadores_quedan_para_m1_03b(self):
        tarjeta = self.nueva()
        with self.assertRaises(NotImplementedError):
            tarjeta.transmitir(a.nativo(a.INS_GET_FILE_COUNTERS, b"\x02"))
        sesion = autenticar(tarjeta, 0, bytes(16))
        with self.assertRaises(NotImplementedError):
            enviar(tarjeta, sesion, a.INS_CHANGE_FILE_SETTINGS, b"\x02", h("4000E0C1F121"), FULL)


if __name__ == "__main__":
    unittest.main()
