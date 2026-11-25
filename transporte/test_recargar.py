"""recargar_tarjeta() con una NTAG215 en memoria (la TarjetaVirtual del simulador).

    cd transporte && python3 -m unittest test_recargar
"""

import unittest
from unittest import mock

import recargar
from simular_tarjetas import emitir
from tarjeta import formato


class NtagVirtual:
    """Lo que recargar_tarjeta() usa de NTAG215, sobre una TarjetaVirtual."""

    def __init__(self, tv):
        self.tv, self.uid = tv, tv.uid

    def autenticar(self, pwd):
        return self.tv.comando(bytes([0x1B]) + pwd)

    def leer(self, desde, hasta):
        return self.tv.comando(bytes([0x3A, desde, hasta]))

    def contador_nfc(self):
        return int.from_bytes(self.tv.comando(b"\x39\x02"), "little")

    def escribir_paginas(self, desde, datos):
        for i in range(0, len(datos), 4):
            self.tv.escribir(desde + i // 4, datos[i:i + 4])


def servidor_falso(tv, abono=500, pierde_autenticacion=True):
    """Responde como el servidor. Mientras se espera su respuesta, el ACR122U (o pcscd) vuelve a
    seleccionar la tarjeta y se pierde la autenticación: medido el 24 nov, pasa a los 2 s sin hablarle."""
    llamadas = []

    def llamar(metodo, ruta, datos=None, **_):
        llamadas.append((metodo, ruta, datos))
        if ruta == "/api/recargas/":
            if pierde_autenticacion:
                tv.autenticada = False
            return {"abono": abono, "hasta_seq": 7, "recarga": {"id": 31},
                    "cuenta": {"nombre": "Prueba", "saldo": 1500}}
        return {}

    return llamar, llamadas


class RecargarTarjeta(unittest.TestCase):
    def saldo(self, tv):
        uid = tv.uid
        est = formato.decodificar(bytes(tv.paginas[formato.PAG_INICIO * 4:(formato.PAG_FIN + 1) * 4]), uid,
                                  recargar.claves.clave_firma(uid))
        return est.registro.saldo, est.registro.recarga

    def test_graba_aunque_se_pierda_la_autenticacion_esperando_al_servidor(self):
        tv = emitir(900000101, 0, 0)
        llamar, llamadas = servidor_falso(tv)
        with mock.patch.object(recargar.api_cliente, "llamar", llamar):
            antes, despues, abono, _ = recargar.recargar_tarjeta(NtagVirtual(tv), 500)
        self.assertEqual((antes, despues, abono), (0, 500, 500))
        self.assertEqual(self.saldo(tv), (500, 7))
        self.assertEqual([r for _, r, _ in llamadas], ["/api/recargas/", "/api/recargas/entregas/"])

    def test_sin_perder_la_autenticacion_tambien_graba(self):
        tv = emitir(900000102, 0, 200)
        llamar, _ = servidor_falso(tv, abono=300, pierde_autenticacion=False)
        with mock.patch.object(recargar.api_cliente, "llamar", llamar):
            recargar.recargar_tarjeta(NtagVirtual(tv), 300)
        self.assertEqual(self.saldo(tv), (500, 7))

    def test_si_se_retira_de_verdad_la_recarga_queda_pendiente(self):
        tv = emitir(900000103, 0, 0)
        tv.cortar_escritura_tras = 1
        llamar, llamadas = servidor_falso(tv)
        with mock.patch.object(recargar.api_cliente, "llamar", llamar):
            with self.assertRaises(recargar.ErrorRecarga) as e:
                recargar.recargar_tarjeta(NtagVirtual(tv), 500)
        self.assertIn("--aplicar", str(e.exception))
        self.assertEqual([r for _, r, _ in llamadas], ["/api/recargas/"])  # sin entrega confirmada


if __name__ == "__main__":
    unittest.main()
