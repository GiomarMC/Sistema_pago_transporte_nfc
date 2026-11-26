"""
Regresión de los programas con una NTAG215 (virtual): emitir, cobrar, recargar
en punto, leer y restablecer deben seguir comportándose como antes de la
interfaz TarjetaFisica (M1-01).

Las trazas esperadas (comandos enviados a la tarjeta, sin los datos) se
obtuvieron ejecutando estas mismas pruebas con el código anterior al cambio.
Diferencias deliberadas respecto a aquel código, marcadas abajo con [M1-01]:
  - abrir_tarjeta() identifica el chip con GET_VERSION (V) también en el
    validador y en la recarga, que antes no lo comprobaban;
  - emitir lee la marca de emisión con leer_area() (F8-25 en vez de F8-8) y
    Tarjeta215.personalizar() relee CFG0/CFG1 (F131-132) justo antes de
    grabar, en vez de reutilizar la lectura hecha antes de llamar al servidor.
Cambios de flujo, también deliberados:
  - una tarjeta de otro tipo se detecta al abrirla: restablecer_tarjeta sale
    antes de pedir confirmación (antes preguntaba y luego fallaba) y cierra la
    tarjeta también al responder "no";
  - en el validador, perder la comunicación durante GET_VERSION o al leer el
    UID muestra "Acerque de nuevo" (antes ese caso no existía o cerraba el
    programa).
Y uno de develop, posterior (#240, PR #242): la recarga vuelve a autenticarse
(PWD) justo antes de grabar, porque el ACR122U pierde la autenticación mientras
se espera al servidor. Marcado con [#240].
El resto de cada traza, los datos grabados y los mensajes no cambian.
"""

import os
import unittest
from unittest.mock import patch

import emitir_tarjeta
import leer_tarjeta
import recargar
import restablecer_tarjeta
import validador
from api_cliente import SinConexion
from tarjeta import claves, formato
from tarjeta.virtual215 import NFC_CNT_EN, NFC_CNT_PWD_PROT, PROT, VERSION_NTAG215
from validador_local import BDValidador

from tests.apoyo215 import UID_PRUEBA, EntornoTarjetas, abrir, con_tarjeta, forma

RESERVA = {"tarjeta": 7, "cuenta": {"id": 3, "nombre": "Ana", "documento": "1",
                                    "tarifa_nombre": "general"},
           "tarifa": 1, "saldo": 1000, "recarga_inicial": 0}

AREA_DATOS = " ".join(f"W{p}" for p in range(formato.PAG_INICIO, formato.PAG_FIN + 1))
TRAZA_EMITIR = ("UID V R8 F8-25 F131-132 F131-132 " + AREA_DATOS +            # [M1-01]
                " W133 W134 W132 W131 REL SEL R8 REL SEL PWD F8-25 CNT REL SEL")
TRAZA_COBRAR = "UID V PWD F8-25 CNT W21 W22 W23 W24 W25 F21-25 REL SEL"     # [M1-01] V
TRAZA_RECARGAR = "UID V PWD F8-25 CNT PWD W21 W22 W23 W24 W25 F21-25 REL SEL"  # [M1-01] V · [#240] PWD
TRAZA_LEER = "UID V R8 REL SEL PWD F8-25 CNT REL SEL"
TRAZA_RESTABLECER = ("UID V R8 REL SEL PWD F131-132 " + AREA_DATOS +
                     " W131 W132 W133 W134 REL SEL R8 REL SEL")


class ApiFalsa:
    """Servidor simulado: registra las llamadas y responde lo mínimo."""

    def __init__(self, abono=0):
        self.llamadas = []
        self.abono = abono

    def __call__(self, metodo, ruta, datos=None):
        self.llamadas.append((metodo, ruta, datos))
        if ruta == "/api/emisiones/":
            return RESERVA
        if ruta.endswith("/confirmar/"):
            return {"bloqueadas": []}
        if ruta == "/api/recargas/":
            return {"abono": self.abono, "hasta_seq": 1, "cuenta": RESERVA["cuenta"],
                    "recarga": {"id": 9} if self.abono else None}
        if ruta == "/api/recargas/entregas/":
            return {}
        raise SinConexion("sin red")

    def rutas(self):
        return [ruta for _, ruta, _ in self.llamadas]


