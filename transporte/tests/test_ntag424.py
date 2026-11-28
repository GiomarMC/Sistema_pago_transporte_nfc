"""Controlador de la NTAG 424 (M1-04, #38) contra el simulador de M1-03: emisión, cobros con el validador de PC,
escrituras cortadas y la mensajería segura.

    cd transporte && python3 -m unittest tests.test_ntag424
"""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import validador
from tarjeta import an10922, claves, formato, ntag424
from tarjeta import apdu424 as a
from tarjeta.base import (OFFSET_REGISTROS, AutenticacionRechazada, ErrorTarjeta, TarjetaAjena,
                          TarjetaFisica)
from tarjeta.ntag424 import NTAG424, ComandoRechazado, Tarjeta424
from tarjeta.simulador424 import Simulador424
from tests.apoyo215 import EntornoTarjetas
from validador_local import BDValidador

UID = bytes.fromhex("04B3E121CA2A81")
CERO = bytes(16)
MAESTRAS = {r: bytes([0x10 + r]) * 16 for r in (0, 1, 2, 4)}  # solo para pruebas
K3 = bytes([0x33]) * 16
PRECIO = 130  # tarifa 1 (general) de config.TARIFAS
CLAVES = ntag424.claves_validador(MAESTRAS[1], MAESTRAS[2])
AJUSTES_03 = bytes.fromhex("032012")  # Read 1, Write 2, ReadWrite 2, Change 0, Full (§9)


def diversificada(ranura, uid=UID):
    return K3 if ranura == 3 else an10922.diversificar_aes128(MAESTRAS[ranura], uid, ranura, 1)


def personalizar(sim):
    """Lo de docs/ntag424-personalizacion.md §13 que usa el controlador: permisos del archivo 03 y las cinco claves
    (personalizar_424.py, #40, hará además el NDEF con SUN)."""
    chip = NTAG424(sim.transmitir, sim.uid)
    chip.seleccionar_aplicacion()
    chip.autenticar(0, CERO)
    chip.cambiar_ajustes(3, AJUSTES_03)
    for numero in (1, 2, 3, 4):
        chip.cambiar_clave(numero, diversificada(numero, sim.uid), CERO, 1)
    chip.cambiar_clave(0, diversificada(0, sim.uid), CERO, 1)
    return chip


def tarjeta(sim, claves_tarjeta=CLAVES):
    sim.acercar()  # toque nuevo
    return Tarjeta424(NTAG424(sim.transmitir, sim.uid), claves_tarjeta)


def emitir(sim, id_tarjeta, saldo, tarifa=1):
    """Personaliza y graba el área inicial con K2, como en §13 pasos 6-7."""
    personalizar(sim)
    cab = formato.Cabecera(id_tarjeta, id_tarjeta, tarifa, date.today(), formato.ACTIVA)
    area = formato.codificar_emision(cab, saldo, sim.uid, claves.clave_firma(sim.uid))
    with tarjeta(sim) as t:
        t.autenticar()
        t.escribir_cabecera(area[:24])
        for indice, offset in enumerate(OFFSET_REGISTROS):
            t.escribir_registro(indice, area[offset:offset + 20])
    return area


def estado(sim):
    with tarjeta(sim) as t:
        t.autenticar()
        return formato.decodificar(t.leer_area(), sim.uid, claves.clave_firma(sim.uid))


