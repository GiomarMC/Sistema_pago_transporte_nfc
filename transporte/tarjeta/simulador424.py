"""
NTAG 424 DNA simulada en memoria, para probar sin tarjetas (M1-03, parte a).

`Simulador424.transmitir(apdu) -> respuesta` se comporta como la tarjeta detrás
de un lector PC/SC: recibe la APDU completa y devuelve datos + SW.

Implementado (parte a): ISOSelectFile por nombre de DF, GetVersion (sin
autenticar), AuthenticateEV2First, ReadData y WriteData en Plain/MAC/Full,
GetFileSettings, ChangeFileSettings sin SDM y ChangeKey. Reglas de acceso de
DS §8.2.3.3-§8.2.3.5 (pp. 11-13) y códigos de error de DS §10.3 (p. 45) y de
cada comando.

Implementado (parte b, SDM/SUN del archivo 02, docs/ntag424-personalizacion.md §8):
ChangeFileSettings con SDM, GetFileSettings con sus campos, ISOSelectFile por
FileID, ISOReadBinary, GetFileCounters y el contador SDMReadCtr. Solo la
configuración que usa SubePe (AN12196 Tabla 18): PICCData cifrado con
SDMMetaRead (UID y contador, PICCDataTag C7), SDMMAC con SDMFileRead y espejo en
ASCII. El resto (espejo en claro, SDMENCFileData, SDMReadCtrLimit, SDM en otro
archivo) lanza NotImplementedError para que ninguna prueba dependa de un
comportamiento inventado. Tampoco AuthenticateEV2NonFirst, LRP ni el contador de
autenticaciones fallidas (decisión F).

GetKeyVersion (para personalizar_424.py, M1-06): libre en claro, con MAC si hay
sesión, como GetFileSettings. Sin contrastar con una tarjeta real.

Simplificaciones de la parte b, sin contrastar con una tarjeta real:
  - SDMReadCtr sube con la primera lectura sin autenticar del archivo 02 tras
    acercar la tarjeta o seleccionar un archivo; las siguientes lecturas de esa
    misma selección devuelven el mismo espejo (un celular lee varias veces por toque).
  - Con sesión abierta, leer el archivo 02 devuelve lo grabado, sin espejo.

Estado de fábrica: DS Tabla 5 (p. 10), Tabla 8 (p. 12) y Tabla 13 (p. 14);
claves a cero (AN12196 Tabla 14).
"""

import os
from dataclasses import dataclass, field
from typing import Callable

from tarjeta import apdu424 as a
from tarjeta import ev2

LIBRE, SIN_ACCESO = 0xE, 0xF
COMM_PLAIN, COMM_MAC, COMM_FULL = 0b00, 0b01, 0b11
_OPCION_SDM = 0x40
_OPCION_RFU = 0xBC
# SDMOptions (DS §10.7.1, Tabla 69)
_SDM_UID, _SDM_CONTADOR, _SDM_LIMITE, _SDM_ENC_DATOS, _SDM_ASCII = 0x80, 0x40, 0x20, 0x10, 0x01
_SDM_RFU = 0x0E
_ETIQUETA_SV_SDM_MAC = bytes.fromhex("3CC300010080")  # DS §9.3.9.1, p. 41
_PICC_DATA_TAG = 0xC7                                 # UID de 7 B y contador en el espejo
_TAM_PICC_ASCII, _TAM_MAC_ASCII = 32, 16
_CONTADOR_SDM_MAXIMO = 0xFFFFFF
# FileID de ISO de los tres archivos de la aplicación (DS Tabla 5, p. 10)
_FILE_ID = {0xE103: 1, 0xE104: 2, 0xE105: 3}
_SW_ISO_SIN_EF = 0x6986
_SW_ISO_SEGURIDAD = 0x6982
_SW_ISO_FUERA_DE_RANGO = 0x6B00
_SW_ISO_LONGITUD = 0x6700
_NUM_CLAVES = 5
_TAM_CABECERA_DATOS = 7  # FileNo, Offset(3), Length(3)
_VERSION_FIJA = (bytes.fromhex("04040830001105"), bytes.fromhex("04040201011105"))
_LOTE_Y_FECHA = bytes.fromhex("CD65935D402118")  # AN12196 Tabla 12, solo de muestra


