#!/usr/bin/env python3
"""
Puente entre el lector ACR122U (en este ordenador) y el validador que corre en la
ESP32 (pantalla_esp32, entorno "validador").

El puente no tiene lógica: no sabe de saldos, firmas ni tarifas. Solo reenvía a
la tarjeta los comandos que pide la ESP32 y le devuelve las respuestas. Cuando la
ESP32 tenga su propio lector (PN532), este programa deja de hacer falta.

    python3 puente_nfc.py                                  # detecta el puerto de la ESP32
    python3 puente_nfc.py --puerto COM3                    # o se indica (Windows: COMx,
                                                           # macOS: /dev/cu.usbserial-...)
    python3 puente_nfc.py --configurar --validador 103     # primera vez: guarda en la
                                                           # microSD el n.º de validador
                                                           # y la clave maestra

Protocolo (una línea por mensaje, 115200 baudios):
    ESP32 -> PC:  >n HOLA | HORA | ESPERA ms | CMD hex | ESCRIBE pag hex | FIN | RETIRADA ms
    PC -> ESP32:  <n OK [datos] | NADA | ERR motivo | CFG clave=valor;clave=valor
    n = número de petición; la respuesta lo repite y la ESP32 descarta las que no
    coinciden. '#...' = mensajes de la ESP32, que el puente muestra en la consola.
"""

import argparse
import sys
import time
import unicodedata
from datetime import datetime

from smartcard.Exceptions import CardConnectionException, NoCardException
from smartcard.System import readers
from smartcard.scard import SCARD_LEAVE_CARD

from pantalla import buscar_puerto
from tarjeta import claves
from tarjeta.ntag215 import ErrorTarjeta, NTAG215

VELOCIDAD = 115200

def hora():
    return datetime.now().strftime("%H:%M:%S")


