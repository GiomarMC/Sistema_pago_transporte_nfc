"""Interfaz TarjetaFisica (contrato C3) y su implementación para la NTAG215 (M1-01)."""

import io
import re
import subprocess
import sys
import typing
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock

import validador
from tarjeta import base, formato, ntag215
from tarjeta.base import (OFFSET_REGISTROS, TAM_AREA, AutenticacionRechazada, ErrorTarjeta,
                          Personalizable, TarjetaAbierta, TarjetaAjena, TarjetaFisica,
                          TipoNoSoportado, abrir_tarjeta)
from validador_local import BDValidador

from tests.apoyo215 import UID_PRUEBA, EntornoTarjetas, forma

TRANSPORTE = Path(__file__).resolve().parents[1]
NTAG213 = bytes.fromhex("0004040201000F03")
# Programas que siguen hablando con la NTAG215 por comandos nativos: el puente
# de la ESP32 reenvía comandos crudos y simular_tarjetas emula ese mismo puente.
PERMITIDOS_NTAG215 = {"puente_nfc.py", "simular_tarjetas.py"}
_IMPORTA_NTAG215 = re.compile(r"^\s*(from\s+tarjeta\.ntag215\s+import|import\s+tarjeta\.ntag215"
                              r"|from\s+tarjeta\s+import\s+[^#\n]*\bntag215\b)", re.MULTILINE)


class ImportsTests(unittest.TestCase):
    def test_ningun_programa_importa_ntag215_salvo_la_lista_blanca(self):
        infractores = sorted(p.name for p in TRANSPORTE.glob("*.py")
                             if p.name not in PERMITIDOS_NTAG215
                             and _IMPORTA_NTAG215.search(p.read_text(encoding="utf-8")))
        self.assertEqual(infractores, [])

    def test_la_expresion_detecta_las_tres_formas_de_importar(self):
        for linea in ("from tarjeta.ntag215 import NTAG215", "import tarjeta.ntag215",
                      "from tarjeta import claves, ntag215"):
            self.assertRegex(linea, _IMPORTA_NTAG215)
        self.assertNotRegex("from tarjeta.base import abrir_tarjeta", _IMPORTA_NTAG215)

    def test_paquete_y_programas_se_importan_sin_pyscard(self):
        codigo = ("import sys\n"
                  "sys.modules['smartcard'] = None  # cualquier import de pyscard falla\n"
                  "import tarjeta.base, tarjeta.ntag215, tarjeta.lector, tarjeta.virtual215\n"
                  "import emitir_tarjeta, leer_tarjeta, recargar, restablecer_tarjeta, validador\n")
        resultado = subprocess.run([sys.executable, "-c", codigo], cwd=TRANSPORTE,
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)

    def test_error_tarjeta_es_la_misma_clase_en_base_y_ntag215(self):
        # puente_nfc.py y simular_tarjetas.py la siguen importando de ntag215
        self.assertIs(ntag215.ErrorTarjeta, base.ErrorTarjeta)


class ContratoTests(unittest.TestCase):
    def test_contador_declara_int_o_none(self):
        self.assertEqual(typing.get_type_hints(TarjetaFisica.contador)["return"], int | None)

    def test_offsets_de_c3_coinciden_con_las_paginas_de_la_ntag215(self):
        inicio = formato.PAG_INICIO * 4
        self.assertEqual(TAM_AREA, formato.TAM_AREA)
        self.assertEqual(formato.PAG_CABECERA * 4 - inicio, 0)
        self.assertEqual(tuple(p * 4 - inicio for p in formato.PAG_REGISTROS), OFFSET_REGISTROS)

    def test_jerarquia_de_errores(self):
        for clase in (TipoNoSoportado, AutenticacionRechazada, TarjetaAjena):
            self.assertTrue(issubclass(clase, ErrorTarjeta), clase)


