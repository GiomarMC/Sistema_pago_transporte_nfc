#!/usr/bin/env python3
"""Servidor C1 falso: probar el sincronizador v2 del validador sin el servidor real (M4-04).

Expone POST /api/v2/paquetes/ como el servidor de verdad: recibe paquetes C1 concatenados
(application/octet-stream), los verifica y decodifica con el módulo del servidor
(servidor_api/transporte/sync_v2/paquetes.py), los imprime y contesta con paquetes firmados:

    HOLA     -> ACUSE (+ LISTAS y TARIFAS si el validador lleva otras versiones)
    EVENTOS  -> ACUSE (hasta el último evento contiguo recibido)

No usa Django ni base de datos: el estado vive en memoria y se pierde al cerrarlo.
Sin autenticación (el servidor real pide el token del validador). Solo para pruebas.

Uso básico (ver pantalla_esp32/README.md):

    python3 herramientas/servidor_c1_falso.py --clave 107=<64 hex del validador 107>
    python3 herramientas/servidor_c1_falso.py --imprimir-clave-publica   # para clave_servidor.h

La clave HMAC de cada validador es la que figura en el servidor real (manage.py
clave_validador). La clave ECDSA del servidor falso se crea la primera vez en
herramientas/c1_falso_clave.pem; su parte pública debe estar en el firmware.
"""

import argparse
import importlib.util
import json
import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

RAIZ = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("paquetes_c1", RAIZ / "servidor_api/transporte/sync_v2/paquetes.py")
P = importlib.util.module_from_spec(_spec)
sys.modules["paquetes_c1"] = P  # los dataclass del módulo lo necesitan registrado para resolver sus tipos
_spec.loader.exec_module(P)

RUTA = "/api/v2/paquetes/"
TIPO_BINARIO = "application/octet-stream"
TAM_MAXIMO = 64 * 1024
VERSION_TARIFAS = 1_700_000_000  # fija: el servidor falso no las modifica
NOMBRES_TIPO = {P.EVENTOS: "EVENTOS", P.HOLA: "HOLA", P.ACUSE: "ACUSE", P.LISTAS: "LISTAS", P.TARIFAS: "TARIFAS"}


