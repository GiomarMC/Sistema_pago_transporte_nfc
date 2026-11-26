"""Criptografía EV2 contra los ejemplos oficiales de NXP (AN12196 Rev. 2.0)."""

import unittest
import zlib

from tarjeta import ev2

h = bytes.fromhex

# AN12196 §5.6, Tabla 14 (pp. 27-28): AuthenticateEV2First con K0 = 00..00
K0_FABRICA = bytes(16)
RND_A = h("13C5DB8A5930439FC3DEF9A4C675360F")
RND_B = h("B9E2FC789B64BF237CCCAA20EC7E6E48")
E_RND_B = h("A04C124213C186F22399D33AC2A30215")
E_RND_A_RND_B = h("35C3E05A752E0144BAC0DE51C1F22C56B34408A23D8AEA266CAB947EA8E0118D")
E_RESPUESTA_AUTH = h("3FA64DB5446D1F34CD6EA311167F5E4985B89690C04A05F17FA7AB2F08120663")
TI_T14 = h("9D00C4DF")
ENC_T14 = h("1309C877509E5A215007FF0ED19CA564")
MAC_T14 = h("4C6626F5E72EA694202139295C7A7FC7")

# AN12196 §5.16, Tablas 25 y 26 (pp. 37-39)
ENC_T25 = h("4CF3CB41A22583A61E89B158D252FC53")
MAC_T25 = h("5529860B2FC5FB6154B7F28361D30BF9")
TI_T25 = h("7614281A")


class PrimitivasTests(unittest.TestCase):
    def test_truncado_toma_los_bytes_de_indice_impar(self):
        self.assertEqual(ev2.truncar_mac(h("EA5D2E0CBFE24C0BCBCD501D21060EE6")), h("5D0CE20BCD1D06E6"))

    def test_relleno_metodo_2(self):
        self.assertEqual(ev2.rellenar(b""), b"\x80" + bytes(15))
        self.assertEqual(ev2.rellenar(bytes(15)), bytes(15) + b"\x80")
        self.assertEqual(len(ev2.rellenar(bytes(16))), 32)  # bloque extra si ya es múltiplo
        for longitud in range(0, 40):
            datos = bytes(range(longitud))
            self.assertEqual(ev2.quitar_relleno(ev2.rellenar(datos)), datos)

    def test_relleno_invalido(self):
        for malo in (bytes(16), b"\x01" * 16, b"\x80" + bytes(31), b"\x80" + bytes(14)):
            with self.assertRaises(ev2.ErrorIntegridad):
                ev2.quitar_relleno(malo)

    def test_rotaciones(self):
        self.assertEqual(ev2.rotar_izquierda(b"\x01\x02\x03"), b"\x02\x03\x01")
        self.assertEqual(ev2.rotar_derecha(b"\x01\x02\x03"), b"\x03\x01\x02")

    def test_claves_de_tamano_incorrecto(self):
        with self.assertRaises(ValueError):
            ev2.cmac(bytes(15), b"")
        with self.assertRaises(ValueError):
            ev2.Sesion(bytes(16), bytes(16), b"\x00\x00\x00")


class AutenticacionTests(unittest.TestCase):
    def test_tabla_14_criptogramas(self):
        self.assertEqual(ev2.aes_cbc_descifrar(K0_FABRICA, ev2.IV_CERO, E_RND_B), RND_B)
        self.assertEqual(ev2.aes_cbc_cifrar(K0_FABRICA, ev2.IV_CERO,
                                            RND_A + ev2.rotar_izquierda(RND_B)), E_RND_A_RND_B)
        claro = ev2.aes_cbc_descifrar(K0_FABRICA, ev2.IV_CERO, E_RESPUESTA_AUTH)
        self.assertEqual(claro[:4], TI_T14)
        self.assertEqual(ev2.rotar_derecha(claro[4:20]), RND_A)

    def test_tabla_14_claves_de_sesion(self):
        self.assertEqual(ev2.claves_sesion(K0_FABRICA, RND_A, RND_B), (ENC_T14, MAC_T14))