class Tarjeta215Tests(EntornoTarjetas):
    def abrir_emitida(self):
        conexion = self.tarjeta_de_fabrica()
        t = abrir_tarjeta(conexion)
        t.personalizar(bytes(range(TAM_AREA)))
        t.reiniciar_sesion()
        conexion.registro.clear()
        return conexion, t

    def test_cumple_los_protocolos(self):
        t = abrir_tarjeta(self.tarjeta_de_fabrica())
        self.assertIsInstance(t, TarjetaFisica)
        self.assertIsInstance(t, TarjetaAbierta)
        self.assertIsInstance(t, Personalizable)
        self.assertEqual((t.tipo, t.uid), ("NTAG215", UID_PRUEBA))

    def test_tipo_no_soportado_cierra_la_tarjeta_y_conserva_el_uid(self):
        conexion = self.tarjeta_de_fabrica()
        conexion.version = NTAG213
        with self.assertRaises(TipoNoSoportado) as e:
            abrir_tarjeta(conexion)
        self.assertEqual(e.exception.uid, UID_PRUEBA)
        self.assertEqual(str(e.exception), "no es una NTAG215")
        self.assertTrue(forma(conexion.registro).endswith("REL SEL"))

    def test_retirada_durante_get_version_no_es_tipo_no_soportado(self):
        conexion = self.tarjeta_de_fabrica()
        conexion.apdus_hasta_retirar = 1  # responde al UID y se va
        with self.assertRaises(ErrorTarjeta) as e:
            abrir_tarjeta(conexion)
        self.assertNotIsInstance(e.exception, TipoNoSoportado)

    def test_cumple_el_protocolo_personalizable_completo(self):
        self.assertIsInstance(abrir_tarjeta(self.tarjeta_de_fabrica()), base.TarjetaPersonalizable)

    def test_contrasena_inyectable(self):
        contrasena = Mock(return_value=(b"\x01\x02\x03\x04", b"\xAA\xBB"))
        t = abrir_tarjeta(self.tarjeta_de_fabrica(), contrasena=contrasena)
        t.personalizar(bytes(TAM_AREA))
        contrasena.assert_called_with(UID_PRUEBA)

    def test_autenticar_tarjeta_de_fabrica_es_rechazada(self):
        t = abrir_tarjeta(self.tarjeta_de_fabrica())
        with self.assertRaises(AutenticacionRechazada):
            t.autenticar()

    def test_autenticar_con_pack_distinto_es_tarjeta_ajena(self):
        conexion, t = self.abrir_emitida()
        conexion.pack = b"\x00\x01"
        with self.assertRaises(TarjetaAjena):
            t.autenticar()

    def test_contador_es_int_de_24_bits(self):
        _, t = self.abrir_emitida()
        t.autenticar()
        t.leer_area()
        contador = t.contador()
        self.assertIs(type(contador), int)
        self.assertTrue(0 <= contador <= 0xFFFFFF)

    def test_leer_area_devuelve_72_bytes(self):
        _, t = self.abrir_emitida()
        t.autenticar()
        self.assertEqual(t.leer_area(), bytes(range(TAM_AREA)))

    def test_escrituras_caen_en_los_offsets_de_c3(self):
        conexion, t = self.abrir_emitida()
        t.autenticar()
        cabecera, reg_a, reg_b = b"\xC1" * 24, b"\xA1" * 20, b"\xB1" * 20
        t.escribir_cabecera(cabecera)
        t.escribir_registro(0, reg_a)
        t.escribir_registro(1, reg_b)
        area = t.leer_area()
        self.assertEqual(area[0:24], cabecera)
        self.assertEqual(area[OFFSET_REGISTROS[0]:OFFSET_REGISTROS[0] + 20], reg_a)
        self.assertEqual(area[OFFSET_REGISTROS[1]:OFFSET_REGISTROS[1] + 20], reg_b)
        self.assertEqual(area[24:32], bytes(range(24, 32)))  # reservado intacto

    def test_tamanos_e_indices_invalidos(self):
        _, t = self.abrir_emitida()
        t.autenticar()
        for llamada in (lambda: t.escribir_cabecera(bytes(23)),
                        lambda: t.escribir_registro(0, bytes(21)),
                        lambda: t.escribir_registro(2, bytes(20)),
                        lambda: t.personalizar(bytes(71))):
            with self.assertRaises(ValueError):
                llamada()

    def test_escribir_registro_retirada_lanza_error_tarjeta(self):
        conexion, t = self.abrir_emitida()
        t.autenticar()
        conexion.escrituras_hasta_retirar = 1
        with self.assertRaises(ErrorTarjeta):
            t.escribir_registro(1, bytes(20))

    def test_personalizar_y_restablecer(self):
        conexion = self.tarjeta_de_fabrica()
        t = abrir_tarjeta(conexion)
        self.assertFalse(t.protegida())
        t.personalizar(bytes(TAM_AREA))
        t.reiniciar_sesion()
        self.assertTrue(t.protegida())
        t.autenticar()  # protegida: la configuración solo se lee autenticado
        self.assertFalse(t.configuracion_bloqueada())
        t.restablecer()
        t.reiniciar_sesion()
        self.assertFalse(t.protegida())

    def test_configuracion_bloqueada_impide_personalizar(self):
        conexion = self.tarjeta_de_fabrica()
        conexion.memoria[ntag215.PAG_CFG1 * 4] |= ntag215.CFGLCK
        t = abrir_tarjeta(conexion)
        self.assertTrue(t.configuracion_bloqueada())
        with self.assertRaises(ErrorTarjeta):
            t.personalizar(bytes(TAM_AREA))
        self.assertEqual(conexion.paginas(formato.PAG_INICIO, formato.PAG_FIN), bytes(TAM_AREA))


class AtenderToqueTests(EntornoTarjetas):
    def atender(self, conexion):
        bdv = BDValidador(101)
        self.addCleanup(bdv.bd.close)
        pantalla = Mock()
        salida = io.StringIO()
        with redirect_stdout(salida):
            validador.atender_toque(conexion, bdv, pantalla)
        return salida.getvalue(), pantalla

    def test_tarjeta_de_otro_tipo_se_rechaza_como_no_emitida(self):
        conexion = self.tarjeta_de_fabrica()
        conexion.version = NTAG213
        salida, pantalla = self.atender(conexion)
        self.assertIn("[RECHAZADA] Tarjeta no válida (No emitida por el sistema)", salida)
        pantalla.rechazo.assert_called_once_with("Tarjeta no válida", "No emitida por el sistema")

    def test_tarjeta_de_fabrica_se_rechaza_como_no_emitida(self):
        salida, pantalla = self.atender(self.tarjeta_de_fabrica())
        self.assertIn("[RECHAZADA] Tarjeta no válida (No emitida por el sistema)", salida)
        pantalla.rechazo.assert_called_once()

    def test_tarjeta_retirada_durante_get_version_pide_acercarla_de_nuevo(self):
        conexion = self.tarjeta_de_fabrica()
        conexion.apdus_hasta_retirar = 1
        salida, pantalla = self.atender(conexion)
        self.assertIn("[ERROR]", salida)
        pantalla.aviso.assert_called_once_with("Acerque de nuevo", "Lectura fallida")
        pantalla.rechazo.assert_not_called()

    def test_tarjeta_retirada_al_leer_el_uid_es_error_de_lectura(self):
        conexion = self.tarjeta_de_fabrica()
        conexion.retirar()
        salida, pantalla = self.atender(conexion)
        self.assertIn("[ERROR]", salida)
        pantalla.aviso.assert_called_once_with("Acerque de nuevo", "Lectura fallida")


if __name__ == "__main__":
    unittest.main()
