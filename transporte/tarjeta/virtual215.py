"""
Lector ACR122U con una NTAG215 encima, en memoria, para probar sin hardware.

Emula la conexión PC/SC (``transmit``, ``connect``, ``reconnect``...) que usa
``tarjeta.ntag215.NTAG215``, de modo que el controlador real se ejecuta entero:
APDU del lector (FF CA, FF D6), comandos del PN532 (InCommunicateThru,
InRelease, InListPassiveTarget) y comandos nativos de la NTAG215 (GET_VERSION,
READ, FAST_READ, PWD_AUTH, READ_CNT).

Reproduce las reglas de la hoja de datos de la NTAG215 que usa el sistema:
AUTH0, PROT, NFC_CNT_EN, NFC_CNT_PWD_PROT, PWD/PACK ilegibles, contador NFC que
sube con la primera lectura tras activar la tarjeta y tarjeta "muda" tras un NAK
hasta que se vuelve a activar. No emula AUTHLIM, el bloqueo CFGLCK en sí ni los
bits de bloqueo de páginas.

Solo para pruebas: no contiene claves ni datos reales.
"""

VERSION_NTAG215 = bytes.fromhex("0004040201001103")
ATR_NTAG215 = bytes.fromhex("3B8F8001804F0CA0000003060300030000000068")
TOTAL_PAGINAS = 135
PAG_CFG0, PAG_CFG1, PAG_PWD, PAG_PACK = 131, 132, 133, 134
PROT, NFC_CNT_EN, NFC_CNT_PWD_PROT = 0x80, 0x10, 0x08

_SW_OK = (0x90, 0x00)
_SW_ERROR = (0x63, 0x00)


