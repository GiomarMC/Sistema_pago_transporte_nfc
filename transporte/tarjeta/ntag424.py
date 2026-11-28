"""
Controlador de la NTAG 424 DNA (M1-04, #38).

NTAG424 envía los comandos de la tarjeta con la mensajería segura EV2
(AuthenticateEV2First, MAC y Full; tarjeta/ev2.py) por un `transmitir(apdu) ->
respuesta` inyectable: el ACR122U con pyscard (transmitir_pcsc) o
Simulador424.transmitir en las pruebas.

Tarjeta424 lo adapta a la interfaz común TarjetaFisica (contrato C3). El área
lógica de 72 B está en el archivo 03 (offsets 0-71), en CommMode.Full: lectura
con K1, escritura con K2 (docs/ntag424-personalizacion.md §9). Las claves K1 y K2
son las diversificadas por UID con AN10922 (tarjeta/an10922.py, M1-05).

Este módulo no importa pyscard: se puede usar (y probar) sin lector.
"""

import os
from typing import Callable

from tarjeta import an10922, ev2
from tarjeta import apdu424 as a
from tarjeta.base import (TAM_AREA, TAM_CABECERA, TAM_REGISTRO, OFFSET_REGISTROS, AutenticacionRechazada,
                          ErrorTarjeta, TarjetaAjena)

__all__ = ["NTAG424", "Tarjeta424", "ComandoRechazado", "transmitir_pcsc", "chip_pcsc", "abrir_pcsc",
           "claves_validador",
           "PLAIN", "MAC", "FULL"]

Transmitir = Callable[[bytes], bytes]
ClavesValidador = Callable[[bytes], tuple[bytes, bytes]]  # uid -> (K1, K2)

PLAIN, MAC, FULL = "plain", "mac", "full"
ARCHIVO_AREA = 3
CLAVE_LECTURA, CLAVE_ESCRITURA = 1, 2
_SW_OK = 0x9100
_SW_SIGUE = 0x91AF


class ComandoRechazado(ErrorTarjeta):
    """La tarjeta respondió a un comando con un código de error (91xx, 6xxx)."""

    def __init__(self, comando: str, sw: int):
        super().__init__(f"{comando}: la tarjeta respondió {sw:04X}")
        self.comando, self.sw = comando, sw


