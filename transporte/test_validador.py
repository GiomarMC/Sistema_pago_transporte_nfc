"""Cobros dudosos del validador de PC (#240) con NTAG215 en memoria (la TarjetaVirtual del simulador).

    cd transporte && python3 -m unittest test_validador
"""

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import validador
from simular_tarjetas import emitir
from tarjeta import formato
from tarjeta.ntag215 import ErrorTarjeta
from validador_local import BDValidador

PRECIO = 130  # tarifa 1 (general) de config.TARIFAS


class NtagVirtual:
    """Lo que cobrar() usa de NTAG215, sobre una TarjetaVirtual. falla_relectura: la siguiente lectura tras una
    escritura falla, con la escritura ya hecha (la tarjeta se retiró justo al final)."""

    def __init__(self, tv, falla_relectura=False):
        self.tv, self.uid = tv, tv.uid
        self.falla_relectura, self.escribio = falla_relectura, False

    def autenticar(self, pwd):
        return self.tv.comando(bytes([0x1B]) + pwd)

    def leer(self, desde, hasta):
        if self.falla_relectura and self.escribio:
            raise ErrorTarjeta("la tarjeta se retiró del lector")
        return self.tv.comando(bytes([0x3A, desde, hasta]))

    def contador_nfc(self):
        self.tv.contador += 1  # cada lectura de verdad lo sube
        return int.from_bytes(self.tv.comando(b"\x39\x02"), "little")

    def escribir_paginas(self, desde, datos):
        for i in range(0, len(datos), 4):
            self.tv.escribir(desde + i // 4, datos[i:i + 4])
        self.escribio = True


class CobrosDudosos(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.bdv = self.validador(101)

    def validador(self, numero):
        bdv = BDValidador(numero, ruta=Path(self.dir.name) / f"v{numero}.db")
        self.addCleanup(bdv.bd.close)
        return bdv

    def registro(self, tv):
        est = formato.decodificar(bytes(tv.paginas[formato.PAG_INICIO * 4:(formato.PAG_FIN + 1) * 4]), tv.uid,
                                  validador.claves.clave_firma(tv.uid))
        return est.registro

    def eventos(self, bdv, tipo="viaje"):
        return [dict(f) for f in bdv.bd.execute("SELECT * FROM eventos WHERE tipo = ? ORDER BY id", (tipo,))]

    def cobro_sin_comprobar(self, tv, **opciones):
        """Primera pasada: la escritura se hace y la relectura falla."""
        with self.assertRaises(validador.Rechazo) as r:
            validador.cobrar(NtagVirtual(tv, **opciones), self.bdv)
        self.assertEqual((r.exception.motivo, r.exception.aviso), ("Acerque de nuevo", True))
        self.assertEqual(self.bdv.cobros_dudosos(), 1)

    def test_escritura_hecha_y_relectura_fallida_dentro_del_margen(self):
        tv = emitir(900000201, 1, 500)
        self.cobro_sin_comprobar(tv, falla_relectura=True)
        self.assertEqual(self.registro(tv).saldo, 500 - PRECIO)  # la tarjeta sí quedó cobrada
        self.assertEqual(self.eventos(self.bdv), [])

        cobro = validador.cobrar(NtagVirtual(tv), self.bdv)  # vuelve a acercarla enseguida
        self.assertEqual(cobro.mensaje, "ya pagado")
        self.assertEqual(self.registro(tv).saldo, 500 - PRECIO)  # sin segundo cobro
        viajes = self.eventos(self.bdv)
        self.assertEqual([(v["operacion"], v["monto"], v["saldo_final"], v["contador"]) for v in viajes],
                         [(2, PRECIO, 500 - PRECIO, None)])
        self.assertEqual(self.bdv.cobros_dudosos(), 0)

    def test_escritura_hecha_y_otra_pasada_pasado_el_margen(self):
        tv = emitir(900000202, 1, 500)
        self.cobro_sin_comprobar(tv, falla_relectura=True)
        with mock.patch.object(validador, "MARGEN_DOBLE_TOQUE", timedelta(0)):  # como si pasara más de 60 s
            cobro = validador.cobrar(NtagVirtual(tv), self.bdv)
        self.assertEqual(cobro.saldo, 500 - 2 * PRECIO)  # el viaje nuevo, una sola vez
        self.assertEqual(self.registro(tv).saldo, 500 - 2 * PRECIO)
        viajes = self.eventos(self.bdv)
        self.assertEqual([(v["operacion"], v["saldo_final"]) for v in viajes],
                         [(2, 500 - PRECIO), (3, 500 - 2 * PRECIO)])
        self.assertIsNone(viajes[0]["contador"])
        self.assertIsNotNone(viajes[1]["contador"])

        # Una tercera pasada inmediata ya no registra nada más
        validador.cobrar(NtagVirtual(tv), self.bdv)
        self.assertEqual(len(self.eventos(self.bdv)), 2)

    def test_escritura_no_terminada_se_olvida(self):
        tv = emitir(900000203, 1, 500)
        tv.cortar_escritura_tras = 0  # se retira antes de escribir nada
        self.cobro_sin_comprobar(tv)
        self.assertEqual(self.registro(tv).saldo, 500)

        cobro = validador.cobrar(NtagVirtual(tv), self.bdv)
        self.assertEqual(cobro.saldo, 500 - PRECIO)
        viajes = self.eventos(self.bdv)
        self.assertEqual([(v["operacion"], v["saldo_final"]) for v in viajes], [(2, 500 - PRECIO)])
        self.assertEqual(self.eventos(self.bdv, "incidencia"), [])
        self.assertEqual(self.bdv.cobros_dudosos(), 0)

    def test_paso_por_otro_validador_deja_una_incidencia(self):
        tv = emitir(900000204, 1, 500)
        self.cobro_sin_comprobar(tv, falla_relectura=True)  # op. 2, grabada
        otro = self.validador(102)
        validador.cobrar(NtagVirtual(tv), otro)  # op. 3, en el 102
        self.assertEqual(self.registro(tv).operacion, 3)

        with mock.patch.object(validador, "MARGEN_DOBLE_TOQUE", timedelta(0)):
            validador.cobrar(NtagVirtual(tv), self.bdv)  # op. 4, de vuelta en el 101
        incidencias = self.eventos(self.bdv, "incidencia")
        self.assertEqual([i["detalle"] for i in incidencias], ["cobro dudoso op 2"])
        self.assertEqual([v["operacion"] for v in self.eventos(self.bdv)], [4])  # la op. 2 no se da por viaje
        self.assertEqual(self.bdv.cobros_dudosos(), 0)

    def test_el_dudoso_sobrevive_a_reabrir_la_base(self):
        tv = emitir(900000205, 1, 500)
        self.cobro_sin_comprobar(tv, falla_relectura=True)
        self.bdv.bd.close()
        self.bdv = self.validador(101)  # el validador se reinició
        validador.cobrar(NtagVirtual(tv), self.bdv)
        self.assertEqual([v["operacion"] for v in self.eventos(self.bdv)], [2])

    def test_cobro_normal_no_anota_nada(self):
        tv = emitir(900000206, 1, 500)
        validador.cobrar(NtagVirtual(tv), self.bdv)
        self.assertEqual(self.bdv.cobros_dudosos(), 0)
        self.assertEqual([v["operacion"] for v in self.eventos(self.bdv)], [2])


if __name__ == "__main__":
    unittest.main()