class Puente:
    def __init__(self, ser, config=None, detalle=False):
        self.ser = ser
        self.config = config      # texto "<CFG ..." pendiente de enviar, o None
        self.detalle = detalle
        self.sesion = None        # NTAG215 de la tarjeta actual
        self.avisado_sin_lector = False
        self.numero = "0"         # n.º de la petición que se está atendiendo

    def responder(self, texto):
        # El protocolo es ASCII: se quitan tildes y saltos de línea de los mensajes
        texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
        texto = texto.replace("\n", " ").replace("\r", " ")
        self.ser.write(f"<{self.numero} {texto}\n".encode("ascii"))
        if self.detalle:
            print(f"  <{texto}")

    def cerrar_sesion(self):
        if self.sesion is not None:
            self.sesion.cerrar()
            self.sesion = None

    def atender(self, peticion):
        if self.detalle:
            print(f"  >{peticion}")
        numero, _, peticion = peticion.partition(" ")
        if numero == self.numero:
            return  # petición repetida (llega duplicada al abrir el puerto): ya contestada
        self.numero = numero
        orden, _, resto = peticion.partition(" ")
        try:
            getattr(self, "orden_" + orden.lower(), self.orden_desconocida)(resto)
        except (ErrorTarjeta, CardConnectionException) as e:
            # Tarjeta que rechaza el comando, retirada o que no responde: es normal
            # (p. ej. una tarjeta sin emitir); la ESP32 decide qué mostrar
            self.cerrar_sesion()
            self.responder(f"ERR {e}")
        except Exception as e:  # cualquier otro fallo: se informa, el puente sigue
            print(f"{hora()}  [!] Error atendiendo '{peticion}': {e!r}")
            self.cerrar_sesion()
            self.responder("ERR error interno del puente")

    def orden_desconocida(self, _):
        self.responder("ERR orden desconocida")

    def orden_hola(self, _):
        if self.config:
            self.responder("CFG " + self.config)
            self.config = None
            print(f"{hora()}  Configuración enviada a la ESP32")
        else:
            self.responder("OK PUENTE 1")

    def orden_hora(self, _):
        self.responder(f"OK {int(time.time())}")

    def orden_espera(self, ms):
        self.cerrar_sesion()
        try:
            t = NTAG215.esperar(mensaje=None, timeout=int(ms) / 1000)
        except ErrorTarjeta as e:  # no hay lector conectado
            if not self.avisado_sin_lector:
                print(f"{hora()}  [!] {e}")
                self.avisado_sin_lector = True
            time.sleep(int(ms) / 1000)
            self.responder("NADA")
            return
        self.avisado_sin_lector = False
        if t is None:
            self.responder("NADA")
            return
        self.sesion = t
        self.responder("OK " + t.uid.hex().upper())

    def orden_cmd(self, hexa):
        if self.sesion is None:
            self.responder("ERR sin tarjeta")
            return
        datos = self.sesion.comando(bytes.fromhex(hexa))
        self.responder(("OK " + datos.hex().upper()) if datos else "OK")

    def orden_escribe(self, resto):
        if self.sesion is None:
            self.responder("ERR sin tarjeta")
            return
        pagina, hexa = resto.split()
        datos = bytes.fromhex(hexa)
        for i in range(0, len(datos), 4):
            self.sesion.escribir(int(pagina) + i // 4, datos[i:i + 4])
        self.responder("OK")

    def orden_fin(self, _):
        self.cerrar_sesion()
        self.responder("OK")

    def orden_retirada(self, ms):
        limite = time.monotonic() + int(ms) / 1000
        lista = readers()
        conn = lista[0].createConnection() if lista else None
        while True:
            try:
                if conn is None:
                    raise NoCardException("sin lector")
                conn.connect(disposition=SCARD_LEAVE_CARD)
                conn.disconnect()
            except (NoCardException, CardConnectionException):
                self.responder("OK")
                return
            if time.monotonic() >= limite:
                self.responder("NADA")
                return
            time.sleep(0.1)


def main():
    p = argparse.ArgumentParser(description="Puente ACR122U <-> validador ESP32")
    p.add_argument("--puerto", help="puerto de la ESP32 (por defecto se detecta solo; "
                                    "Windows: COM3, macOS: /dev/cu.usbserial-...)")
    p.add_argument("--configurar", action="store_true",
                   help="enviar a la ESP32 el n.º de validador y la clave maestra")
    p.add_argument("--validador", type=int, help="n.º de validador para --configurar (1-899)")
    p.add_argument("--detalle", action="store_true", help="mostrar cada comando")
    args = p.parse_args()

    try:
        import serial
    except ImportError:
        sys.exit("Falta pyserial: pip install pyserial (Fedora: sudo dnf install python3-pyserial)")

    config = None
    if args.configurar:
        if not args.validador or not 1 <= args.validador <= 899:
            p.error("--configurar necesita --validador entre 1 y 899")
        config = f"validador={args.validador};clave_maestra={claves.clave_maestra().hex()}"

    if not args.puerto:
        try:
            args.puerto = buscar_puerto()
        except RuntimeError as e:
            sys.exit(str(e))
    try:
        ser = serial.Serial(args.puerto, VELOCIDAD, timeout=0.2)
    except serial.SerialException as e:
        sys.exit(f"No se pudo abrir {args.puerto}: {e}")
    # Descarta las peticiones que la ESP32 envió mientras el puente estaba cerrado:
    # contestarlas ahora solo desincronizaría las respuestas
    time.sleep(0.3)
    ser.reset_input_buffer()
    print(f"Puente activo en {args.puerto}. Esperando a la ESP32... (Ctrl+C para salir)")
    puente = Puente(ser, config, args.detalle)
    try:
        while True:
            linea = ser.readline().decode("utf-8", errors="replace").strip()
            if linea.startswith(">"):
                puente.atender(linea[1:])
            elif linea.startswith("#"):
                print(f"{hora()}  [ESP32] {linea[1:]}")
    except KeyboardInterrupt:
        print("\nPuente cerrado.")
    except serial.SerialException as e:
        print(f"\nSe perdió la conexión con la ESP32: {e}")
    finally:
        puente.cerrar_sesion()


if __name__ == "__main__":
    main()
