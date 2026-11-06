import json
from dataclasses import replace

from django.test import SimpleTestCase

from transporte.management.commands import generar_vectores_c1 as vec
from transporte.sync_v2 import paquetes as p

PUBLICA = vec.clave_ecdsa().public_key()


def clave_de(n):
    return vec.CLAVE_HMAC if n == vec.VALIDADOR else None


def leer(datos):
    return p.desempaquetar(datos, clave_de, PUBLICA)


def caso(nombre):
    return next((c, b) for n, _, c, b in vec.casos_validos() if n == nombre)


class IdaYVueltaTests(SimpleTestCase):
    def test_todos_los_tipos(self):
        for nombre, _, cabecera, cuerpo in vec.casos_validos():
            with self.subTest(nombre):
                paquete = leer(vec.empaquetar(cabecera, cuerpo))
                self.assertEqual(paquete, p.Paquete(cabecera, cuerpo))

    def test_cubre_los_cinco_tipos(self):
        tipos = {c.tipo for _, _, c, _ in vec.casos_validos()}
        self.assertEqual(tipos, {p.EVENTOS, p.HOLA, p.ACUSE, p.LISTAS, p.TARIFAS})

    def test_firma_ecdsa_determinista(self):
        cabecera, cuerpo = caso("acuse")
        self.assertEqual(vec.empaquetar(cabecera, cuerpo), vec.empaquetar(cabecera, cuerpo))


class TamanosTests(SimpleTestCase):
    """Tamaños byte a byte según C1: total = 20 + largo + (16 o 64)."""

    def medir(self, nombre):
        cabecera, cuerpo = caso(nombre)
        datos = vec.empaquetar(cabecera, cuerpo)
        return len(datos), int.from_bytes(datos[16:18], "little")

    def test_cabecera(self):
        self.assertEqual(len(p.Cabecera(p.HOLA, 1, 0, 1, 0).a_bytes(32)), 20)

    def test_evento(self):
        self.assertEqual(len(vec._viaje(1).a_bytes()), 64)

    def test_eventos(self):
        self.assertEqual(self.medir("eventos_1"), (20 + 4 + 64 + 16, 4 + 64))
        self.assertEqual(self.medir("eventos_30"), (20 + 4 + 30 * 64 + 16, 4 + 30 * 64))

    def test_hola(self):
        self.assertEqual(self.medir("hola"), (20 + 32 + 16, 32))

    def test_acuse(self):
        self.assertEqual(self.medir("acuse"), (20 + 8 + 64, 8))

    def test_listas(self):
        self.assertEqual(self.medir("listas_normal"), (20 + 12 + 3 * 16 + 64, 12 + 3 * 16))
        self.assertEqual(self.medir("listas_vacia"), (20 + 12 + 64, 12))

    def test_tarifas(self):
        self.assertEqual(self.medir("tarifas"), (20 + 8 + 3 * 24 + 64, 8 + 3 * 24))

    def test_listas_con_120_entradas_cabe_en_2048(self):
        listas = p.Listas(0, 1, [p.EntradaLista(p.BLOQUEO, p.AGREGAR, a=i) for i in range(120)])
        datos = vec.empaquetar(p.Cabecera(p.LISTAS, 0, 1, 1, 0), listas)
        self.assertLessEqual(len(datos), p.TAM_MAXIMO)
        self.assertEqual(leer(datos).cuerpo, listas)

    def test_campos_en_su_offset(self):
        cabecera, cuerpo = caso("eventos_1")
        datos = vec.empaquetar(cabecera, cuerpo)
        self.assertEqual(datos[:4], b"SP\x01\x01")
        self.assertEqual(int.from_bytes(datos[4:6], "little"), vec.VALIDADOR)
        evento = datos[24:88]
        self.assertEqual(evento[5], 7)                                  # largo del UID
        self.assertEqual(evento[16:26], bytes.fromhex("04AABBCCDDEEFF000000"))
        self.assertEqual(int.from_bytes(evento[28:32], "little", signed=True), -80)


