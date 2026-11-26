"""
Acceso de bajo nivel a tarjetas NTAG215 con un lector ACR122U (PC/SC).

Lecturas y autenticación: comandos nativos de la tarjeta enviados a través del
chip PN532 del lector (InCommunicateThru). Escrituras: APDU UPDATE BINARY del
ACR122U (FF D6), que interpreta por nosotros el ACK/NAK de la tarjeta.

NTAG215 es el acceso por páginas; Tarjeta215 lo adapta a la interfaz común
TarjetaFisica (contrato C3) que usan los programas. pyscard se importa solo al
usarse, así que el controlador funciona con la conexión simulada de virtual215.
"""

from tarjeta import formato
from tarjeta.base import (TAM_CABECERA, TAM_REGISTRO, AutenticacionRechazada, ErrorTarjeta,
                          TarjetaAjena)

__all__ = ["NTAG215", "Tarjeta215", "ErrorTarjeta", "VERSION_NTAG215"]

VERSION_NTAG215 = bytes.fromhex("0004040201001103")
TOTAL_PAGINAS = 135
PAG_CFG0, PAG_CFG1, PAG_PWD, PAG_PACK = 131, 132, 133, 134

# Bits del byte ACCESS (primer byte de CFG1)
PROT = 0x80              # la contraseña protege también la lectura
CFGLCK = 0x40            # bloqueo PERMANENTE de la configuración: nunca lo activamos
NFC_CNT_EN = 0x10        # contador NFC de 24 bits activo
NFC_CNT_PWD_PROT = 0x08  # leer el contador exige contraseña


class ComandoRechazado(ErrorTarjeta):
    """La tarjeta respondió NAK (o no respondió) a un comando nativo: por ejemplo,
    un comando que no existe en ese tipo de chip. Distinto de perder la conexión."""


def _excepciones_pcsc():
    """(conexión perdida, sin tarjeta) de pyscard. Sin pyscard devuelve tuplas
    vacías, de modo que isinstance() da False y la excepción original se propaga."""
    try:
        from smartcard.Exceptions import CardConnectionException, NoCardException
    except ImportError:
        return (), ()
    return CardConnectionException, NoCardException


class NTAG215:
    def __init__(self, conn):
        self.conn = conn
        self.uid = self._leer_uid()

    # --- Conexión -------------------------------------------------------------

    @classmethod
    def esperar(cls, mensaje="Acerca una tarjeta al lector...", timeout=None):
        """Espera a que haya una tarjeta en el lector y se conecta a ella.
        Con timeout (segundos) devuelve None si no aparece ninguna.
        (Lo usa el puente de la ESP32; los programas usan base.esperar_tarjeta.)"""
        from tarjeta.lector import esperar_conexion
        conn = esperar_conexion(mensaje, timeout)
        return None if conn is None else cls(conn)

    @staticmethod
    def esperar_retirada():
        from tarjeta.lector import esperar_retirada
        esperar_retirada()

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
        except Exception as error:
            conexion_perdida, sin_tarjeta = _excepciones_pcsc()
            if not isinstance(error, conexion_perdida):
                raise
            # Tras re-activar la tarjeta, el servicio PC/SC puede marcar la conexión
            # como "reiniciada". Se reconecta sin tocar la tarjeta y se reintenta una
            # vez. (No se mira el texto del error: en Windows llega traducido.)
            from smartcard.scard import SCARD_LEAVE_CARD
            try:
                self.conn.disconnect()
                self.conn.connect(disposition=SCARD_LEAVE_CARD)
                resp, sw1, sw2 = self.conn.transmit(list(datos))
            except (conexion_perdida, sin_tarjeta) as e:
                raise ErrorTarjeta(f"se perdió la comunicación con la tarjeta: {e}") from e
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
            raise ComandoRechazado(f"la tarjeta rechazó el comando {cmd[0]:02X}")
        return resp[3:]

    def comando(self, cmd) -> bytes:
        """Comando nativo de la tarjeta (para el puente de la ESP32). Lanza
        ErrorTarjeta si la tarjeta lo rechaza; queda reactivada para el siguiente."""
        return self._nativo(list(cmd))

    def reactivar(self):
        """Vuelve a seleccionar la tarjeta. Pierde la autenticación con contraseña."""
        try:
            self._pn532([0xD4, 0x52, 0x00])                    # InRelease
            resp = self._pn532([0xD4, 0x4A, 0x01, 0x00])       # InListPassiveTarget
            if len(resp) >= 3 and resp[2] == 1:
                return
        except ErrorTarjeta:
            pass
        from smartcard.scard import SCARD_UNPOWER_CARD
        conexion_perdida, _ = _excepciones_pcsc()
        try:
            self.conn.reconnect(disposition=SCARD_UNPOWER_CARD)
        except conexion_perdida as e:
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
        """False si la tarjeta no es una NTAG215 (versión distinta o rechaza
        GET_VERSION). Si se pierde la comunicación lanza ErrorTarjeta."""
        try:
            return self.version() == VERSION_NTAG215
        except ComandoRechazado:
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