class ConexionVirtual215:
    """Conexión PC/SC simulada. Los atributos son el estado del chip."""

    def __init__(self, uid: bytes = bytes.fromhex("04A1B2C3D4E5F6")):
        if len(uid) != 7:
            raise ValueError("el UID de una NTAG215 tiene 7 bytes")
        self.uid = uid
        self.memoria = bytearray(TOTAL_PAGINAS * 4)
        self.memoria[0:3] = uid[0:3]
        self.memoria[4:8] = uid[3:7]
        self.memoria[12:16] = bytes.fromhex("E1103E00")      # CC de fábrica
        self.memoria[PAG_CFG0 * 4:PAG_CFG0 * 4 + 4] = bytes.fromhex("040000FF")  # AUTH0 = FF
        self.memoria[PAG_CFG1 * 4:PAG_CFG1 * 4 + 4] = bytes(4)
        self.version = VERSION_NTAG215     # respuesta a GET_VERSION
        self.pwd = b"\xff\xff\xff\xff"
        self.pack = b"\x00\x00"
        self.contador_nfc = 0
        self.autenticada = False
        self.muda = False                  # tras un NAK no responde hasta reactivarla
        self.contada = False               # el contador ya subió en esta activación
        self.presente = True
        self.escrituras_hasta_retirar = None  # n.º de escrituras de página antes de retirarla
        self.apdus_hasta_retirar = None    # n.º de APDU que se atienden antes de retirarla
        self.registro = []                 # APDU recibidas, para las pruebas

    # --- Estado de configuración ---------------------------------------------

    @property
    def auth0(self) -> int:
        return self.memoria[PAG_CFG0 * 4 + 3]

    @property
    def access(self) -> int:
        return self.memoria[PAG_CFG1 * 4]

    def paginas(self, desde: int, hasta: int) -> bytes:
        """Contenido real de las páginas desde..hasta (incluidas), sin reglas de acceso."""
        return bytes(self.memoria[desde * 4:(hasta + 1) * 4])

    def retirar(self):
        self.presente = False

    # --- Interfaz PC/SC --------------------------------------------------------

    def getATR(self):
        return list(ATR_NTAG215)

    def acercar(self):
        """La tarjeta entra en el campo del lector: se enciende (toque nuevo)."""
        self.presente = True
        self._encender()

    def connect(self, disposition=None):
        if not self.presente:
            from smartcard.Exceptions import NoCardException
            raise NoCardException("tarjeta virtual retirada", 0)
        self._activar()

    def reconnect(self, disposition=None):
        if not self.presente:
            from smartcard.Exceptions import CardConnectionException
            raise CardConnectionException("tarjeta virtual retirada")
        from smartcard.scard import SCARD_UNPOWER_CARD
        if disposition == SCARD_UNPOWER_CARD:
            self._encender()  # se corta el campo RF: la tarjeta se reinicia
        else:
            self._activar()

    def disconnect(self):
        pass

    def transmit(self, apdu):
        apdu = bytes(apdu)
        self.registro.append(apdu)
        if self.apdus_hasta_retirar is not None:
            if self.apdus_hasta_retirar == 0:
                self.retirar()
            self.apdus_hasta_retirar -= 1
        if not self.presente:
            from smartcard.Exceptions import CardConnectionException
            raise CardConnectionException("tarjeta virtual retirada")
        if apdu[:5] == b"\xFF\xCA\x00\x00\x00":
            return list(self.uid), *_SW_OK
        if apdu[:3] == b"\xFF\xD6\x00" and len(apdu) == 9 and apdu[4] == 4:
            return [], *self._update_binary(apdu[3], apdu[5:9])
        if apdu[:4] == b"\xFF\x00\x00\x00" and len(apdu) == 5 + apdu[4]:
            return list(self._pn532(apdu[5:])), *_SW_OK
        return [], 0x6A, 0x81

    # --- PN532 -----------------------------------------------------------------

    def _pn532(self, cmd: bytes) -> bytes:
        if cmd[:2] == b"\xD4\x42":
            respuesta = self._nativo(cmd[2:])
            return b"\xD5\x43\x01" if respuesta is None else b"\xD5\x43\x00" + respuesta
        if cmd == b"\xD4\x52\x00":
            return b"\xD5\x53\x00"
        if cmd == b"\xD4\x4A\x01\x00":
            self._activar()
            return b"\xD5\x4B\x01\x01\x00\x44\x00\x07" + self.uid
        return b"\xD5\x7F"

    def _activar(self):
        """Re-selección (InListPassiveTarget) sin apagar el campo RF."""
        self.autenticada = False
        self.muda = False

    def _encender(self):
        """Encendido del chip: el contador NFC vuelve a poder subir (hoja de datos
        NTAG213/215/216: primera READ o FAST_READ tras el encendido). Pendiente
        de contrastar con una tarjeta real (M1-01b)."""
        self._activar()
        self.contada = False

    # --- NTAG215 ---------------------------------------------------------------

    def _nak(self):
        self.muda = True
        self.autenticada = False
        return None

    def _lectura_protegida(self, pagina: int) -> bool:
        return bool(self.access & PROT) and pagina >= self.auth0 and not self.autenticada

    def _leer_paginas(self, desde: int, hasta: int) -> bytes:
        datos = bytearray(self.memoria[desde * 4:(hasta + 1) * 4])
        for pagina in (PAG_PWD, PAG_PACK):  # PWD y PACK se leen siempre como 00h
            if desde <= pagina <= hasta:
                inicio = (pagina - desde) * 4
                datos[inicio:inicio + 4] = bytes(4)
        return bytes(datos)

    def _contar_lectura(self):
        if self.access & NFC_CNT_EN and not self.contada:
            self.contador_nfc = (self.contador_nfc + 1) & 0xFFFFFF
            self.contada = True

    def _nativo(self, cmd: bytes):
        if self.muda or not cmd:
            return self._nak()
        op = cmd[0]
        if op == 0x60 and len(cmd) == 1:
            return self.version
        if op == 0x30 and len(cmd) == 2:
            inicio = cmd[1]
            if inicio >= TOTAL_PAGINAS:
                return self._nak()
            paginas = [(inicio + i) % TOTAL_PAGINAS for i in range(4)]
            if any(self._lectura_protegida(p) for p in paginas):
                return self._nak()
            self._contar_lectura()
            return b"".join(self._leer_paginas(p, p) for p in paginas)
        if op == 0x3A and len(cmd) == 3:
            desde, hasta = cmd[1], cmd[2]
            if desde > hasta or hasta >= TOTAL_PAGINAS \
                    or self._lectura_protegida(desde) or self._lectura_protegida(hasta):
                return self._nak()
            self._contar_lectura()
            return self._leer_paginas(desde, hasta)
        if op == 0x1B and len(cmd) == 5:
            if cmd[1:5] != self.pwd:
                return self._nak()
            self.autenticada = True
            return self.pack
        if op == 0x39 and len(cmd) == 2 and cmd[1] == 0x02:
            if not self.access & NFC_CNT_EN:
                return self._nak()
            if self.access & NFC_CNT_PWD_PROT and not self.autenticada:
                return self._nak()
            return self.contador_nfc.to_bytes(3, "little")
        return self._nak()

    def _update_binary(self, pagina: int, datos: bytes):
        if self.muda or pagina < 2 or pagina >= TOTAL_PAGINAS:
            return _SW_ERROR
        if pagina >= self.auth0 and not self.autenticada:
            self.muda = True
            return _SW_ERROR
        if self.escrituras_hasta_retirar is not None:
            if self.escrituras_hasta_retirar == 0:
                self.retirar()
                return _SW_ERROR
            self.escrituras_hasta_retirar -= 1
        if pagina == PAG_PWD:
            self.pwd = bytes(datos)
        elif pagina == PAG_PACK:
            self.pack = bytes(datos[:2])
        self.memoria[pagina * 4:pagina * 4 + 4] = datos
        return _SW_OK