class Estado:
    """Lo que el servidor falso recuerda: eventos recibidos por validador y qué lista y tarifas ofrece."""

    def __init__(self, claves, clave_privada, tarifas, bloqueos, en_voz_alta=True):
        self.claves = claves                  # {n.º de validador: clave HMAC de 32 B}
        self.clave_privada = clave_privada
        self.tarifas = tuple(tarifas)         # (P.Tarifa, ...)
        self.bloqueos = tuple(bloqueos)       # ids de tarjeta que figuran en la lista negra
        self.en_voz_alta = en_voz_alta
        self.cerrojo = threading.Lock()
        self.recibidos = {}                   # {validador: {ids de evento}}
        self.contiguo = {}                    # {validador: mayor id tal que llegaron todos 1..id}
        self.secuencia = int(time.time())     # creciente y sin repetirse entre ejecuciones
        self.paquetes_recibidos = 0
        self.paquetes_invalidos = 0

    @property
    def version_listas(self):
        return len(self.bloqueos)  # cada tarjeta bloqueada es "un cambio": suficiente para probar

    def decir(self, texto):
        if self.en_voz_alta:
            print(f"{time.strftime('%H:%M:%S')}  {texto}", flush=True)

    def siguiente_secuencia(self):
        self.secuencia += 1
        return self.secuencia

    def firmar(self, tipo, destino, cuerpo):
        cab = P.Cabecera(tipo, origen=P.SERVIDOR, destino=destino, secuencia=self.siguiente_secuencia(),
                         creado=int(time.time()))
        return P.empaquetar(cab, cuerpo, clave_ecdsa=self.clave_privada)

    # --- procesamiento ------------------------------------------------------------------

    def procesar(self, datos):
        """Un paquete recibido -> lista de paquetes de respuesta (vacía si es inválido)."""
        with self.cerrojo:
            self.paquetes_recibidos += 1
            try:
                paquete = P.desempaquetar(datos, self.claves.get, clave_publica=None)
                if paquete.cabecera.destino != P.SERVIDOR:
                    raise P.PaqueteInvalido("destino", f"{paquete.cabecera.destino} (debe ser 0)")
            except P.PaqueteInvalido as e:
                self.paquetes_invalidos += 1
                self.decir(f"✗ paquete INVÁLIDO, se ignora: {e}")
                return []
            cab, cuerpo = paquete.cabecera, paquete.cuerpo
            self.decir(f"← {NOMBRES_TIPO[cab.tipo]:<7} de validador {cab.origen} (secuencia {cab.secuencia}, "
                       f"creado {cab.creado}): {self._resumen(cuerpo)}")
            if cab.tipo == P.EVENTOS:
                return self._eventos(cab.origen, cuerpo)
            if cab.tipo == P.HOLA:
                return self._hola(cab.origen, cuerpo)
            return []

    @staticmethod
    def _resumen(cuerpo):
        if isinstance(cuerpo, P.Hola):
            return (f"listas v{cuerpo.version_listas}, tarifas v{cuerpo.version_tarifas}, último evento "
                    f"{cuerpo.ultimo_evento}, pendientes {cuerpo.pendientes}, ajenos {cuerpo.ajenos}, enlace "
                    f"{cuerpo.enlace}, señal {cuerpo.senal}, bytes tx/rx {cuerpo.bytes_tx_hoy}/{cuerpo.bytes_rx_hoy}, "
                    f"firmware {cuerpo.version_firmware}")
        if isinstance(cuerpo, P.Eventos):
            ids = [e.id_evento for e in cuerpo.eventos]
            detalle = "; ".join(f"#{e.id_evento} tarjeta {e.id_tarjeta} monto {e.monto} saldo {e.saldo_final}"
                                for e in cuerpo.eventos[:3])
            return f"{len(ids)} evento(s), ids {ids[0]}..{ids[-1]} [{detalle}{'; ...' if len(ids) > 3 else ''}]"
        return repr(cuerpo)

    def _acuse(self, validador):
        hasta = self.contiguo.get(validador, 0)
        self.decir(f"→ ACUSE   para el validador {validador}: confirma hasta el evento {hasta}")
        return self.firmar(P.ACUSE, validador, P.Acuse(hasta, int(time.time())))

    def _eventos(self, validador, eventos):
        vistos = self.recibidos.setdefault(validador, set())
        for e in eventos.eventos:
            vistos.add(e.id_evento)
        contiguo = self.contiguo.get(validador, 0)
        while contiguo + 1 in vistos:
            contiguo += 1
        self.contiguo[validador] = contiguo
        return [self._acuse(validador)]

    def _hola(self, validador, hola):
        respuestas = [self._acuse(validador)]
        if hola.version_listas != self.version_listas:
            entradas = tuple(P.EntradaLista(P.BLOQUEO, P.AGREGAR, t) for t in self.bloqueos)
            self.decir(f"→ LISTAS  para el validador {validador}: foto completa, versión {self.version_listas}, "
                       f"{len(entradas)} bloqueo(s)")
            respuestas.append(self.firmar(P.LISTAS, validador, P.Listas(0, self.version_listas, entradas)))
        if hola.version_tarifas != VERSION_TARIFAS:
            self.decir(f"→ TARIFAS para el validador {validador}: {len(self.tarifas)} tarifa(s)")
            respuestas.append(self.firmar(P.TARIFAS, validador, P.Tarifas(VERSION_TARIFAS, self.tarifas)))
        return respuestas


def manejador(estado):
    class Manejador(BaseHTTPRequestHandler):
        def log_message(self, formato, *args):  # el servidor ya imprime lo que importa
            pass

        def _responder(self, codigo, cuerpo=b"", tipo="text/plain; charset=utf-8"):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_GET(self):
            texto = (f"Servidor C1 falso. POST {RUTA} con paquetes C1. "
                     f"Recibidos: {estado.paquetes_recibidos}, inválidos: {estado.paquetes_invalidos}.\n")
            self._responder(200, texto.encode())

        def do_POST(self):
            if self.path.split("?")[0] != RUTA:
                return self._responder(404, b"no existe\n")
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != TIPO_BINARIO:
                return self._responder(415, f"el cuerpo debe ser {TIPO_BINARIO}\n".encode())
            try:
                largo = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                largo = 0
            if largo <= 0 or largo > TAM_MAXIMO:
                return self._responder(400, b"cuerpo vacio o demasiado grande\n")
            cuerpo = self.rfile.read(largo)
            try:
                paquetes = P.separar(cuerpo)
            except P.PaqueteInvalido as e:
                estado.decir(f"✗ flujo mal formado: {e}")
                return self._responder(400, f"flujo de paquetes mal formado: {e}\n".encode())
            respuestas = []
            for datos in paquetes:
                respuestas += estado.procesar(datos)
            self._responder(200, b"".join(respuestas), TIPO_BINARIO)

    return Manejador