class NTAG424:
    """Comandos de la NTAG 424 DNA sobre un transmitir() inyectable.

    Guarda la sesión EV2 abierta (sesion, clave_sesion). Como la tarjeta, la
    pierde ante cualquier error, al seleccionar la aplicación o si se retira
    (DS §9.1.10, p. 28)."""

    def __init__(self, transmitir: Transmitir, uid: bytes, rng: Callable[[int], bytes] = os.urandom,
                 cerrar: Callable[[], None] | None = None):
        if len(uid) != 7:
            raise ValueError("el UID de una NTAG 424 tiene 7 bytes")
        self._transmitir = transmitir
        self.uid = bytes(uid)
        self._rng = rng
        self._cerrar = cerrar
        self.sesion: ev2.Sesion | None = None
        self.clave_sesion: int | None = None

    # --- Transporte ---------------------------------------------------------------

    def _apdu(self, apdu: bytes) -> tuple[bytes, int]:
        try:
            respuesta = self._transmitir(apdu)
        except a.TarjetaRetirada as e:
            self.olvidar_sesion()
            raise ErrorTarjeta(f"se perdió la comunicación con la tarjeta: {e}") from e
        except ErrorTarjeta:
            self.olvidar_sesion()
            raise
        try:
            return a.separar_sw(bytes(respuesta))
        except ValueError as e:
            self.olvidar_sesion()
            raise ErrorTarjeta("respuesta de la tarjeta demasiado corta") from e

    def olvidar_sesion(self) -> None:
        self.sesion = self.clave_sesion = None

    def cerrar(self) -> None:
        """Desconecta dejando la tarjeta sin autenticar."""
        try:
            self.seleccionar_aplicacion()
        except Exception:  # la tarjeta ya no está: no queda sesión que cerrar
            pass
        self.olvidar_sesion()
        if self._cerrar is not None:
            try:
                self._cerrar()
            except Exception:
                pass

    # --- Selección y autenticación --------------------------------------------------

    def seleccionar_aplicacion(self) -> None:
        """ISOSelectFile de la aplicación NDEF (DS §10.9.1, p. 77). Cierra la sesión."""
        self.olvidar_sesion()
        _, sw = self._apdu(a.seleccionar_df(a.DF_APLICACION_NDEF))
        if sw != a.SW_ISO_OK:
            raise ComandoRechazado("ISOSelectFile", sw)

    def autenticar(self, numero: int, clave: bytes) -> None:
        """AuthenticateEV2First con la clave `numero` (DS §9.1.5, p. 24).

        AutenticacionRechazada si la tarjeta no acepta la clave; TarjetaAjena si la
        acepta pero no prueba conocerla (RndA' no coincide)."""
        self.olvidar_sesion()
        datos, sw = self._apdu(a.nativo(a.INS_AUTH_EV2_FIRST, bytes([numero, 0x00])))
        if sw != _SW_SIGUE or len(datos) != ev2.TAM_BLOQUE:
            raise AutenticacionRechazada(f"AuthenticateEV2First con K{numero}: {sw:04X}")
        rnd_b = ev2.aes_cbc_descifrar(clave, ev2.IV_CERO, datos)
        rnd_a = self._rng(ev2.TAM_BLOQUE)
        parte2 = ev2.aes_cbc_cifrar(clave, ev2.IV_CERO, rnd_a + ev2.rotar_izquierda(rnd_b))
        datos, sw = self._apdu(a.nativo(a.INS_FRAME_ADICIONAL, parte2))
        if sw != _SW_OK or len(datos) != 2 * ev2.TAM_BLOQUE:
            raise AutenticacionRechazada(f"AuthenticateEV2First con K{numero}: {sw:04X}")
        claro = ev2.aes_cbc_descifrar(clave, ev2.IV_CERO, datos)
        if not ev2.mac_igual(ev2.rotar_derecha(claro[4:20]), rnd_a):
            raise TarjetaAjena("la tarjeta no devolvió RndA rotado")
        enc, mac = ev2.claves_sesion(clave, rnd_a, rnd_b)
        self.sesion, self.clave_sesion = ev2.Sesion(enc, mac, claro[:4]), numero

    # --- Comandos -----------------------------------------------------------------

    def comando(self, ins: int, cabecera: bytes = b"", datos: bytes = b"", modo: str = PLAIN,
                nombre: str | None = None) -> bytes:
        """Envía un comando nativo con la mensajería `modo` y devuelve los datos de
        la respuesta ya comprobados (MAC) y descifrados (Full)."""
        nombre = nombre or f"comando {ins:02X}"
        sesion = self.sesion
        if modo != PLAIN and sesion is None:
            raise ErrorTarjeta(f"{nombre}: hace falta autenticar antes")
        if sesion is not None and sesion.agotada:
            self.olvidar_sesion()
            raise ErrorTarjeta(f"{nombre}: CmdCtr agotado, hay que volver a autenticar")
        if modo == FULL and datos:
            datos = sesion.cifrar_comando(datos)
        cuerpo = cabecera + datos
        if modo != PLAIN:
            cuerpo += sesion.mac_comando(ins, cabecera, datos)
        respuesta, sw = self._apdu(a.nativo(ins, cuerpo))
        if sw != _SW_OK:
            self.olvidar_sesion()
            raise ComandoRechazado(nombre, sw)
        if sesion is None:
            return respuesta
        siguiente = sesion.avanzada()
        if modo != PLAIN:
            if len(respuesta) < ev2.TAM_MAC:
                self.olvidar_sesion()
                raise ErrorTarjeta(f"{nombre}: respuesta sin MAC")
            respuesta, mac = respuesta[:-ev2.TAM_MAC], respuesta[-ev2.TAM_MAC:]
            if not ev2.mac_igual(mac, siguiente.mac_respuesta(a.OPERATION_OK, respuesta)):
                self.olvidar_sesion()
                raise ErrorTarjeta(f"{nombre}: MAC de la respuesta incorrecto")
            if modo == FULL and respuesta:
                try:
                    respuesta = siguiente.descifrar_respuesta(respuesta)
                except ev2.ErrorIntegridad as e:
                    self.olvidar_sesion()
                    raise ErrorTarjeta(f"{nombre}: {e}") from e
        self.sesion = siguiente
        return respuesta

    @staticmethod
    def _cabecera_datos(archivo: int, offset: int, longitud: int) -> bytes:
        return bytes([archivo]) + offset.to_bytes(3, "little") + longitud.to_bytes(3, "little")

    def leer_datos(self, archivo: int, offset: int, longitud: int, modo: str = FULL) -> bytes:
        """ReadData (DS §10.8.1, p. 73). Devuelve exactamente `longitud` bytes."""
        datos = self.comando(a.INS_READ_DATA, self._cabecera_datos(archivo, offset, longitud), modo=modo,
                             nombre="ReadData")
        if len(datos) != longitud:
            raise ErrorTarjeta(f"ReadData: {len(datos)} bytes en lugar de {longitud}")
        return datos

    def escribir_datos(self, archivo: int, offset: int, datos: bytes, modo: str = FULL) -> None:
        """WriteData de una sola trama (DS §10.8.2, p. 75): atómico hasta 128 B (§7)."""
        self.comando(a.INS_WRITE_DATA, self._cabecera_datos(archivo, offset, len(datos)), datos, modo,
                     "WriteData")

    def ajustes_archivo(self, archivo: int) -> bytes:
        """GetFileSettings (DS §10.7.2, p. 69): MAC con sesión, en claro sin ella."""
        return self.comando(a.INS_GET_FILE_SETTINGS, bytes([archivo]), modo=MAC if self.sesion else PLAIN,
                            nombre="GetFileSettings")

    def cambiar_ajustes(self, archivo: int, ajustes: bytes) -> None:
        """ChangeFileSettings en CommMode.Full (DS §10.7.1, p. 65)."""
        self.comando(a.INS_CHANGE_FILE_SETTINGS, bytes([archivo]), ajustes, FULL, "ChangeFileSettings")

    def version_clave(self, numero: int) -> int:
        """GetKeyVersion (DS §10.6.2): KeyVer de la clave; 00h = de fábrica."""
        datos = self.comando(a.INS_GET_KEY_VERSION, bytes([numero]), modo=MAC if self.sesion else PLAIN,
                             nombre="GetKeyVersion")
        if len(datos) != 1:
            raise ErrorTarjeta(f"GetKeyVersion: {len(datos)} bytes")
        return datos[0]

    def leer_ndef(self) -> bytes:
        """El archivo 02 como lo lee un celular: sin autenticar, con ISOSelectFile de E104h e
        ISOReadBinary (DS §10.9). Con SDM, trae el SUN y sube SDMReadCtr."""
        self.seleccionar_aplicacion()
        for nombre, apdu in (("ISOSelectFile E104", "00A4000002E104"), ("ISOReadBinary", "00B0000000")):
            datos, sw = self._apdu(bytes.fromhex(apdu))
            if sw != a.SW_ISO_OK:
                raise ComandoRechazado(nombre, sw)
        return datos

    def cambiar_clave(self, numero: int, nueva: bytes, anterior: bytes, version: int) -> None:
        """ChangeKey (DS §10.6.1, p. 62); exige sesión con K0. Si se cambia la clave
        de la sesión, la tarjeta la cierra y responde sin MAC (AN12196 §5.16.2)."""
        datos = ev2.datos_change_key(numero, nueva, anterior, version)
        if numero != self.clave_sesion:
            self.comando(a.INS_CHANGE_KEY, bytes([numero]), datos, FULL, "ChangeKey")
            return
        sesion = self.sesion
        cifrado = sesion.cifrar_comando(datos)
        cabecera = bytes([numero])
        self.olvidar_sesion()
        respuesta, sw = self._apdu(a.nativo(a.INS_CHANGE_KEY,
                                            cabecera + cifrado + sesion.mac_comando(a.INS_CHANGE_KEY, cabecera,
                                                                                    cifrado)))
        if sw != _SW_OK:
            raise ComandoRechazado("ChangeKey", sw)
        if respuesta:
            raise ErrorTarjeta("ChangeKey de la clave de la sesión: respuesta inesperada")


