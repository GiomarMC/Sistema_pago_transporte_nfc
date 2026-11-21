#!/usr/bin/env python3
"""Pruebas del servidor C1 falso: python3 -m unittest herramientas.test_servidor_c1_falso -v"""
import importlib.util
import pathlib
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from cryptography.hazmat.primitives.asymmetric import ec

AQUI = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("servidor_c1_falso", AQUI / "servidor_c1_falso.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)
P = S.P

CLAVE_107 = bytes((0x10 + i) & 0xFF for i in range(32))


def validador(tipo, cuerpo, secuencia=1, origen=107):
    cab = P.Cabecera(tipo, origen=origen, destino=P.SERVIDOR, secuencia=secuencia, creado=1_795_000_000)
    return P.empaquetar(cab, cuerpo, clave_hmac=CLAVE_107)


def evento(i):
    return P.Evento(i, P.VIAJE, i, 1_795_000_000 + i, 900 + i, bytes([i, 0, 0, 0]), monto=-130, saldo_final=5000 - 130 * i,
                    contador=i, detalle=f"viaje {i}")


class ServidorFalso(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.privada = ec.generate_private_key(ec.SECP256R1())
        cls.estado = S.Estado({107: CLAVE_107}, cls.privada, [P.Tarifa(1, 130, "general")], [4242, 77], en_voz_alta=False)
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), S.manejador(cls.estado))
        cls.url = f"http://127.0.0.1:{cls.http.server_port}{S.RUTA}"
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()

    def post(self, cuerpo, tipo=S.TIPO_BINARIO):
        peticion = urllib.request.Request(self.url, data=cuerpo, headers={"Content-Type": tipo}, method="POST")
        with urllib.request.urlopen(peticion, timeout=5) as r:
            return r.read()

    def respuestas(self, cuerpo):
        """Los paquetes de la respuesta, ya verificados con la clave pública del servidor falso."""
        return [P.desempaquetar(d, lambda n: None, self.privada.public_key()) for d in P.separar(self.post(cuerpo))]

    def test_eventos_confirma_el_ultimo_contiguo(self):
        eventos = P.Eventos(tuple(evento(i) for i in range(1, 31)))
        (acuse,) = self.respuestas(validador(P.EVENTOS, eventos))
        self.assertEqual(acuse.cabecera.tipo, P.ACUSE)
        self.assertEqual(acuse.cabecera.destino, 107)
        self.assertEqual(acuse.cuerpo.hasta_evento, 30)

        # Un hueco (faltan 31 a 40): confirma solo lo contiguo
        (acuse,) = self.respuestas(validador(P.EVENTOS, P.Eventos(tuple(evento(i) for i in range(41, 51))), secuencia=2))
        self.assertEqual(acuse.cuerpo.hasta_evento, 30)
        # Llegan los que faltaban: salta hasta el 50
        (acuse,) = self.respuestas(validador(P.EVENTOS, P.Eventos(tuple(evento(i) for i in range(31, 41))), secuencia=3))
        self.assertEqual(acuse.cuerpo.hasta_evento, 50)
        # Un reenvío de eventos ya recibidos no cambia nada
        (acuse,) = self.respuestas(validador(P.EVENTOS, P.Eventos((evento(5),)), secuencia=4))
        self.assertEqual(acuse.cuerpo.hasta_evento, 50)

    def hola(self, version_listas, version_tarifas):
        return P.Hola(version_listas, version_tarifas, ultimo_evento=0, pendientes=0, ajenos=0, enlace=1, senal=-20,
                      bytes_tx_hoy=0, bytes_rx_hoy=0, version_firmware=20261120)

    def test_hola_desactualizado_recibe_acuse_listas_y_tarifas(self):
        paquetes = self.respuestas(validador(P.HOLA, self.hola(0, 0), secuencia=10))
        self.assertEqual([p.cabecera.tipo for p in paquetes], [P.ACUSE, P.LISTAS, P.TARIFAS])
        listas = paquetes[1].cuerpo
        self.assertEqual((listas.version_desde, listas.version_hasta), (0, 2))  # foto completa
        self.assertEqual([e.a for e in listas.entradas], [4242, 77])
        self.assertEqual(paquetes[2].cuerpo.tarifas[0].nombre, "general")

    def test_hola_al_dia_solo_recibe_acuse(self):
        paquetes = self.respuestas(validador(P.HOLA, self.hola(2, S.VERSION_TARIFAS), secuencia=11))
        self.assertEqual([p.cabecera.tipo for p in paquetes], [P.ACUSE])

    def test_varios_paquetes_en_una_peticion(self):
        cuerpo = validador(P.HOLA, self.hola(2, S.VERSION_TARIFAS), secuencia=20) + \
            validador(P.EVENTOS, P.Eventos((evento(1),)), secuencia=21)
        paquetes = self.respuestas(cuerpo)
        self.assertEqual([p.cabecera.tipo for p in paquetes], [P.ACUSE, P.ACUSE])

    def test_firma_mala_y_validador_desconocido_se_ignoran(self):
        malo = bytearray(validador(P.HOLA, self.hola(2, S.VERSION_TARIFAS), secuencia=30))
        malo[-1] ^= 1
        antes = self.estado.paquetes_invalidos
        self.assertEqual(self.post(bytes(malo)), b"")
        self.assertEqual(self.post(validador(P.HOLA, self.hola(2, 0), secuencia=31, origen=999)), b"")
        self.assertEqual(self.estado.paquetes_invalidos, antes + 2)

    def test_peticiones_mal_hechas(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post(b"hola", tipo="text/plain")
        self.assertEqual(e.exception.code, 415)
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post(b"basura que no es C1")
        self.assertEqual(e.exception.code, 400)

    def test_la_clave_publica_exportada_verifica_sus_firmas(self):
        publico = S.punto_publico(self.privada)
        self.assertEqual(len(publico), 65)
        self.assertEqual(publico[0], 0x04)
        self.assertIn("CLAVE_SERVIDOR[65]", S.como_c(publico))


if __name__ == "__main__":
    unittest.main(verbosity=2)