class Cobros(EntornoTarjetas):
    """Criterio de #38: se emite, se cobra 10 veces, se corta una escritura y la lectura sigue devolviendo el
    registro anterior válido."""

    def setUp(self):
        super().setUp()
        directorio = tempfile.TemporaryDirectory()
        self.addCleanup(directorio.cleanup)
        self.bdv = BDValidador(101, ruta=Path(directorio.name) / "v101.db")
        self.addCleanup(self.bdv.bd.close)
        margen = mock.patch.object(validador, "MARGEN_DOBLE_TOQUE", timedelta(0))  # cada cobro, un viaje nuevo
        margen.start()
        self.addCleanup(margen.stop)
        self.sim = Simulador424(uid=UID)

    def cobrar(self):
        with tarjeta(self.sim) as t:
            return validador.cobrar(t, self.bdv)

    def viajes(self):
        return [dict(f) for f in self.bdv.bd.execute("SELECT * FROM eventos WHERE tipo = 'viaje' ORDER BY id")]

    def test_emitir_y_10_cobros(self):
        emitir(self.sim, 900000301, 2000)
        self.assertEqual(estado(self.sim).registro.saldo, 2000)
        for i in range(1, 11):
            cobro = self.cobrar()
            self.assertEqual(cobro.saldo, 2000 - i * PRECIO)
            est = estado(self.sim)
            self.assertEqual((est.registro.saldo, est.registro.operacion), (2000 - i * PRECIO, 1 + i))
        viajes = self.viajes()
        self.assertEqual([v["operacion"] for v in viajes], list(range(2, 12)))
        self.assertEqual({v["contador"] for v in viajes}, {None})  # la 424 no da contador (C3)

    def test_escritura_cortada_deja_el_registro_anterior(self):
        emitir(self.sim, 900000302, 1000)
        for _ in range(3):
            self.cobrar()
        antes = estado(self.sim).registro
        # El WriteData del registro (53 B) no llega entero; la autenticación con K2 (38 B) sí
        self.sim.cortar_en_byte = 40
        with self.assertRaises(validador.Rechazo) as r:
            self.cobrar()
        self.assertEqual(r.exception.motivo, "Acerque de nuevo")
        self.sim.cortar_en_byte = None
        despues = estado(self.sim).registro
        self.assertEqual((despues.saldo, despues.operacion), (antes.saldo, antes.operacion))
        # Al volver a acercarla se cobra una sola vez y el cobro dudoso queda resuelto
        cobro = self.cobrar()
        self.assertEqual(cobro.saldo, antes.saldo - PRECIO)
        self.assertEqual(estado(self.sim).registro.operacion, antes.operacion + 1)
        self.assertEqual(self.bdv.cobros_dudosos(), 0)
        self.assertEqual(len(self.viajes()), 4)

    def test_escritura_hecha_y_relectura_cortada(self):
        """La tarjeta se retira justo después del WriteData: el cobro quedó grabado y el validador lo recupera."""
        emitir(self.sim, 900000303, 1000)
        original = self.sim.transmitir
        enviados = []

        def transmitir(apdu):
            if enviados and enviados[-1] == a.INS_WRITE_DATA and apdu[1] == a.INS_READ_DATA:
                raise a.TarjetaRetirada("retirada antes de la relectura")
            enviados.append(apdu[1])
            return original(apdu)

        self.sim.transmitir = transmitir
        with self.assertRaises(validador.Rechazo):
            self.cobrar()
        self.sim.transmitir = original
        self.assertEqual(estado(self.sim).registro.saldo, 1000 - PRECIO)  # la tarjeta sí quedó cobrada
        self.assertEqual(self.bdv.cobros_dudosos(), 1)
        cobro = self.cobrar()
        self.assertEqual(cobro.saldo, 1000 - 2 * PRECIO)
        self.assertEqual([v["operacion"] for v in self.viajes()], [2, 3])
        self.assertEqual(self.bdv.cobros_dudosos(), 0)