@dataclass(frozen=True)
class ConfigSdm:
    """Campos SDM de ChangeFileSettings que usa SubePe (DS §10.7.1, AN12196 Tabla 18)."""
    opciones: int
    meta_read: int        # clave del PICCData cifrado
    file_read: int        # clave del SDMMAC
    ctr_ret: int          # quién lee el contador con GetFileCounters
    picc_offset: int
    mac_input_offset: int
    mac_offset: int
    crudo: bytes          # SDMOptions en adelante, tal como llegó (lo devuelve GetFileSettings)


@dataclass
class Archivo:
    opcion: int        # FileOption: bit 6 SDM, bits 1-0 CommMode
    derechos: int      # Read<<12 | Write<<8 | ReadWrite<<4 | Change (DS Tabla 7)
    datos: bytearray
    sdm: ConfigSdm | None = field(default=None)

    @property
    def comm_mode(self) -> int:
        modo = self.opcion & 0b11
        return COMM_PLAIN if modo & 0b01 == 0 else modo

    def condicion(self, desplazamiento: int) -> int:
        return (self.derechos >> desplazamiento) & 0xF

    @property
    def lectura(self) -> tuple[int, int]:
        return self.condicion(12), self.condicion(4)   # Read, ReadWrite

    @property
    def escritura(self) -> tuple[int, int]:
        return self.condicion(8), self.condicion(4)    # Write, ReadWrite

    @property
    def cambio(self) -> int:
        return self.condicion(0)


def derechos_en_cable(derechos: int) -> bytes:
    """AccessRights tal como viajan: LSB primero (AN12196 Tabla 11: 00E0 = Read E)."""
    return derechos.to_bytes(2, "little")


def archivos_de_fabrica() -> dict[int, Archivo]:
    return {
        1: Archivo(0x00, 0xE000, bytearray(32)),    # CC (contenido no emulado)
        2: Archivo(0x00, 0xEEE0, bytearray(256)),   # NDEF
        3: Archivo(0x03, 0x2330, bytearray(128)),   # propietario, CommMode.Full
    }


class _Error(Exception):
    """Código 91xx que corta el comando y la autenticación."""

    def __init__(self, codigo: int):
        super().__init__(f"91{codigo:02X}")
        self.codigo = codigo