class Tarjeta424:
    """NTAG 424 DNA vista como TarjetaFisica (contrato C3).

    autenticar() abre la sesión con K1 (lectura); la primera escritura la cambia por
    una con K2, que lee y escribe (ReadWrite = K2, §9). `claves(uid) -> (K1, K2)`."""

    tipo = "NTAG 424 DNA"

    def __init__(self, chip: NTAG424, claves: ClavesValidador):
        self.chip = chip
        self._claves = claves

    @property
    def uid(self) -> bytes:
        return self.chip.uid

    def cerrar(self) -> None:
        self.chip.cerrar()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cerrar()

    # --- TarjetaFisica (C3) -------------------------------------------------------

    def autenticar(self) -> None:
        self.chip.seleccionar_aplicacion()
        self.chip.autenticar(CLAVE_LECTURA, self._claves(self.uid)[0])

    def leer_area(self) -> bytes:
        return self.chip.leer_datos(ARCHIVO_AREA, 0, TAM_AREA)

    def _para_escribir(self) -> None:
        if self.chip.clave_sesion == CLAVE_ESCRITURA:
            return
        if self.chip.clave_sesion is None:
            raise ErrorTarjeta("hace falta autenticar() antes de escribir")
        try:
            self.chip.autenticar(CLAVE_ESCRITURA, self._claves(self.uid)[1])
        except ErrorTarjeta as e:
            raise ErrorTarjeta(f"la tarjeta rechazó la clave de escritura: {e}") from e

    def escribir_cabecera(self, datos: bytes) -> None:
        if len(datos) != TAM_CABECERA:
            raise ValueError(f"la cabecera son {TAM_CABECERA} bytes")
        self._para_escribir()
        self.chip.escribir_datos(ARCHIVO_AREA, 0, datos)

    def escribir_registro(self, indice: int, datos: bytes) -> None:
        if indice not in (0, 1):
            raise ValueError("el registro es 0 (A) o 1 (B)")
        if len(datos) != TAM_REGISTRO:
            raise ValueError(f"un registro son {TAM_REGISTRO} bytes")
        self._para_escribir()
        offset = OFFSET_REGISTROS[indice]
        self.chip.escribir_datos(ARCHIVO_AREA, offset, datos)
        if self.chip.leer_datos(ARCHIVO_AREA, offset, TAM_REGISTRO) != datos:
            raise ErrorTarjeta("verificación de escritura")

    def contador(self) -> None:
        """None (contrato C3): SDMReadCtr solo sube con lecturas de celular, no de los
        validadores, y nadie puede leerlo (SDMCtrRet = F, §8.3). La 424 no se puede
        clonar: la autenticación AES mutua ya prueba que es la tarjeta original."""
        return None

    def reiniciar_sesion(self) -> None:
        """Pierde la autenticación, como un toque nuevo."""
        self.chip.seleccionar_aplicacion()