class Controlador(EntornoTarjetas):
    def setUp(self):
        super().setUp()
        self.sim = Simulador424(uid=UID)

    def test_cumple_la_interfaz_c3(self):
        t = tarjeta(self.sim)
        self.assertIsInstance(t, TarjetaFisica)
        self.assertEqual(t.uid, UID)
        self.assertIsNone(t.contador())

    def test_tarjeta_de_fabrica_no_es_del_sistema(self):
        with self.assertRaises(AutenticacionRechazada):
            tarjeta(self.sim).autenticar()

    def test_otras_claves_se_rechazan(self):
        personalizar(self.sim)
        otras = ntag424.claves_validador(bytes(16), bytes(16))
        with self.assertRaises(AutenticacionRechazada):
            tarjeta(self.sim, otras).autenticar()

    def test_tarjeta_que_no_prueba_conocer_la_clave(self):
        """Una tarjeta que acepta cualquier parte 2 pero no conoce la clave no devuelve RndA rotado."""
        respuestas = iter([bytes(16) + b"\x91\xAF", bytes(32) + b"\x91\x00"])
        chip = NTAG424(lambda apdu: next(respuestas), UID)
        with self.assertRaises(TarjetaAjena):
            chip.autenticar(1, CERO)
        self.assertIsNone(chip.sesion)

    def test_leer_sin_autenticar(self):
        personalizar(self.sim)
        with self.assertRaises(ErrorTarjeta):
            tarjeta(self.sim).leer_area()

    def test_k1_lee_pero_no_escribe(self):
        emitir(self.sim, 900000304, 500)
        chip = NTAG424(self.sim.transmitir, UID)
        chip.seleccionar_aplicacion()
        chip.autenticar(1, diversificada(1))
        self.assertEqual(len(chip.leer_datos(3, 0, 72)), 72)
        with self.assertRaises(ComandoRechazado) as r:
            chip.escribir_datos(3, 32, bytes(20))
        self.assertEqual(r.exception.sw, 0x91AE)
        self.assertIsNone(chip.sesion)  # el error cierra la sesión, como en la tarjeta

    def test_sin_k2_no_escribe_y_el_area_no_cambia(self):
        area = emitir(self.sim, 900000305, 500)
        k1 = CLAVES(UID)[0]
        t = tarjeta(self.sim, lambda uid: (k1, CERO))
        t.autenticar()
        with self.assertRaises(ErrorTarjeta):
            t.escribir_registro(0, bytes(20))
        with tarjeta(self.sim) as t:
            t.autenticar()
            self.assertEqual(t.leer_area(), area)

    def test_la_escritura_pasa_a_k2_una_sola_vez(self):
        emitir(self.sim, 900000306, 500)
        t = tarjeta(self.sim)
        t.autenticar()
        self.assertEqual(t.chip.clave_sesion, 1)
        area = t.leer_area()
        t.escribir_registro(0, area[32:52])
        self.assertEqual(t.chip.clave_sesion, 2)
        t.escribir_registro(1, area[52:72])
        self.assertEqual(t.chip.clave_sesion, 2)
        self.assertEqual(t.leer_area(), area)

    def test_mac_de_la_respuesta_alterado(self):
        emitir(self.sim, 900000307, 500)
        original = self.sim.transmitir

        def alterar(apdu):
            respuesta = original(apdu)
            if apdu[1] == a.INS_READ_DATA:
                respuesta = respuesta[:-3] + bytes([respuesta[-3] ^ 1]) + respuesta[-2:]
            return respuesta

        self.sim.transmitir = alterar
        t = tarjeta(self.sim)
        t.autenticar()
        with self.assertRaises(ErrorTarjeta):
            t.leer_area()
        self.assertIsNone(t.chip.sesion)

    def test_tarjeta_retirada(self):
        emitir(self.sim, 900000308, 500)
        t = tarjeta(self.sim)
        t.autenticar()
        self.sim.presente = False
        with self.assertRaises(ErrorTarjeta):
            t.leer_area()
        self.assertIsNone(t.chip.sesion)
        t.cerrar()  # sin tarjeta no falla

    def test_reiniciar_sesion_pierde_la_autenticacion(self):
        emitir(self.sim, 900000309, 500)
        t = tarjeta(self.sim)
        t.autenticar()
        t.reiniciar_sesion()
        with self.assertRaises(ErrorTarjeta):
            t.leer_area()

    def test_change_key_de_k0_cierra_la_sesion(self):
        chip = personalizar(self.sim)
        self.assertIsNone(chip.sesion)
        self.assertEqual(self.sim.versiones, [1] * 5)
        with self.assertRaises(AutenticacionRechazada):
            chip.autenticar(0, CERO)  # la K0 de fábrica ya no sirve
        chip.autenticar(0, diversificada(0))
        self.assertEqual(chip.clave_sesion, 0)

    def test_ajustes_del_archivo_03(self):
        chip = personalizar(self.sim)
        chip.autenticar(0, diversificada(0))
        self.assertEqual(chip.ajustes_archivo(3), bytes.fromhex("00032012800000"))  # FileType, opciones, tamaño
        chip.seleccionar_aplicacion()
        self.assertEqual(chip.ajustes_archivo(3), bytes.fromhex("00032012800000"))  # en claro sin sesión

    def test_cmdctr_agotado(self):
        chip = personalizar(self.sim)
        chip.autenticar(1, diversificada(1))
        chip.sesion = chip.sesion.__class__(chip.sesion.clave_enc, chip.sesion.clave_mac, chip.sesion.ti, 0xFFFF)
        with self.assertRaises(ErrorTarjeta):
            chip.leer_datos(3, 0, 72)
        self.assertIsNone(chip.sesion)


