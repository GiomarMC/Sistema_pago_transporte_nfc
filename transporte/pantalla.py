"""
Envío de resultados a la pantalla del validador (ESP32 + LCD, ver pantalla_esp32/).

La pantalla es opcional: si no está conectada o falla, el validador sigue
cobrando con normalidad y solo se muestra un aviso en la consola.

Protocolo: una línea ASCII por mensaje, campos separados por '|'.
    PASA|linea1|linea2|linea3     RECHAZO|...     AVISO|...     ESPERA|linea1|linea2
"""

import sys
import time
import unicodedata

VELOCIDAD = 115200

# Chips USB-serie habituales en las placas ESP32 (fabricante USB)
CHIPS_ESP32 = {0x10C4: "CP210x", 0x1A86: "CH340", 0x0403: "FTDI", 0x303A: "Espressif"}


def buscar_puerto():
    """Puerto de la ESP32 en Linux, Windows o macOS, por el chip USB de la placa."""
    from serial.tools import list_ports
    puertos = list(list_ports.comports())
    candidatos = [p for p in puertos if p.vid in CHIPS_ESP32]
    if len(candidatos) == 1:
        return candidatos[0].device
    lista = ", ".join(f"{p.device} ({p.description})" for p in puertos) or "ninguno"
    if not candidatos:
        raise RuntimeError(f"No se encontró la ESP32. ¿Está conectada por USB? Puertos disponibles: {lista}")
    raise RuntimeError(f"Hay varias placas conectadas; indica cuál con --puerto. Puertos: {lista}")




def _ascii(texto):
    """La fuente de la pantalla solo tiene ASCII: quita tildes y separadores."""
    texto = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return texto.replace("|", "/").replace("\n", " ").strip()


class Pantalla:
    """Pantalla conectada por USB. Si se desconecta (cable flojo, reinicio), el
    validador sigue cobrando y `mantener()` la reconecta sola mientras espera tarjetas."""

    REVISAR_CADA = 5.0  # segundos entre comprobaciones de la conexión

    def __init__(self, puerto):
        try:
            import serial
        except ImportError:
            raise SystemExit("Falta la biblioteca pyserial: pip install pyserial "
                             "(Fedora: sudo dnf install python3-pyserial)")
        self._serial = serial
        self.puerto = buscar_puerto() if puerto == "auto" else puerto
        self.ser = None
        self.funciona = False
        self._espera = ("Validador", "")
        self._proxima_revision = 0.0
        self._conectar()  # la primera vez, si falla, se informa al arrancar

    def _conectar(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = self._serial.Serial(self.puerto, VELOCIDAD, timeout=0.5)
        # Al abrir el puerto la ESP32 se reinicia: esperar a que arranque
        time.sleep(2.0)
        self.ser.reset_input_buffer()
        if not self._responde():
            raise RuntimeError(f"la placa en {self.puerto} no respondió como pantalla")
        self.funciona = True
        self._enviar("ESPERA", *self._espera)

    def _responde(self):
        """Envía PING y espera PANTALLA_OK (puede llegar tras algún 'OK' pendiente)."""
        self.ser.write(b"PING\n")
        for _ in range(4):
            if "PANTALLA_OK" in self.ser.readline().decode(errors="replace"):
                return True
        return False

    def mantener(self):
        """Llamar mientras no hay tarjeta: comprueba la conexión y, si se cayó,
        intenta reconectar. No hace nada si se llamó hace menos de REVISAR_CADA s."""
        ahora = time.monotonic()
        if ahora < self._proxima_revision:
            return
        self._proxima_revision = ahora + self.REVISAR_CADA
        if self.funciona:
            try:
                if self._responde():
                    self._enviar("ESPERA", *self._espera)  # por si la placa se reinició
                    return
            except Exception:
                pass
            self.funciona = False
            print("  [!] Se perdió la conexión con la pantalla; reintentando...")
        try:
            self._conectar()
            print(f"  Pantalla reconectada en {self.puerto}")
        except Exception:
            pass  # se reintentará en la próxima revisión

    def _enviar(self, tipo, *lineas):
        if not self.funciona:
            return
        mensaje = "|".join([tipo] + [_ascii(l) for l in lineas]) + "\n"
        try:
            self.ser.write(mensaje.encode("ascii"))
            self.ser.reset_input_buffer()  # descarta los "OK" de la placa
        except Exception as e:  # cable desconectado, etc.: no detener el cobro
            self.funciona = False
            print(f"  [!] La pantalla dejó de responder ({e}); se sigue cobrando sin ella")

    def pasa(self, linea1, linea2="", linea3=""):
        self._enviar("PASA", linea1, linea2, linea3)

    def rechazo(self, linea1, linea2="", linea3=""):
        self._enviar("RECHAZO", linea1, linea2, linea3)

    def aviso(self, linea1, linea2="", linea3=""):
        self._enviar("AVISO", linea1, linea2, linea3)

    def espera(self, linea1, linea2=""):
        self._espera = (linea1, linea2)
        self._enviar("ESPERA", linea1, linea2)


class SinPantalla:
    """Sustituto cuando no se usa pantalla: no hace nada."""

    def pasa(self, *a):
        pass

    rechazo = aviso = espera = mantener = pasa