class Tarjeta215:
    """NTAG215 vista como TarjetaFisica (contrato C3) y Personalizable.

    El área lógica de 72 bytes está en las páginas 8-25: cabecera en 8-13,
    registro A en 16-20 y registro B en 21-25 (formato.PAG_*). La contraseña y
    el PACK se derivan del UID con `contrasena(uid) -> (PWD, PACK)`.
    """

    tipo = "NTAG215"

    def __init__(self, chip: NTAG215, contrasena):
        self.chip = chip
        self._contrasena = contrasena

    @property
    def uid(self) -> bytes:
        return self.chip.uid

    def cerrar(self):
        self.chip.cerrar()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cerrar()

    # --- TarjetaFisica (C3) -------------------------------------------------------

    def autenticar(self) -> None:
        pwd, pack = self._contrasena(self.uid)
        try:
            respuesta = self.chip.autenticar(pwd)
        except ErrorTarjeta as e:
            raise AutenticacionRechazada(str(e)) from e
        if respuesta != pack:
            raise TarjetaAjena("PACK incorrecto")

    def leer_area(self) -> bytes:
        return self.chip.leer(formato.PAG_INICIO, formato.PAG_FIN)

    def escribir_cabecera(self, datos: bytes) -> None:
        if len(datos) != TAM_CABECERA:
            raise ValueError(f"la cabecera son {TAM_CABECERA} bytes")
        self.chip.escribir_paginas(formato.PAG_CABECERA, datos)

    def escribir_registro(self, indice: int, datos: bytes) -> None:
        if indice not in (0, 1):
            raise ValueError("el registro es 0 (A) o 1 (B)")
        if len(datos) != TAM_REGISTRO:
            raise ValueError(f"un registro son {TAM_REGISTRO} bytes")
        pagina = formato.PAG_REGISTROS[indice]
        self.chip.escribir_paginas(pagina, datos)
        if self.chip.leer(pagina, pagina + len(datos) // 4 - 1) != datos:
            raise ErrorTarjeta("verificación de escritura")

    def contador(self) -> int:
        return self.chip.contador_nfc()

    # --- Personalizable (estación de emisión) ---------------------------------------

    def protegida(self) -> bool:
        return not self.chip.puede_leer(formato.PAG_INICIO)

    def _configuracion(self) -> bytes:
        """8 bytes de CFG0 + CFG1."""
        return self.chip.leer(PAG_CFG0, PAG_CFG1)

    def configuracion_bloqueada(self) -> bool:
        return bool(self._configuracion()[4] & CFGLCK)

    def _configuracion_modificable(self) -> bytes:
        cfg = self._configuracion()
        if cfg[4] & CFGLCK:
            raise ErrorTarjeta("la configuración está bloqueada de forma permanente")
        return cfg

    def personalizar(self, area: bytes) -> None:
        """Graba el área y activa contraseña, PACK y contador NFC protegido."""
        if len(area) != formato.TAM_AREA:
            raise ValueError(f"el área son {formato.TAM_AREA} bytes")
        cfg = self._configuracion_modificable()
        pwd, pack = self._contrasena(self.uid)
        self.chip.escribir_paginas(formato.PAG_INICIO, area)
        # Orden importante: la contraseña primero y AUTH0 (que activa la protección) al final
        self.chip.escribir(PAG_PWD, pwd)
        self.chip.escribir(PAG_PACK, pack + b"\x00\x00")
        access = (cfg[4] & ~(PROT | CFGLCK | NFC_CNT_EN | NFC_CNT_PWD_PROT | 0x07)) \
            | PROT | NFC_CNT_EN | NFC_CNT_PWD_PROT   # AUTHLIM = 0: intentos ilimitados (pruebas)
        self.chip.escribir(PAG_CFG1, bytes([access]) + cfg[5:8])
        self.chip.escribir(PAG_CFG0, cfg[0:3] + bytes([formato.PAG_INICIO]))  # AUTH0

    def restablecer(self) -> None:
        """Borra el área y deja contraseña, PACK y protección como de fábrica.
        El contador NFC no se puede poner a cero: es así por diseño del chip."""
        cfg = self._configuracion_modificable()
        self.chip.escribir_paginas(formato.PAG_INICIO, bytes(formato.TAM_AREA))
        self.chip.escribir(PAG_CFG0, cfg[0:3] + b"\xFF")  # AUTH0 = FF: sin protección
        access = cfg[4] & ~(PROT | NFC_CNT_EN | NFC_CNT_PWD_PROT | 0x07)
        self.chip.escribir(PAG_CFG1, bytes([access]) + cfg[5:8])
        self.chip.escribir(PAG_PWD, b"\xFF\xFF\xFF\xFF")  # valores de fábrica
        self.chip.escribir(PAG_PACK, b"\x00\x00\x00\x00")

    def reiniciar_sesion(self) -> None:
        self.chip.reactivar()
