"""
Acceso de bajo nivel a tarjetas NTAG215 con un lector ACR122U (PC/SC).

Lecturas y autenticación: comandos nativos de la tarjeta enviados a través del
chip PN532 del lector (InCommunicateThru). Escrituras: APDU UPDATE BINARY del
ACR122U (FF D6), que interpreta por nosotros el ACK/NAK de la tarjeta.
"""

import time

from smartcard.Exceptions import CardConnectionException, NoCardException
from smartcard.System import readers
from smartcard.scard import SCARD_LEAVE_CARD, SCARD_UNPOWER_CARD

VERSION_NTAG215 = bytes.fromhex("0004040201001103")
TOTAL_PAGINAS = 135
PAG_CFG0, PAG_CFG1, PAG_PWD, PAG_PACK = 131, 132, 133, 134

# Bits del byte ACCESS (primer byte de CFG1)
PROT = 0x80              # la contraseña protege también la lectura
CFGLCK = 0x40            # bloqueo PERMANENTE de la configuración: nunca lo activamos
NFC_CNT_EN = 0x10        # contador NFC de 24 bits activo
NFC_CNT_PWD_PROT = 0x08  # leer el contador exige contraseña


class ErrorTarjeta(Exception):
    pass


class NTAG215:
    def __init__(self, conn):
        self.conn = conn
        self.uid = self._leer_uid()

    # --- Conexión -------------------------------------------------------------

    @classmethod
    def esperar(cls, mensaje="Acerca una tarjeta al lector...", timeout=None):
        """Espera a que haya una tarjeta en el lector y se conecta a ella.
        Con timeout (segundos) devuelve None si no aparece ninguna."""
        lista = readers()
        if not lista:
            raise ErrorTarjeta("no se encontró ningún lector (¿conectado? ¿pcscd activo?)")
        avisado = False
        limite = None if timeout is None else time.monotonic() + timeout
        while True:
            conn = lista[0].createConnection()
            try:
                conn.connect(disposition=SCARD_LEAVE_CARD)
                return cls(conn)
            except (NoCardException, CardConnectionException):
                if mensaje and not avisado:
                    print(mensaje)
                    avisado = True
                if limite is not None and time.monotonic() >= limite:
                    return None
                time.sleep(0.1)

    @staticmethod
    def esperar_retirada():
        lector = readers()[0]
        while True:
            conn = lector.createConnection()
            try:
                conn.connect(disposition=SCARD_LEAVE_CARD)
                conn.disconnect()
            except (NoCardException, CardConnectionException):
                return
            time.sleep(0.2)

    def cerrar(self):
        """Desconecta dejando la tarjeta sin autenticar: si no, el siguiente
        programa que la lea en el mismo campo RF heredaría el acceso."""
        try:
            self.reactivar()
        except Exception:
            pass
        try:
            self.conn.disconnect()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cerrar()

    # --- Transporte -----------------------------------------------------------

    def _apdu(self, datos):
        try:
            resp, sw1, sw2 = self.conn.transmit(list(datos))
        except CardConnectionException as e:
            # Tras re-activar la tarjeta, pcscd puede marcar la conexión como
            # "reiniciada". Se reconecta sin tocar la tarjeta y se reintenta.
            if "reset" not in str(e).lower():
                raise ErrorTarjeta(f"se perdió la comunicación con la tarjeta: {e}") from e
            self.conn.disconnect()
            self.conn.connect(disposition=SCARD_LEAVE_CARD)
            resp, sw1, sw2 = self.conn.transmit(list(datos))
        if sw1 == 0x61:  # firmware antiguo del ACR122U
            resp, sw1, sw2 = self.conn.transmit([0xFF, 0xC0, 0x00, 0x00, sw2])
        return bytes(resp), (sw1 << 8) | sw2

    def _pn532(self, cmd):
        resp, sw = self._apdu([0xFF, 0x00, 0x00, 0x00, len(cmd)] + list(cmd))
        if sw != 0x9000 or len(resp) < 2:
            raise ErrorTarjeta(f"error del lector (sw={sw:04X})")
        return resp

    def _nativo(self, cmd):
        """Comando nativo de la tarjeta. Si la tarjeta lo rechaza (NAK) queda muda:
        se re-activa y se lanza ErrorTarjeta."""
        resp = self._pn532([0xD4, 0x42] + list(cmd))
        if resp[:2] != b"\xD5\x43" or len(resp) < 3 or resp[2] & 0x3F != 0 or len(resp) == 3:
            self.reactivar()
            raise ErrorTarjeta(f"la tarjeta rechazó el comando {cmd[0]:02X}")
        return resp[3:]

    def reactivar(self):
        """Vuelve a seleccionar la tarjeta. Pierde la autenticación con contraseña."""
        try:
            self._pn532([0xD4, 0x52, 0x00])                    # InRelease
            resp = self._pn532([0xD4, 0x4A, 0x01, 0x00])       # InListPassiveTarget
            if len(resp) >= 3 and resp[2] == 1:
                return
        except ErrorTarjeta:
            pass
        try:
            self.conn.reconnect(disposition=SCARD_UNPOWER_CARD)
        except CardConnectionException as e:
            raise ErrorTarjeta("la tarjeta se retiró del lector") from e

    # --- Comandos NTAG --------------------------------------------------------

    def _leer_uid(self):
        resp, sw = self._apdu([0xFF, 0xCA, 0x00, 0x00, 0x00])
        if sw != 0x9000:
            raise ErrorTarjeta("no se pudo leer el UID")
        return resp

    def version(self) -> bytes:
        return self._nativo([0x60])

    def es_ntag215(self) -> bool:
        try:
            return self.version() == VERSION_NTAG215
        except ErrorTarjeta:
            return False

    def leer(self, desde: int, hasta: int) -> bytes:
        """FAST_READ: páginas desde..hasta (incluidas)."""
        datos = self._nativo([0x3A, desde, hasta])
        if len(datos) != (hasta - desde + 1) * 4:
            self.reactivar()
            raise ErrorTarjeta(f"lectura incompleta de las páginas {desde}-{hasta}")
        return datos

    def puede_leer(self, pagina: int) -> bool:
        """True si la página se lee sin contraseña (no lanza excepción)."""
        try:
            self._nativo([0x30, pagina])
            return True
        except ErrorTarjeta:
            return False

    def escribir(self, pagina: int, datos: bytes):
        if len(datos) != 4:
            raise ValueError("una página son 4 bytes")
        _, sw = self._apdu([0xFF, 0xD6, 0x00, pagina, 0x04] + list(datos))
        if sw != 0x9000:
            self.reactivar()
            raise ErrorTarjeta(f"no se pudo escribir la página {pagina} (sw={sw:04X})")

    def escribir_paginas(self, desde: int, datos: bytes):
        for i in range(0, len(datos), 4):
            self.escribir(desde + i // 4, datos[i:i + 4])

    def autenticar(self, pwd: bytes) -> bytes:
        """PWD_AUTH: devuelve el PACK de 2 bytes. Lanza ErrorTarjeta si se rechaza."""
        pack = self._nativo([0x1B] + list(pwd))
        if len(pack) != 2:
            self.reactivar()
            raise ErrorTarjeta("respuesta de autenticación inválida")
        return pack

    def contador_nfc(self) -> int:
        """READ_CNT: contador NFC de 24 bits (requiere estar autenticado si
        NFC_CNT_PWD_PROT está activo)."""
        c = self._nativo([0x39, 0x02])
        return c[0] | (c[1] << 8) | (c[2] << 16)