class MensajeriaTests(unittest.TestCase):
    def test_tabla_18_comando_full_y_mac_de_respuesta(self):
        # ChangeFileSettings del archivo 02, CmdCtr = 1 (AN12196 §5.9, pp. 31-32)
        sesion = ev2.Sesion(ENC_T14, MAC_T14, TI_T14, cmd_ctr=1)
        cifrado = sesion.cifrar_comando(h("4000E0C1F121200000430000430000"))
        self.assertEqual(cifrado, h("61B6D97903566E84C3AE5274467E89EA"))
        self.assertEqual(sesion.mac_comando(0x5F, b"\x02", cifrado), h("D799B7C1A0EF7A04"))
        self.assertEqual(sesion.descifrar_comando(cifrado), h("4000E0C1F121200000430000430000"))
        self.assertEqual(sesion.avanzada().mac_respuesta(0x00), h("57BFF87B1241E93D"))

    def test_tabla_17_mac_de_respuesta(self):
        # WriteData con CmdCtr = 0: la respuesta usa CmdCtr = 1 (AN12196 §5.8.2, p. 30)
        sesion = ev2.Sesion(ENC_T14, MAC_T14, TI_T14).avanzada()
        self.assertEqual(sesion.mac_respuesta(0x00), h("FC222E5F7A542452"))

    def test_respuesta_cifrada_ida_y_vuelta(self):
        sesion = ev2.Sesion(ENC_T14, MAC_T14, TI_T14, cmd_ctr=5)
        datos = bytes(range(20))
        self.assertEqual(sesion.descifrar_respuesta(sesion.cifrar_respuesta(datos)), datos)
        self.assertNotEqual(sesion.cifrar_respuesta(datos), sesion.cifrar_comando(datos))

    def test_sesion_inmutable_y_cmdctr_agotado(self):
        sesion = ev2.Sesion(ENC_T14, MAC_T14, TI_T14, cmd_ctr=ev2.CMDCTR_MAXIMO - 1)
        siguiente = sesion.avanzada()
        self.assertEqual((sesion.cmd_ctr, siguiente.cmd_ctr), (0xFFFE, 0xFFFF))
        self.assertTrue(siguiente.agotada)
        with self.assertRaises(ev2.ErrorIntegridad):
            siguiente.avanzada()


class ChangeKeyTests(unittest.TestCase):
    def test_crc32nk_es_el_complemento_de_zlib(self):
        nueva = h("F3847D627727ED3BC9C4CC050489B966")
        self.assertEqual(ev2.crc32nk(nueva), h("789DFADC"))
        self.assertNotEqual(ev2.crc32nk(nueva), zlib.crc32(nueva).to_bytes(4, "little"))

    def test_tabla_25_cambiar_k2(self):
        sesion = ev2.Sesion(ENC_T25, MAC_T25, TI_T25, cmd_ctr=2)
        datos = ev2.datos_change_key(2, h("F3847D627727ED3BC9C4CC050489B966"), bytes(16), 1)
        cifrado = sesion.cifrar_comando(datos)
        self.assertEqual(cifrado, h("2CF362B7BF4311FF3BE1DAA295E8C68D"
                                    "E09050560D19B9E16C2393AE9CD1FAC7"))
        self.assertEqual(sesion.mac_comando(0xC4, b"\x02", cifrado), h("5D0CE20BCD1D06E6"))
        self.assertEqual(sesion.avanzada().mac_respuesta(0x00), h("203BB55D1089D587"))

    def test_tabla_26_cambiar_k0(self):
        sesion = ev2.Sesion(ENC_T25, MAC_T25, TI_T25, cmd_ctr=3)
        datos = ev2.datos_change_key(0, h("5004BF991F408672B1EF00F08F9E8647"), bytes(16), 1)
        cifrado = sesion.cifrar_comando(datos)
        self.assertEqual(cifrado, h("C0EB4DEEFEDDF0B513A03A95A7549181"
                                    "8580503190D4D05053FF75668A01D6FD"))
        self.assertEqual(sesion.mac_comando(0xC4, b"\x00", cifrado), h("A6610234BDED6432"))

    def test_datos_change_key_invalidos(self):
        with self.assertRaises(ValueError):
            ev2.datos_change_key(5, bytes(16), bytes(16), 1)
        with self.assertRaises(ValueError):
            ev2.datos_change_key(1, bytes(16), bytes(16), 256)


if __name__ == "__main__":
    unittest.main()