class Simulador424:
    def __init__(self, uid: bytes = bytes.fromhex("04DE5F1EACC040"),
                 rng: Callable[[int], bytes] = os.urandom):
        if len(uid) != 7:
            raise ValueError("el UID de una NTAG 424 tiene 7 bytes")
        self.uid = uid
        self._rng = rng
        self.claves = [bytes(16)] * _NUM_CLAVES
        self.versiones = [0] * _NUM_CLAVES
        self.archivos = archivos_de_fabrica()
        self.cortar_en_byte: int | None = None  # APDU más largas: la tarjeta se retira
        self.presente = True
        self.aplicacion = False
        self.sesion: ev2.Sesion | None = None
        self.clave_sesion: int | None = None
        self._pendiente = None
        self.contador_sdm = 0                 # SDMReadCtr del archivo 02 (24 bits)
        self.ef: int | None = None            # archivo seleccionado por FileID (ISOReadBinary)
        self._espejo: bytes | None = None     # contenido con SUN de la selección actual

    # --- Ciclo de vida ------------------------------------------------------------

    def acercar(self) -> None:
        """La tarjeta vuelve al campo: se reinicia (sin selección ni autenticación)."""
        self.presente = True
        self.aplicacion = False
        self.ef = None
        self._espejo = None
        self._perder_autenticacion()

    def con_sesion(self, sesion: ev2.Sesion, clave: int) -> None:
        """Fija una sesión ya negociada (para reproducir los ejemplos de AN12196,
        que parten de sesiones abiertas por comandos no emulados)."""
        self.aplicacion = True
        self.sesion, self.clave_sesion = sesion, clave

    def _perder_autenticacion(self) -> None:
        self.sesion = self.clave_sesion = None
        self._pendiente = None

    # --- Entrada ------------------------------------------------------------------

    def transmitir(self, apdu: bytes) -> bytes:
        apdu = bytes(apdu)
        if not self.presente:
            raise a.TarjetaRetirada("la tarjeta no está en el campo")
        if self.cortar_en_byte is not None and len(apdu) > self.cortar_en_byte:
            # La trama no llega entera: no se aplica nada (DS §8.6, p. 19)
            self.presente = False
            self._perder_autenticacion()
            raise a.TarjetaRetirada(f"retirada tras {self.cortar_en_byte} bytes")
        if len(apdu) < 4:
            return (0x6700).to_bytes(2, "big")
        if apdu[0] == a.CLA_ISO:
            return self._iso(apdu)
        if apdu[0] == a.CLA_NATIVO:
            return self._nativo(apdu)
        return a.SW_ISO_CLA_NO_SOPORTADA.to_bytes(2, "big")

    def _iso(self, apdu: bytes) -> bytes:
        if apdu[1] == a.INS_READ_BINARY:
            return self._iso_read_binary(apdu)
        if apdu[1] != a.INS_SELECT:
            return a.SW_ISO_INS_NO_SOPORTADA.to_bytes(2, "big")
        nombre = apdu[5:5 + apdu[4]] if len(apdu) > 5 else b""
        self._espejo = None
        if apdu[2] in (0x00, 0x02) and len(nombre) == 2:
            # FileID de un archivo de la aplicación: no toca la autenticación
            numero = _FILE_ID.get(int.from_bytes(nombre, "big"))
            if not self.aplicacion or numero is None:
                return a.SW_ISO_NO_ENCONTRADO.to_bytes(2, "big")
            self.ef = numero
            return a.SW_ISO_OK.to_bytes(2, "big")
        if apdu[2] != 0x04:
            raise NotImplementedError(f"ISOSelectFile con P1 = {apdu[2]:02X}h")
        self._perder_autenticacion()
        self.ef = None
        if nombre == a.DF_APLICACION_NDEF:
            self.aplicacion = True
        elif nombre == a.DF_PICC:
            self.aplicacion = False
        else:
            return a.SW_ISO_NO_ENCONTRADO.to_bytes(2, "big")
        return a.SW_ISO_OK.to_bytes(2, "big")

    def _iso_read_binary(self, apdu: bytes) -> bytes:
        """ISOReadBinary del archivo seleccionado por FileID (DS §10.9.2): 00 B0 P1 P2 Le,
        offset en P1-P2 (P1 < 80h), Le = 00 hasta el final. Solo archivos de lectura libre."""
        if len(apdu) != 5:
            return _SW_ISO_LONGITUD.to_bytes(2, "big")
        if self.ef is None:
            return _SW_ISO_SIN_EF.to_bytes(2, "big")
        if apdu[2] & 0x80:
            raise NotImplementedError("ISOReadBinary con ShortFileID en P1")
        archivo = self.archivos[self.ef]
        if LIBRE not in archivo.lectura:
            return _SW_ISO_SEGURIDAD.to_bytes(2, "big")
        offset = int.from_bytes(apdu[2:4], "big")
        if offset >= len(archivo.datos):
            return _SW_ISO_FUERA_DE_RANGO.to_bytes(2, "big")
        contenido = self._contenido(self.ef, archivo)
        longitud = apdu[4] or len(contenido) - offset
        if offset + longitud > len(contenido):
            return _SW_ISO_FUERA_DE_RANGO.to_bytes(2, "big")
        return contenido[offset:offset + longitud] + a.SW_ISO_OK.to_bytes(2, "big")

    # --- SDM / SUN (DS §9.3, pp. 35-42) --------------------------------------------

    def _contenido(self, numero: int, archivo: Archivo) -> bytes:
        """Lo que lee quien no está autenticado: con SDM, el archivo con el SUN en el espejo."""
        if archivo.sdm is None or self.sesion is not None:
            return bytes(archivo.datos)
        if self._espejo is None:
            if self.contador_sdm < _CONTADOR_SDM_MAXIMO:
                self.contador_sdm += 1
            self._espejo = self._con_sun(archivo)
        return self._espejo

    def _con_sun(self, archivo: Archivo) -> bytes:
        sdm = archivo.sdm
        contador = self.contador_sdm.to_bytes(3, "little")
        datos = bytearray(archivo.datos)
        picc = bytes([_PICC_DATA_TAG]) + self.uid + contador + self._rng(5)
        cifrado = ev2.aes_cbc_cifrar(self.claves[sdm.meta_read], ev2.IV_CERO, picc)
        datos[sdm.picc_offset:sdm.picc_offset + _TAM_PICC_ASCII] = cifrado.hex().upper().encode()
        clave_sesion = ev2.cmac(self.claves[sdm.file_read], _ETIQUETA_SV_SDM_MAC + self.uid + contador)
        mac = ev2.mac_truncado(clave_sesion, bytes(datos[sdm.mac_input_offset:sdm.mac_offset]))
        datos[sdm.mac_offset:sdm.mac_offset + _TAM_MAC_ASCII] = mac.hex().upper().encode()
        return bytes(datos)

    def _leer_sdm(self, archivo: Archivo, cuerpo: bytes) -> ConfigSdm:
        """Campos SDM de ChangeFileSettings, desde SDMOptions (DS §10.7.1, Tabla 69)."""
        if len(cuerpo) < 3:
            raise _Error(a.LENGTH_ERROR)
        opciones = cuerpo[0]
        derechos = int.from_bytes(cuerpo[1:3], "little")
        # En el cable: RFU|SDMCtrRet, SDMMetaRead|SDMFileRead (AN12196 Tabla 18: F121)
        meta_read, file_read = (derechos >> 12) & 0xF, (derechos >> 8) & 0xF
        rfu, ctr_ret = (derechos >> 4) & 0xF, derechos & 0xF
        if opciones & _SDM_RFU or not opciones & _SDM_ASCII or rfu != 0xF:
            raise _Error(a.PARAMETER_ERROR)
        if any(5 <= c < LIBRE for c in (meta_read, file_read, ctr_ret)):
            raise _Error(a.PARAMETER_ERROR)
        if (opciones & (_SDM_LIMITE | _SDM_ENC_DATOS) or meta_read > 4 or file_read > 4
                or opciones & (_SDM_UID | _SDM_CONTADOR) != _SDM_UID | _SDM_CONTADOR):
            raise NotImplementedError("SDM distinto del de SubePe (PICCData cifrado + SDMMAC)")
        if self.archivos.get(2) is not archivo:
            raise NotImplementedError("SDM solo en el archivo 02")
        if len(cuerpo) != 3 + 9:
            raise _Error(a.LENGTH_ERROR)
        picc, mac_input, mac = (int.from_bytes(cuerpo[i:i + 3], "little") for i in (3, 6, 9))
        tamano = len(archivo.datos)
        if (picc + _TAM_PICC_ASCII > tamano or mac + _TAM_MAC_ASCII > tamano or mac_input > mac
                or picc < mac + _TAM_MAC_ASCII and mac < picc + _TAM_PICC_ASCII):
            raise _Error(a.PARAMETER_ERROR)
        return ConfigSdm(opciones, meta_read, file_read, ctr_ret, picc, mac_input, mac, bytes(cuerpo))

    def _nativo(self, apdu: bytes) -> bytes:
        ins = apdu[1]
        if len(apdu) == 5:
            datos = b""
        elif len(apdu) >= 6 and len(apdu) == 6 + apdu[4]:
            datos = apdu[5:5 + apdu[4]]
        else:
            self._perder_autenticacion()  # cualquier error corta la sesión (DS §9.1.10)
            return a.sw_nativo(a.LENGTH_ERROR)

        pendiente, self._pendiente = self._pendiente, None
        if ins == a.INS_FRAME_ADICIONAL:
            return self._continuar(pendiente, datos)
        if pendiente is not None and pendiente[0] == "auth":
            self._perder_autenticacion()
            return a.sw_nativo(a.COMMAND_ABORTED)
        if ins == a.INS_GET_VERSION:
            return self._get_version()
        if not self.aplicacion:
            return a.sw_nativo(a.PERMISSION_DENIED)
        if ins == a.INS_AUTH_EV2_FIRST:
            return self._autenticar_1(datos)
        comandos = {
            a.INS_GET_FILE_COUNTERS: (1, self._prep_get_file_counters, self._get_file_counters),
            a.INS_GET_FILE_SETTINGS: (1, self._prep_get_file_settings, self._get_file_settings),
            a.INS_READ_DATA: (_TAM_CABECERA_DATOS, self._prep_read_data, self._read_data),
            a.INS_WRITE_DATA: (_TAM_CABECERA_DATOS, self._prep_write_data, self._write_data),
            a.INS_CHANGE_FILE_SETTINGS: (1, self._prep_change_file_settings,
                                         self._change_file_settings),
            a.INS_CHANGE_KEY: (1, self._prep_change_key, self._change_key),
            a.INS_GET_KEY_VERSION: (1, self._prep_get_key_version, self._get_key_version),
        }
        if ins not in comandos:
            self._perder_autenticacion()
            return a.sw_nativo(a.ILLEGAL_COMMAND_CODE)
        tam_cabecera, preparar, ejecutar = comandos[ins]
        return self._con_mensajeria(ins, datos, tam_cabecera, preparar, ejecutar)

    # --- Mensajería segura (DS §9.1.8-§9.1.10, pp. 27-28) ----------------------------

    def _con_mensajeria(self, ins, datos, tam_cabecera, preparar, ejecutar) -> bytes:
        try:
            if len(datos) < tam_cabecera:
                raise _Error(a.LENGTH_ERROR)
            cabecera = datos[:tam_cabecera]
            modo = preparar(cabecera)
            cuerpo = datos[tam_cabecera:]
            if modo != COMM_PLAIN:
                cuerpo = self._verificar_comando(ins, cabecera, cuerpo, modo)
            elif self.sesion is not None and self.sesion.agotada:
                raise _Error(a.INTEGRITY_ERROR)
            respuesta, terminar_sesion = ejecutar(cabecera, cuerpo)
        except _Error as e:
            self._perder_autenticacion()
            return a.sw_nativo(e.codigo)
        if terminar_sesion:  # se cambió la clave de la sesión: 9100 sin MAC (AN12196 Tabla 26)
            self._perder_autenticacion()
            return a.sw_nativo(a.OPERATION_OK)
        if self.sesion is None:
            return respuesta + a.sw_nativo(a.OPERATION_OK)
        self.sesion = self.sesion.avanzada()  # CmdCtr sube en todos los modos (DS §9.1.2)
        if modo == COMM_PLAIN:
            return respuesta + a.sw_nativo(a.OPERATION_OK)
        if modo == COMM_FULL and respuesta:
            respuesta = self.sesion.cifrar_respuesta(respuesta)
        respuesta += self.sesion.mac_respuesta(a.OPERATION_OK, respuesta)
        return self._sin_encadenar(respuesta) + a.sw_nativo(a.OPERATION_OK)

    @staticmethod
    def _sin_encadenar(respuesta: bytes) -> bytes:
        """La respuesta corta llega a 256 bytes con la mensajería incluida (DS Tabla 79);
        más exigiría tramas encadenadas (91 AF), que no se emulan."""
        if len(respuesta) > 256:
            raise NotImplementedError("respuestas de más de 256 bytes (encadenadas): leer por partes")
        return respuesta

    def _verificar_comando(self, ins, cabecera, cuerpo, modo) -> bytes:
        sesion = self.sesion
        if sesion is None or sesion.agotada or len(cuerpo) < ev2.TAM_MAC:
            raise _Error(a.INTEGRITY_ERROR)
        datos, mac = cuerpo[:-ev2.TAM_MAC], cuerpo[-ev2.TAM_MAC:]
        if not ev2.mac_igual(mac, sesion.mac_comando(ins, cabecera, datos)):
            raise _Error(a.INTEGRITY_ERROR)
        if modo == COMM_FULL and datos:
            try:
                datos = sesion.descifrar_comando(datos)
            except ev2.ErrorIntegridad:
                raise _Error(a.INTEGRITY_ERROR)
        return datos

    def _modo_acceso(self, archivo: Archivo, condiciones: tuple[int, ...]) -> int:
        """Modo a aplicar o _Error, según DS §8.2.3.3 y §8.2.3.5 (pp. 11-13)."""
        if self.sesion is not None and self.clave_sesion in condiciones:
            return archivo.comm_mode
        if LIBRE in condiciones:
            return COMM_PLAIN  # acceso libre: Plain aunque haya sesión
        if all(c == SIN_ACCESO for c in condiciones):
            raise _Error(a.PERMISSION_DENIED)
        raise _Error(a.AUTHENTICATION_ERROR)

    def _archivo(self, numero: int) -> Archivo:
        archivo = self.archivos.get(numero & 0x1F)
        if archivo is None or numero & 0xE0:
            raise _Error(a.FILE_NOT_FOUND)
        return archivo

    # --- GetVersion ---------------------------------------------------------------

    def _get_version(self) -> bytes:
        if self.sesion is not None:
            raise NotImplementedError("GetVersion con sesión (CommMode.MAC): no lo usa SubePe")
        self._pendiente = ("version", 1)
        return _VERSION_FIJA[0] + a.sw_nativo(a.ADDITIONAL_FRAME)

    def _continuar(self, pendiente, datos: bytes) -> bytes:
        if pendiente is None:
            return a.sw_nativo(a.ILLEGAL_COMMAND_CODE)
        if pendiente[0] == "auth":
            return self._autenticar_2(pendiente, datos)
        trama = pendiente[1]
        if trama == 1:
            self._pendiente = ("version", 2)
            return _VERSION_FIJA[1] + a.sw_nativo(a.ADDITIONAL_FRAME)
        return self.uid + _LOTE_Y_FECHA + a.sw_nativo(a.OPERATION_OK)

    # --- AuthenticateEV2First (DS §10.4.1, p. 46) ---------------------------------

    def _autenticar_1(self, datos: bytes) -> bytes:
        self._perder_autenticacion()
        if len(datos) < 2 or len(datos) != 2 + datos[1] or datos[1] > 6:
            return a.sw_nativo(a.LENGTH_ERROR)
        numero = datos[0] & 0x3F
        if datos[0] & 0xC0 or numero >= _NUM_CLAVES:
            return a.sw_nativo(a.NO_SUCH_KEY)
        rnd_b = self._rng(16)
        self._pendiente = ("auth", numero, rnd_b)
        cifrado = ev2.aes_cbc_cifrar(self.claves[numero], ev2.IV_CERO, rnd_b)
        return cifrado + a.sw_nativo(a.ADDITIONAL_FRAME)

    def _autenticar_2(self, pendiente, datos: bytes) -> bytes:
        _, numero, rnd_b = pendiente
        if len(datos) != 32:
            return a.sw_nativo(a.LENGTH_ERROR)
        clave = self.claves[numero]
        claro = ev2.aes_cbc_descifrar(clave, ev2.IV_CERO, datos)
        rnd_a, rnd_b_rotado = claro[:16], claro[16:]
        if rnd_b_rotado != ev2.rotar_izquierda(rnd_b):
            return a.sw_nativo(a.AUTHENTICATION_ERROR)
        ti = self._rng(ev2.TAM_TI)
        enc, mac = ev2.claves_sesion(clave, rnd_a, rnd_b)
        self.sesion, self.clave_sesion = ev2.Sesion(enc, mac, ti), numero
        respuesta = ti + ev2.rotar_izquierda(rnd_a) + bytes(6) + bytes(6)  # PDcap2, PCDcap2
        return ev2.aes_cbc_cifrar(clave, ev2.IV_CERO, respuesta) + a.sw_nativo(a.OPERATION_OK)

    # --- Comandos de archivos -----------------------------------------------------------

    def _prep_get_file_settings(self, cabecera: bytes) -> int:
        self._archivo(cabecera[0])
        return COMM_MAC if self.sesion is not None else COMM_PLAIN

    def _get_file_settings(self, cabecera: bytes, cuerpo: bytes):
        if cuerpo:
            raise _Error(a.LENGTH_ERROR)
        archivo = self._archivo(cabecera[0])
        tamano = len(archivo.datos).to_bytes(3, "little")
        sdm = archivo.sdm.crudo if archivo.sdm else b""
        return bytes([0x00, archivo.opcion]) + derechos_en_cable(archivo.derechos) + tamano + sdm, False

    # --- GetKeyVersion (DS §10.6.2) --------------------------------------------------

    def _prep_get_key_version(self, cabecera: bytes) -> int:
        if cabecera[0] >= _NUM_CLAVES:
            raise _Error(a.NO_SUCH_KEY)
        return COMM_MAC if self.sesion is not None else COMM_PLAIN

    def _get_key_version(self, cabecera: bytes, cuerpo: bytes):
        if cuerpo:
            raise _Error(a.LENGTH_ERROR)
        return bytes([self.versiones[cabecera[0]]]), False

    # --- GetFileCounters (DS §10.7.3) -----------------------------------------------

    def _prep_get_file_counters(self, cabecera: bytes) -> int:
        """CommMode.Full con la clave SDMCtrRet; libre (E) en claro (DS §10.7.3)."""
        archivo = self._archivo(cabecera[0])
        if archivo.sdm is None:
            raise _Error(a.PERMISSION_DENIED)
        if self.sesion is not None and self.clave_sesion == archivo.sdm.ctr_ret:
            return COMM_FULL
        return self._modo_acceso(archivo, (archivo.sdm.ctr_ret,))

    def _get_file_counters(self, cabecera: bytes, cuerpo: bytes):
        if cuerpo:
            raise _Error(a.LENGTH_ERROR)
        return self.contador_sdm.to_bytes(3, "little") + bytes(2), False  # + 2 B reservados

    @staticmethod
    def _rango(cabecera: bytes, archivo: Archivo, *, escritura: bool) -> tuple[int, int]:
        """ReadData: Length 0 = hasta el final; fuera de rango, PARAMETER_ERROR
        (DS Tablas 78 y 80, p. 74). WriteData: Length >= 1; pasar del final,
        BOUNDARY_ERROR (DS Tablas 81 y 83, pp. 75-76)."""
        offset = int.from_bytes(cabecera[1:4], "little")
        longitud = int.from_bytes(cabecera[4:7], "little")
        tamano = len(archivo.datos)
        if offset >= tamano:
            raise _Error(a.PARAMETER_ERROR)
        if longitud == 0:
            if escritura:
                raise _Error(a.PARAMETER_ERROR)
            longitud = tamano - offset
        if offset + longitud > tamano:
            raise _Error(a.BOUNDARY_ERROR if escritura else a.PARAMETER_ERROR)
        return offset, longitud

    def _prep_read_data(self, cabecera: bytes) -> int:
        archivo = self._archivo(cabecera[0])
        return self._modo_acceso(archivo, archivo.lectura)

    def _read_data(self, cabecera: bytes, cuerpo: bytes):
        if cuerpo:
            raise _Error(a.LENGTH_ERROR)
        archivo = self._archivo(cabecera[0])
        offset, longitud = self._rango(cabecera, archivo, escritura=False)
        contenido = self._contenido(cabecera[0] & 0x1F, archivo)
        return contenido[offset:offset + longitud], False

    def _prep_write_data(self, cabecera: bytes) -> int:
        archivo = self._archivo(cabecera[0])
        return self._modo_acceso(archivo, archivo.escritura)

    def _write_data(self, cabecera: bytes, cuerpo: bytes):
        archivo = self._archivo(cabecera[0])
        offset, longitud = self._rango(cabecera, archivo, escritura=True)
        if len(cuerpo) != longitud:
            raise _Error(a.LENGTH_ERROR)
        archivo.datos[offset:offset + longitud] = cuerpo
        self._espejo = None
        return b"", False

    def _prep_change_file_settings(self, cabecera: bytes) -> int:
        """Siempre Full con sesión, sea cual sea el CommMode del archivo (DS Tabla 22,
        p. 44; AN12196 Tabla 18 lo cifra sobre el archivo 02, que es Plain)."""
        archivo = self._archivo(cabecera[0])
        if self.sesion is not None and self.clave_sesion == archivo.cambio:
            return COMM_FULL
        return self._modo_acceso(archivo, (archivo.cambio,))  # libre: Plain; si no, error

    def _change_file_settings(self, cabecera: bytes, cuerpo: bytes):
        archivo = self._archivo(cabecera[0])
        if len(cuerpo) < 3:
            raise _Error(a.LENGTH_ERROR)
        derechos = int.from_bytes(cuerpo[1:3], "little")
        condiciones = [(derechos >> d) & 0xF for d in (12, 8, 4, 0)]
        if cuerpo[0] & _OPCION_RFU or any(5 <= c < LIBRE for c in condiciones):
            raise _Error(a.PARAMETER_ERROR)  # clave inexistente (DS Tabla 71)
        sdm = None
        if cuerpo[0] & _OPCION_SDM:
            sdm = self._leer_sdm(archivo, cuerpo[3:])
        elif len(cuerpo) != 3:
            raise _Error(a.LENGTH_ERROR)
        archivo.opcion = cuerpo[0]
        archivo.derechos = derechos
        if sdm is not None and archivo.sdm is None:
            self.contador_sdm = 0  # al activar SDM el contador vuelve a 0 (DS §9.3.1, p. 35)
        archivo.sdm = sdm
        self._espejo = None
        return b"", False

    # --- ChangeKey (DS §10.6.1, p. 62) --------------------------------------------------

    def _prep_change_key(self, cabecera: bytes) -> int:
        if self.sesion is None or self.clave_sesion != 0:
            raise _Error(a.AUTHENTICATION_ERROR)
        numero = cabecera[0] & 0x3F
        if cabecera[0] & 0xC0 or numero >= _NUM_CLAVES:
            raise _Error(a.NO_SUCH_KEY)
        return COMM_FULL

    def _change_key(self, cabecera: bytes, cuerpo: bytes):
        numero = cabecera[0]
        if numero == 0:
            if len(cuerpo) != 17:
                raise _Error(a.LENGTH_ERROR)
            nueva = cuerpo[:16]
        else:
            if len(cuerpo) != 21:
                raise _Error(a.LENGTH_ERROR)
            nueva = bytes(x ^ y for x, y in zip(cuerpo[:16], self.claves[numero]))
            if cuerpo[17:21] != ev2.crc32nk(nueva):
                raise _Error(a.INTEGRITY_ERROR)
        claves = list(self.claves)
        claves[numero] = nueva
        self.claves = claves
        versiones = list(self.versiones)
        versiones[numero] = cuerpo[16]
        self.versiones = versiones
        return b"", numero == self.clave_sesion