def transmitir_pcsc(conexion) -> Transmitir:
    """transmitir() sobre una conexión de pyscard (ACR122U). Los errores de
    comunicación se convierten en ErrorTarjeta."""
    def transmitir(apdu: bytes) -> bytes:
        try:
            datos, sw1, sw2 = conexion.transmit(list(apdu))
            if sw1 == 0x61:  # el lector pide GET RESPONSE (firmware antiguo del ACR122U)
                datos, sw1, sw2 = conexion.transmit([0xFF, 0xC0, 0x00, 0x00, sw2])
        except Exception as e:
            if type(e).__module__.startswith("smartcard"):
                raise ErrorTarjeta(f"se perdió la comunicación con la tarjeta: {e}") from e
            raise
        return bytes(datos) + bytes([sw1, sw2])
    return transmitir


def chip_pcsc(conexion) -> NTAG424:
    """NTAG424 sobre una conexión PC/SC; el UID se pide al lector (GET DATA, FF CA)."""
    transmitir = transmitir_pcsc(conexion)
    uid, sw = a.separar_sw(transmitir(bytes.fromhex("FFCA000000")))
    if sw != a.SW_ISO_OK:
        raise ErrorTarjeta(f"el lector no devolvió el UID ({sw:04X})")
    return NTAG424(transmitir, uid, cerrar=conexion.disconnect)


def abrir_pcsc(conexion, claves: ClavesValidador) -> Tarjeta424:
    """Tarjeta424 sobre una conexión PC/SC."""
    return Tarjeta424(chip_pcsc(conexion), claves)


def claves_validador(maestra_k1: bytes, maestra_k2: bytes, version: int = 1) -> ClavesValidador:
    """claves(uid) -> (K1, K2) diversificadas con AN10922 (ranuras 1 y 2, §6.1)."""
    def claves(uid: bytes) -> tuple[bytes, bytes]:
        return (an10922.diversificar_aes128(maestra_k1, uid, 1, version),
                an10922.diversificar_aes128(maestra_k2, uid, 2, version))
    return claves