class InvalidosTests(SimpleTestCase):
    def test_vectores_invalidos(self):
        for motivo, _, datos in vec.casos_invalidos():
            with self.subTest(motivo):
                with self.assertRaises(p.PaqueteInvalido) as e:
                    leer(datos)
                self.assertEqual(e.exception.motivo, motivo)

    def test_validador_desconocido(self):
        cabecera, cuerpo = caso("hola")
        datos = p.empaquetar(replace(cabecera, origen=999), cuerpo, clave_hmac=vec.CLAVE_HMAC)
        with self.assertRaises(p.PaqueteInvalido) as e:
            leer(datos)
        self.assertEqual(e.exception.motivo, "validador")

    def test_clave_hmac_de_otro_validador(self):
        cabecera, cuerpo = caso("hola")
        datos = p.empaquetar(cabecera, cuerpo, clave_hmac=bytes(32))
        with self.assertRaises(p.PaqueteInvalido) as e:
            leer(datos)
        self.assertEqual(e.exception.motivo, "firma")

    def test_lista_firmada_con_otra_clave_ecdsa(self):
        cabecera, cuerpo = caso("listas_urgente")
        otra = p.ec.derive_private_key(12345, p.ec.SECP256R1())
        with self.assertRaises(p.PaqueteInvalido) as e:
            leer(p.empaquetar(cabecera, cuerpo, clave_ecdsa=otra))
        self.assertEqual(e.exception.motivo, "firma")

    def test_validador_no_puede_mandar_listas(self):
        # Un bus no puede hacerse pasar por el servidor firmando LISTAS con su HMAC
        _, cuerpo = caso("listas_urgente")
        datos = bytearray(vec.empaquetar(*caso("hola")))
        datos[3] = p.LISTAS
        with self.assertRaises(p.PaqueteInvalido) as e:
            leer(bytes(datos))
        self.assertEqual(e.exception.motivo, "origen")
        with self.assertRaises(ValueError):
            p.empaquetar(p.Cabecera(p.LISTAS, vec.VALIDADOR, 0, 1, 0), cuerpo,
                         clave_hmac=vec.CLAVE_HMAC)

    def test_tipo_desconocido(self):
        datos = bytearray(vec.empaquetar(*caso("hola")))
        datos[3] = 0x05
        with self.assertRaises(p.PaqueteInvalido) as e:
            leer(bytes(datos))
        self.assertEqual(e.exception.motivo, "tipo")

    def test_cortado(self):
        datos = vec.empaquetar(*caso("hola"))
        for n in (0, 10, len(datos) - 1):
            with self.subTest(n), self.assertRaises(p.PaqueteInvalido) as e:
                leer(datos[:n])
            self.assertEqual(e.exception.motivo, "largo")

    def test_cuerpo_incoherente_bien_firmado(self):
        # n dice 2 eventos pero solo viaja 1: la firma es válida y aun así se rechaza
        cabecera, _ = caso("eventos_1")
        cuerpo = bytes([2, 0, 0, 0]) + vec._viaje(1).a_bytes()
        firmado = cabecera.a_bytes(len(cuerpo)) + cuerpo
        datos = firmado + p._firma_hmac(vec.CLAVE_HMAC, firmado)
        with self.assertRaises(p.PaqueteInvalido) as e:
            leer(datos)
        self.assertEqual(e.exception.motivo, "cuerpo")

    def test_limites_al_empaquetar(self):
        cabecera, _ = caso("eventos_1")
        for eventos in ([], [vec._viaje(i) for i in range(31)]):
            with self.subTest(len(eventos)), self.assertRaises(ValueError):
                vec.empaquetar(cabecera, p.Eventos(eventos))
        with self.assertRaises(ValueError):
            vec.empaquetar(cabecera, p.Hola(*[0] * 10))       # cabecera y cuerpo no coinciden


class SepararTests(SimpleTestCase):
    def test_parte_paquetes_concatenados(self):
        todos = [vec.empaquetar(c, b) for _, _, c, b in vec.casos_validos()]
        self.assertEqual(p.separar(b"".join(todos)), todos)
        self.assertEqual(p.separar(b""), [])

    def test_flujo_cortado(self):
        hola = vec.empaquetar(*caso("hola"))
        for flujo in (hola + hola[:10], hola + hola[:-1]):
            with self.subTest(len(flujo)), self.assertRaises(p.PaqueteInvalido) as e:
                p.separar(flujo)
            self.assertEqual(e.exception.motivo, "largo")

    def test_basura_entre_paquetes(self):
        hola = vec.empaquetar(*caso("hola"))
        with self.assertRaises(p.PaqueteInvalido) as e:
            p.separar(hola + b"XX" + hola[2:])
        self.assertEqual(e.exception.motivo, "magia")


class VectoresDelRepositorioTests(SimpleTestCase):
    """docs/contratos/vectores_c1.json debe estar al día con la biblioteca."""

    def setUp(self):
        ruta = vec.ruta_por_defecto()
        if not ruta.exists():
            self.skipTest(f"no existe {ruta}")
        self.vectores = json.loads(ruta.read_text())

    def test_coinciden_con_lo_que_genera_la_biblioteca(self):
        self.assertEqual(self.vectores, vec.construir_vectores(),
                         "regenerar con: python manage.py generar_vectores_c1")

    def test_cantidad_minima(self):
        self.assertGreaterEqual(len(self.vectores["validos"]), 8)
        self.assertEqual({v["motivo"] for v in self.vectores["invalidos"]},
                         {"magia", "version", "largo", "firma"})

    def test_los_hex_se_leen(self):
        for v in self.vectores["validos"]:
            with self.subTest(v["nombre"]):
                leer(bytes.fromhex(v["hex"]))