def cargar_o_crear_clave(ruta: pathlib.Path):
    if ruta.exists():
        clave = serialization.load_pem_private_key(ruta.read_bytes(), password=None)
        if not isinstance(clave, ec.EllipticCurvePrivateKey) or clave.curve.name != "secp256r1":
            raise SystemExit(f"{ruta} no es una clave ECDSA P-256")
        return clave
    clave = ec.generate_private_key(ec.SECP256R1())
    ruta.write_bytes(clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    ruta.chmod(0o600)
    print(f"Clave ECDSA nueva creada en {ruta} (solo para pruebas; no la subas al repositorio).", flush=True)
    return clave


def punto_publico(clave) -> bytes:
    return clave.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def como_c(datos: bytes) -> str:
    filas = ",\n    ".join(", ".join(f"0x{b:02X}" for b in datos[i:i + 13]) for i in range(0, len(datos), 13))
    return f"static const uint8_t CLAVE_SERVIDOR[{len(datos)}] = {{\n    {filas}}};"


def leer_claves(args):
    claves = {}
    if args.claves_json:
        for n, hexa in json.loads(pathlib.Path(args.claves_json).read_text()).items():
            claves[int(n)] = bytes.fromhex(hexa)
    for par in args.clave or []:
        n, _, hexa = par.partition("=")
        if not n.isdigit() or len(hexa) != 64:
            raise SystemExit(f"--clave espera N=<64 caracteres hexadecimales>, no {par!r}")
        claves[int(n)] = bytes.fromhex(hexa)
    return claves


def leer_tarifas(textos):
    tarifas = []
    for t in textos or ["1:130:general", "2:80:estudiante"]:
        try:
            codigo, precio, nombre = t.split(":", 2)
            tarifas.append(P.Tarifa(int(codigo), int(precio), nombre))
        except ValueError:
            raise SystemExit(f"--tarifa espera CODIGO:PRECIO_EN_CENTIMOS:NOMBRE, no {t!r}") from None
    return tarifas


def principal():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--direccion", default="0.0.0.0", help="dónde escuchar (por defecto todas, para que la ESP32 llegue)")
    ap.add_argument("--puerto", type=int, default=8001)
    ap.add_argument("--clave", action="append", metavar="N=HEX", help="clave HMAC (32 B en hex) del validador N; se puede repetir")
    ap.add_argument("--claves-json", metavar="ARCHIVO", help='JSON {"107": "<64 hex>", ...}')
    ap.add_argument("--clave-privada", default=str(pathlib.Path(__file__).with_name("c1_falso_clave.pem")),
                    help="clave ECDSA P-256 del servidor falso (se crea si no existe)")
    ap.add_argument("--imprimir-clave-publica", action="store_true",
                    help="escribe la clave pública del servidor (hex y como array C para clave_servidor.h) y sale")
    ap.add_argument("--tarifa", action="append", metavar="CODIGO:PRECIO:NOMBRE", help="tarifa que ofrece (por defecto 1:130:general y 2:80:estudiante)")
    ap.add_argument("--bloquear", action="append", type=int, metavar="ID_TARJETA", help="tarjeta que figura en la lista negra")
    ap.add_argument("--silencioso", action="store_true", help="no imprime los paquetes")
    args = ap.parse_args()

    clave_privada = cargar_o_crear_clave(pathlib.Path(args.clave_privada))
    if args.imprimir_clave_publica:
        publico = punto_publico(clave_privada)
        print(publico.hex())
        print()
        print(como_c(publico))
        return
    claves = leer_claves(args)
    if not claves:
        raise SystemExit("Falta al menos una clave: --clave 107=<64 hex> (la del validador, como en manage.py clave_validador)")

    estado = Estado(claves, clave_privada, leer_tarifas(args.tarifa), args.bloquear or [], not args.silencioso)
    servidor = ThreadingHTTPServer((args.direccion, args.puerto), manejador(estado))
    print(f"Servidor C1 falso en http://{args.direccion}:{servidor.server_port}{RUTA}  "
          f"(validadores {sorted(claves)}; Ctrl+C para cerrar)", flush=True)
    print("Clave pública ECDSA del servidor falso:", punto_publico(clave_privada).hex()[:32] + "…", flush=True)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor cerrado.")


if __name__ == "__main__":
    principal()
