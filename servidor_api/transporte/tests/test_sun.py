"""Criptografía SUN del servidor (transporte/sun.py, M1-08b, #42)."""

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from transporte import sun

from . import tarjeta_sun

H = bytes.fromhex
CERO = bytes(16)
# AN12196 §3.4.2.2 (Tabla 2) y §3.4.4.2.1 (Tabla 4): K3 = K4 = 00…00
AN_UID, AN_CONTADOR = H("04DE5F1EACC040"), 0x3D
AN_PICC, AN_MAC = H("EF963FF7828658A599F3041510671E88"), H("94EED9EE65337086")

UID = H("04B3E121CA2A81")
K3 = H("33" * 16)
MAESTRA_K4 = H("44" * 16)


class AyudanteDeTarjetaTests(SimpleTestCase):
    """El generador de enlaces de las pruebas reproduce el ejemplo oficial."""

    def test_reproduce_an12196(self):
        p, m = tarjeta_sun.enlace(CERO, CERO, AN_UID, AN_CONTADOR, relleno=H("DA5CF60941"))
        self.assertEqual((H(p), H(m)), (AN_PICC, AN_MAC))

    def test_k4_coincide_con_el_vector_de_m1_05(self):
        # vectores_an10922.json (#39): k4_v1, maestra 44…44, UID 04b3e121ca2a81
        self.assertEqual(tarjeta_sun.k4_de_tarjeta(MAESTRA_K4, UID), H("7635fed287aa54870ea237fd72dfed45"))


class VectoresAN12196Tests(SimpleTestCase):
    def test_descifrar_picc_data(self):
        self.assertEqual(sun.descifrar_picc(CERO, AN_PICC), (AN_UID, AN_CONTADOR))

    def test_clave_de_sesion_del_mac(self):
        self.assertEqual(sun.clave_sesion_mac(CERO, AN_UID, AN_CONTADOR), H("3FB5F6E3A807A03D5E3570ACE393776F"))
        # AN12196 §3.3, Tabla 1
        self.assertEqual(sun.clave_sesion_mac(H("5ACE7E50AB65D5D51FD5BF5A16B8205B"), H("04C767F2066180"), 1),
                         H("3A3E8110E05311F7A3FCF0D969BF2B48"))

    def test_sdmmac_sobre_entrada_vacia(self):
        self.assertEqual(sun.sdmmac(CERO, AN_UID, AN_CONTADOR), AN_MAC)


class DiversificacionK4Tests(SimpleTestCase):
    def test_vector_de_m1_05(self):
        self.assertEqual(sun.diversificar_k4(MAESTRA_K4, UID, 1), H("7635fed287aa54870ea237fd72dfed45"))

    def test_version_fuera_de_rango(self):
        for version in (0, 256):
            with self.subTest(version=version), self.assertRaises(ValueError):
                sun.diversificar_k4(MAESTRA_K4, UID, version)


class VerificarTests(SimpleTestCase):
    claves = sun.ClavesSun(k3=K3, k4_maestras=((1, MAESTRA_K4),))

    def enlace(self, contador=5, maestra=MAESTRA_K4, version=1, k3=K3, uid=UID):
        p, m = tarjeta_sun.enlace(k3, tarjeta_sun.k4_de_tarjeta(maestra, uid, version), uid, contador)
        return H(p), H(m)

    def test_enlace_valido(self):
        self.assertEqual(sun.verificar(self.claves, *self.enlace(contador=5)),
                         sun.LecturaSun(uid=UID, contador=5, version_k4=1))

    def test_mac_alterado(self):
        p, m = self.enlace()
        for alterado in (bytes([m[0] ^ 1]) + m[1:], m[:-1] + bytes([m[-1] ^ 0x80]), bytes(8)):
            with self.subTest(mac=alterado.hex()), self.assertRaises(sun.SunInvalido):
                sun.verificar(self.claves, p, alterado)

    def test_picc_data_alterado(self):
        p, m = self.enlace()
        with self.assertRaises(sun.SunInvalido):
            sun.verificar(self.claves, bytes([p[0] ^ 1]) + p[1:], m)

    def test_otra_k3_no_descifra(self):
        with self.assertRaises(sun.SunInvalido):
            sun.verificar(self.claves, *self.enlace(k3=H("55" * 16)))

    def test_otra_maestra_k4(self):
        with self.assertRaises(sun.SunInvalido):
            sun.verificar(self.claves, *self.enlace(maestra=H("66" * 16)))

    def test_rotacion_acepta_las_versiones_configuradas(self):
        claves = sun.ClavesSun(k3=K3, k4_maestras=((2, H("77" * 16)), (1, MAESTRA_K4)))
        self.assertEqual(sun.verificar(claves, *self.enlace(version=1)).version_k4, 1)
        self.assertEqual(sun.verificar(claves, *self.enlace(maestra=H("77" * 16), version=2)).version_k4, 2)

    def test_longitudes_invalidas(self):
        p, m = self.enlace()
        for argumentos in ((p[:15], m), (p, m[:7]), (p + b"\x00", m)):
            with self.subTest(largos=tuple(map(len, argumentos))), self.assertRaises(sun.SunInvalido):
                sun.verificar(self.claves, *argumentos)


class ConfiguracionTests(SimpleTestCase):
    @override_settings(SUN_K3="", SUN_K4_MAESTRAS="")
    def test_sin_claves_no_hay_verificacion(self):
        self.assertIsNone(sun.claves_configuradas())

    @override_settings(SUN_K3="33" * 16, SUN_K4_MAESTRAS="1:" + "44" * 16 + ", 3:" + "77" * 16)
    def test_lee_las_claves_y_ordena_de_la_version_mas_nueva(self):
        claves = sun.claves_configuradas()
        self.assertEqual(claves.k3, K3)
        self.assertEqual(claves.k4_maestras, ((3, H("77" * 16)), (1, MAESTRA_K4)))

    def test_configuracion_incompleta_o_mal_formada(self):
        malas = [("33" * 16, ""), ("", "1:" + "44" * 16), ("33" * 15, "1:" + "44" * 16),
                 ("33" * 16, "44" * 16), ("33" * 16, "0:" + "44" * 16), ("33" * 16, "256:" + "44" * 16),
                 ("33" * 16, "1:" + "44" * 16 + ",1:" + "55" * 16), ("33" * 16, "1:" + "4G" * 16),
                 ("33" * 16, "x:" + "44" * 16)]
        for k3, maestras in malas:
            with self.subTest(k3=k3, maestras=maestras), override_settings(SUN_K3=k3, SUN_K4_MAESTRAS=maestras):
                with self.assertRaises(ImproperlyConfigured) as contexto:
                    sun.claves_configuradas()
                self.assertNotIn("44" * 16, str(contexto.exception))