class ConexionPcsc(unittest.TestCase):
    """transmitir_pcsc y abrir_pcsc con una conexión de mentira (sin pyscard)."""

    class Conexion:
        def __init__(self, respuestas):
            self.respuestas, self.enviados, self.cerrada = list(respuestas), [], False

        def transmit(self, apdu):
            self.enviados.append(bytes(apdu))
            r = self.respuestas.pop(0)
            if isinstance(r, Exception):
                raise r
            return list(r[:-2]), r[-2], r[-1]

        def disconnect(self):
            self.cerrada = True

    def test_respuesta_con_sw(self):
        conexion = self.Conexion([b"\x01\x02\x91\x00"])
        self.assertEqual(ntag424.transmitir_pcsc(conexion)(b"\x90\x60\x00\x00\x00"), b"\x01\x02\x91\x00")

    def test_get_response_del_acr122u(self):
        conexion = self.Conexion([b"\x61\x02", b"\xAA\xBB\x90\x00"])
        self.assertEqual(ntag424.transmitir_pcsc(conexion)(b"\x00"), b"\xAA\xBB\x90\x00")
        self.assertEqual(conexion.enviados[1], bytes.fromhex("FFC0000002"))

    def test_error_de_pyscard_es_error_de_tarjeta(self):
        excepcion = type("CardConnectionException", (Exception,), {"__module__": "smartcard.Exceptions"})
        conexion = self.Conexion([excepcion("sin tarjeta")])
        with self.assertRaises(ErrorTarjeta):
            ntag424.transmitir_pcsc(conexion)(b"\x00")

    def test_otros_errores_no_se_esconden(self):
        conexion = self.Conexion([RuntimeError("fallo del programa")])
        with self.assertRaises(RuntimeError):
            ntag424.transmitir_pcsc(conexion)(b"\x00")

    def test_abrir_pide_el_uid_y_cerrar_desconecta(self):
        conexion = self.Conexion([UID + b"\x90\x00", b"\x90\x00"])
        t = ntag424.abrir_pcsc(conexion, CLAVES)
        self.assertEqual((t.uid, conexion.enviados[0]), (UID, bytes.fromhex("FFCA000000")))
        t.cerrar()
        self.assertTrue(conexion.cerrada)

    def test_abrir_sin_uid(self):
        with self.assertRaises(ErrorTarjeta):
            ntag424.abrir_pcsc(self.Conexion([b"\x63\x00"]), CLAVES)


class ClavesValidador(unittest.TestCase):
    def test_k1_y_k2_diversificadas_con_an10922(self):
        self.assertEqual(CLAVES(UID), (diversificada(1), diversificada(2)))
        self.assertNotEqual(CLAVES(UID), CLAVES(bytes.fromhex("0412BD11CE2A81")))


if __name__ == "__main__":
    unittest.main()