class Regresion215(EntornoTarjetas):
    def emitir(self, conexion, *extra):
        api = ApiFalsa()
        with con_tarjeta(emitir_tarjeta, conexion), patch("api_cliente.llamar", side_effect=api):
            salida, codigo = self.ejecutar(emitir_tarjeta.main, "--nombre", "Ana", "--dni", "1",
                                           "--tarifa", "general", "--sin-comprobante", *extra)
        return salida, codigo, api

    def emitida(self):
        conexion = self.tarjeta_de_fabrica()
        _, codigo, _ = self.emitir(conexion)
        self.assertIsNone(codigo)
        conexion.registro.clear()
        return conexion

    def bdv(self):
        bdv = BDValidador(101)
        self.addCleanup(bdv.bd.close)
        return bdv

    def estado_en_memoria(self, conexion):
        area = conexion.paginas(formato.PAG_INICIO, formato.PAG_FIN)
        return formato.decodificar(area, conexion.uid, claves.clave_firma(conexion.uid))

    def cobrar(self, conexion, bdv):
        t = abrir(conexion)
        try:
            return validador.cobrar(t, bdv)
        finally:
            t.cerrar()

    # --- Emisión ----------------------------------------------------------------

    def test_emitir_tarjeta_de_fabrica(self):
        conexion = self.tarjeta_de_fabrica()
        salida, codigo, api = self.emitir(conexion)

        self.assertIsNone(codigo, salida)
        self.assertEqual(forma(conexion.registro), TRAZA_EMITIR)
        self.assertIn("[OK] Tarjeta emitida y verificada", salida)
        self.assertIn("Contador:    1", salida)
        pwd, pack = claves.contrasena(UID_PRUEBA)
        self.assertEqual((conexion.pwd, conexion.pack), (pwd, pack))
        self.assertEqual(conexion.auth0, formato.PAG_INICIO)
        self.assertEqual(conexion.access & (PROT | NFC_CNT_EN | NFC_CNT_PWD_PROT),
                         PROT | NFC_CNT_EN | NFC_CNT_PWD_PROT)
        self.assertEqual(self.estado_en_memoria(conexion).registro.saldo, 1000)
        self.assertEqual(api.llamadas[-1], ("POST", "/api/emisiones/7/confirmar/", {"contador": 1}))

    def test_emitir_tarjeta_ya_emitida_sin_forzar(self):
        conexion = self.emitida()
        salida, codigo, api = self.emitir(conexion)
        self.assertEqual(codigo, "[X] No se emitió la tarjeta: la tarjeta ya está emitida "
                                 "(usa --forzar para reemitirla)")
        self.assertEqual(api.llamadas, [])

    def test_emitir_tarjeta_con_contrasena_ajena(self):
        conexion = self.emitida()
        conexion.pwd = os.urandom(4)
        _, codigo, api = self.emitir(conexion)
        self.assertEqual(codigo, "[X] No se emitió la tarjeta: la tarjeta tiene una contraseña "
                                 "que no es de este sistema")
        self.assertEqual(api.llamadas, [])

    def test_emitir_tarjeta_que_no_es_ntag215(self):
        conexion = self.tarjeta_de_fabrica()
        conexion.version = bytes.fromhex("0004040201000F03")  # NTAG213
        salida, codigo, api = self.emitir(conexion)
        self.assertIn("Tarjeta detectada: UID 04A1B2C3D4E5F6", salida)
        self.assertEqual(codigo, "[X] No se emitió la tarjeta: no es una NTAG215")
        self.assertEqual(api.llamadas, [])

    def test_reemitir_con_forzar(self):
        conexion = self.emitida()
        salida, codigo, _ = self.emitir(conexion, "--forzar")
        self.assertIsNone(codigo, salida)
        self.assertEqual(self.estado_en_memoria(conexion).registro.saldo, 1000)

    # --- Cobro ------------------------------------------------------------------

    def test_cobrar_pasaje(self):
        conexion = self.emitida()
        bdv = self.bdv()
        cobro = self.cobrar(conexion, bdv)

        self.assertEqual(forma(conexion.registro), TRAZA_COBRAR)
        self.assertEqual(cobro.saldo, 870)
        estado = self.estado_en_memoria(conexion)
        self.assertEqual((estado.vigente, estado.registro.saldo, estado.registro.operacion), (1, 870, 2))
        viaje = bdv.bd.execute("SELECT monto, saldo_final, operacion, contador FROM eventos "
                               "WHERE tipo = 'viaje'").fetchone()
        self.assertEqual(tuple(viaje), (130, 870, 2, 2))

    def test_cobrar_tarjeta_de_fabrica(self):
        with self.assertRaises(validador.Rechazo) as e:
            self.cobrar(self.tarjeta_de_fabrica(), self.bdv())
        self.assertEqual((e.exception.motivo, e.exception.detalle),
                         ("Tarjeta no válida", "No emitida por el sistema"))

    def test_cobrar_tarjeta_con_pack_distinto(self):
        conexion = self.emitida()
        conexion.pack = b"\x00\x01"
        with self.assertRaises(validador.Rechazo) as e:
            self.cobrar(conexion, self.bdv())
        self.assertEqual(e.exception.detalle, "No reconocida")

    def test_cobrar_tarjeta_retirada_durante_la_escritura(self):
        conexion = self.emitida()
        conexion.escrituras_hasta_retirar = 2
        with self.assertRaises(validador.Rechazo) as e:
            self.cobrar(conexion, self.bdv())
        self.assertTrue(e.exception.aviso)
        self.assertEqual(e.exception.motivo, "Acerque de nuevo")
        estado = self.estado_en_memoria(conexion)
        self.assertEqual((estado.vigente, estado.registro.saldo), (0, 1000))

    def test_cobrar_tarjeta_en_lista_negra_graba_el_bloqueo(self):
        conexion = self.emitida()
        bdv = self.bdv()
        bdv.bd.execute("INSERT INTO lista_negra (tarjeta_id) VALUES (7)")
        with self.assertRaises(validador.Rechazo) as e:
            self.cobrar(conexion, bdv)
        self.assertEqual(e.exception.motivo, "Tarjeta bloqueada")
        self.assertEqual(self.estado_en_memoria(conexion).cabecera.estado, formato.BLOQUEADA)

    # --- Recarga en punto -------------------------------------------------------

    def test_recarga_en_punto(self):
        conexion = self.emitida()
        api = ApiFalsa(abono=500)
        t = abrir(conexion)
        with patch("api_cliente.llamar", side_effect=api):
            antes, despues, abono, _ = recargar.recargar_tarjeta(t, 500)
        t.cerrar()

        self.assertEqual(forma(conexion.registro), TRAZA_RECARGAR)
        self.assertEqual((antes, despues, abono), (1000, 1500, 500))
        self.assertEqual(api.llamadas[-1], ("POST", "/api/recargas/entregas/",
                                            {"tarjeta": 7, "hasta_seq": 1, "operacion": 2,
                                             "contador": 2, "recarga": 9}))

    def test_recarga_en_tarjeta_no_emitida(self):
        t = abrir(self.tarjeta_de_fabrica())
        with self.assertRaises(recargar.ErrorRecarga) as e:
            recargar.recargar_tarjeta(t, 500)
        self.assertEqual(str(e.exception), "tarjeta no válida o no emitida por este sistema")

    # --- Lectura y restablecimiento ----------------------------------------------

    def test_leer_tarjeta(self):
        conexion = self.emitida()
        with con_tarjeta(leer_tarjeta, conexion), patch("api_cliente.llamar", side_effect=ApiFalsa()):
            salida, codigo = self.ejecutar(leer_tarjeta.main)
        self.assertIsNone(codigo, salida)
        self.assertEqual(forma(conexion.registro), TRAZA_LEER)
        self.assertIn("Contador NFC: 2", salida)
        self.assertIn("Registro A:     S/ 10.00  op 1", salida)

    def test_leer_tarjeta_que_no_es_ntag215(self):
        conexion = self.tarjeta_de_fabrica()
        conexion.version = bytes.fromhex("0004040201000F03")
        with con_tarjeta(leer_tarjeta, conexion):
            _, codigo = self.ejecutar(leer_tarjeta.main)
        self.assertEqual(codigo, "UID 04A1B2C3D4E5F6: no es una NTAG215")

    def test_restablecer_tarjeta(self):
        conexion = self.emitida()
        with con_tarjeta(restablecer_tarjeta, conexion), \
                patch("api_cliente.llamar", side_effect=ApiFalsa()):
            salida, codigo = self.ejecutar(restablecer_tarjeta.main, "--si")
        self.assertIsNone(codigo, salida)
        self.assertEqual(forma(conexion.registro), TRAZA_RESTABLECER)
        self.assertIn("[OK] Tarjeta restablecida a valores de fábrica", salida)
        self.assertEqual((conexion.auth0, conexion.access & (PROT | NFC_CNT_EN | NFC_CNT_PWD_PROT)),
                         (0xFF, 0))
        self.assertEqual(conexion.pwd, b"\xff\xff\xff\xff")
        self.assertEqual(conexion.paginas(formato.PAG_INICIO, formato.PAG_FIN), bytes(formato.TAM_AREA))

    def test_restablecer_tarjeta_que_no_es_ntag215(self):
        conexion = self.tarjeta_de_fabrica()
        conexion.version = bytes.fromhex("0004040201000F03")
        with con_tarjeta(restablecer_tarjeta, conexion):
            salida, codigo = self.ejecutar(restablecer_tarjeta.main, "--si")
        self.assertIn("Tarjeta detectada: UID 04A1B2C3D4E5F6", salida)
        self.assertEqual(codigo, "[X] No se pudo restablecer: no es una NTAG215")


if __name__ == "__main__":
    unittest.main()
