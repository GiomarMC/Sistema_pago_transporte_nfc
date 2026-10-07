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
    python3 puente_nfc.py --configurar --validador 103 --wifi MiRed   # además, WiFi,
                                                           # servidor y token para sincronizar
    python3 puente_nfc.py --simular                        # pruebas con tarjetas virtuales

Protocolo (una línea por mensaje, 115200 baudios):
    ESP32 -> PC:  >n HOLA | HORA | ESPERA ms | CMD hex | ESCRIBE pag hex | FIN | RETIRADA ms
    PC -> ESP32:  <n OK [datos] | NADA | ERR motivo | CFG clave=valor;clave=valor
    n = número de petición; la respuesta lo repite y la ESP32 descarta las que no
    coinciden. '#...' = mensajes de la ESP32, que el puente muestra en la consola.
"""

import argparse
import re
import sys
import time
import unicodedata
from datetime import datetime

from smartcard.Exceptions import CardConnectionException, NoCardException
from smartcard.System import readers
from smartcard.scard import SCARD_LEAVE_CARD

from pantalla import abrir_esp32, buscar_puerto
from config import RUTA_CLAVE_MAESTRA
from tarjeta import claves
from tarjeta.ntag215 import ErrorTarjeta, NTAG215

VELOCIDAD = 115200
SILENCIO_MAXIMO = 12  # s sin noticias de la ESP32 antes de reconectar y reiniciarla
                      # (una sincronización por WiFi puede tenerla ocupada unos 6 s)

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
    p.add_argument("--wifi", help="con --configurar: red WiFi de 2,4 GHz para sincronizar con "
                                  "el servidor (el token sale de api.json)")
    p.add_argument("--wifi-clave", help="contraseña del WiFi (si no se indica, se pide)")
    p.add_argument("--servidor", help="URL del servidor vista desde la ESP32, p. ej. "
                                      "https://subepe.duckdns.org (por defecto, la de api.json). "
                                      "Sin --wifi, cambia solo servidor y token")
    p.add_argument("--detalle", action="store_true", help="mostrar cada comando")
    p.add_argument("--simular", action="store_true",
                   help="probar la ESP32 con tarjetas virtuales (sin ACR122U); ver simular_tarjetas.py")
    p.add_argument("--intervalo", type=float, default=5, help="s entre pruebas con --simular")
    args = p.parse_args()

    try:
        import serial
    except ImportError:
        sys.exit("Falta pyserial: pip install pyserial (Fedora: sudo dnf install python3-pyserial)")

    config = None
    if args.configurar:
        if not args.validador or not 1 <= args.validador <= 899:
            p.error("--configurar necesita --validador entre 1 y 899")
        pares = {"validador": args.validador, "clave_maestra": claves.clave_maestra().hex()}
        if args.wifi:
            pares.update(config_wifi(args))
        elif args.servidor:  # solo cambia el servidor; el WiFi guardado se conserva
            import api_cliente
            # El token es distinto en cada servidor: se envía el de api.json
            pares.update(servidor=args.servidor, token=api_cliente.token_validador(args.validador))
            print(f"La ESP32 sincronizará con {args.servidor} (token de api.json, "
                  f"servidor {api_cliente._conf()['url']})")
        for clave, valor in pares.items():
            valor = str(valor)
            if not valor.isascii() or any(c in valor for c in ";\r\n"):
                p.error(f"'{clave}' no puede tener tildes, ñ, ';' ni saltos de línea")
        config = ";".join(f"{k}={v}" for k, v in pares.items())

    if not args.puerto:
        try:
            args.puerto = buscar_puerto()
        except RuntimeError as e:
            sys.exit(str(e))
    try:
        # Reinicio limpio: evita que la ESP32 arranque en modo de carga de programa
        # y descarta lo que envió mientras el puente estaba cerrado
        ser = abrir_esp32(serial, args.puerto, timeout=0.2)
    except serial.SerialException as e:
        sys.exit(f"No se pudo abrir {args.puerto}: {e}")
    print(f"Puente activo en {args.puerto}. Esperando a la ESP32... (Ctrl+C para salir)")
    if args.simular:
        if not RUTA_CLAVE_MAESTRA.exists():
            sys.exit(f"--simular necesita la clave maestra de la ESP32 en {RUTA_CLAVE_MAESTRA}")
        from simular_tarjetas import PuenteSimulado
        puente = PuenteSimulado(ser, config, args.detalle, args.intervalo)
    else:
        puente = Puente(ser, config, args.detalle)
    ultimo_mensaje = time.monotonic()
    ruido = []  # líneas que no son del protocolo (p. ej. mensajes de arranque del chip)
    try:
        while True:
            try:
                crudo = ser.readline()
            except serial.SerialException:
                crudo = None  # puerto caído (cable, reinicio del USB): se recupera abajo
            linea = None if crudo is None else crudo.decode("utf-8", errors="replace").strip()
            # Un mensaje válido puede llegar con bytes basura delante (ruido en la
            # línea serie): se rescata el mensaje en vez de descartar la línea entera
            mensaje = re.search(r"(>\d+ .*|#.*)$", linea) if linea else None
            if mensaje and mensaje.start():
                linea = mensaje.group(1)
                basura = crudo[:crudo.rfind(linea.encode("utf-8", errors="replace"))]
                print(f"{hora()}  [!] {len(basura)} bytes de ruido antes de un mensaje de la "
                      f"ESP32: {basura[:32].hex(' ')}{' ...' if len(basura) > 32 else ''}")
            if mensaje:
                ultimo_mensaje = time.monotonic()
                ruido.clear()
                if not linea.startswith(">"):
                    print(f"{hora()}  [ESP32] {linea[1:]}")
                    continue
                try:
                    puente.atender(linea[1:])
                    continue
                except serial.SerialException:
                    linea = None  # el puerto cayó al responder: se recupera abajo
            if linea and len(ruido) < 200:
                ruido.append(crudo)
            # El validador pregunta varias veces por segundo. Si no lo hace (puerto
            # perdido, placa bloqueada o reiniciándose en bucle), se recupera sola.
            if linea is None or time.monotonic() - ultimo_mensaje > SILENCIO_MAXIMO:
                if ruido:
                    # En hexadecimal: al decodificar como texto se pierden los bytes no válidos
                    print(f"{hora()}  [!] La ESP32 envió {len(ruido)} líneas que no son del "
                          f"validador. Primeras (hex):")
                    for l in ruido[:3]:
                        print(f"          {l[:48].hex(' ')}{' ...' if len(l) > 48 else ''}")
                    if any(b"rst:" in l or b"brownout" in l.lower() for l in ruido):
                        print(f"{hora()}      Parecen reinicios del chip: posible caída de tensión "
                              "(cable USB fino o largo, o consumo al arrancar)")
                    ruido.clear()
                puente.cerrar_sesion()
                ser = recuperar_esp32(serial, ser, args.puerto)
                puente.ser = ser
                ultimo_mensaje = time.monotonic()
    except KeyboardInterrupt:
        print("\nPuente cerrado.")
    finally:
        puente.cerrar_sesion()


def config_wifi(args):
    """Claves de config.txt para la sincronización por WiFi."""
    import getpass
    import socket
    from urllib.parse import urlsplit

    import api_cliente

    servidor = args.servidor
    if not servidor:
        url = urlsplit(api_cliente._conf()["url"])
        if url.hostname in ("127.0.0.1", "localhost"):
            # 127.0.0.1 en la ESP32 sería ella misma. La IP de este ordenador puede
            # cambiar (la reparte el router), así que se usa su nombre en la red
            # local, "nombre.local" (mDNS: avahi en Linux, Bonjour en macOS/Windows)
            nombre = socket.gethostname().split(".")[0] + ".local"
            url = url._replace(netloc=f"{nombre}:{url.port}" if url.port else nombre)
        servidor = url.geturl()
    clave = args.wifi_clave if args.wifi_clave is not None else \
        getpass.getpass(f"Contraseña del WiFi '{args.wifi}': ")
    print(f"La ESP32 sincronizará con {servidor} a través de '{args.wifi}'")
    return {"wifi_ssid": args.wifi, "wifi_clave": clave, "servidor": servidor,
            "token": api_cliente.token_validador(args.validador)}


def recuperar_esp32(serial_mod, ser, puerto):
    """Vuelve a abrir el puerto (si desapareció o cambió) y reinicia la ESP32."""
    print(f"{hora()}  [!] La ESP32 no responde; reconectando y reiniciándola...")
    try:
        ser.close()
    except Exception:
        pass
    while True:
        try:
            nuevo = abrir_esp32(serial_mod, puerto, timeout=0.2)
            print(f"{hora()}  Puerto {puerto} abierto de nuevo")
            return nuevo
        except (serial_mod.SerialException, OSError):
            time.sleep(1)  # placa desconectada: se espera a que vuelva

if __name__ == "__main__":
    main()
