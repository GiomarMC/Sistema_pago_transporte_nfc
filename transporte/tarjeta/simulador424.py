"""
NTAG 424 DNA simulada en memoria, para probar sin tarjetas (M1-03, parte a).

`Simulador424.transmitir(apdu) -> respuesta` se comporta como la tarjeta detrás
de un lector PC/SC: recibe la APDU completa y devuelve datos + SW.

Implementado (parte a): ISOSelectFile por nombre de DF, GetVersion (sin
autenticar), AuthenticateEV2First, ReadData y WriteData en Plain/MAC/Full,
GetFileSettings, ChangeFileSettings sin SDM y ChangeKey. Reglas de acceso de
DS §8.2.3.3-§8.2.3.5 (pp. 11-13) y códigos de error de DS §10.3 (p. 45) y de
cada comando.

No implementado todavía (M1-03b, depende de la decisión SDMMACInput): SDM/SUN,
GetFileCounters e ISOReadBinary; lanzan NotImplementedError para que ninguna
prueba dependa de un comportamiento inventado. Tampoco AuthenticateEV2NonFirst,
LRP ni el contador de autenticaciones fallidas (decisión F).

Estado de fábrica: DS Tabla 5 (p. 10), Tabla 8 (p. 12) y Tabla 13 (p. 14);
claves a cero (AN12196 Tabla 14).
"""

import os
from dataclasses import dataclass
from typing import Callable

from tarjeta import apdu424 as a
from tarjeta import ev2

LIBRE, SIN_ACCESO = 0xE, 0xF
COMM_PLAIN, COMM_MAC, COMM_FULL = 0b00, 0b01, 0b11
_OPCION_SDM = 0x40
_OPCION_RFU = 0xBC
_NUM_CLAVES = 5
_TAM_CABECERA_DATOS = 7  # FileNo, Offset(3), Length(3)
_VERSION_FIJA = (bytes.fromhex("04040830001105"), bytes.fromhex("04040201011105"))
_LOTE_Y_FECHA = bytes.fromhex("CD65935D402118")  # AN12196 Tabla 12, solo de muestra


@dataclass
class Archivo:
    opcion: int        # FileOption: bit 6 SDM, bits 1-0 CommMode
    derechos: int      # Read<<12 | Write<<8 | ReadWrite<<4 | Change (DS Tabla 7)
    datos: bytearray

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

    # --- Ciclo de vida ------------------------------------------------------------

    def acercar(self) -> None:
        """La tarjeta vuelve al campo: se reinicia (sin selección ni autenticación)."""
        self.presente = True
        self.aplicacion = False
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
        if apdu[1] != a.INS_SELECT:
            return a.SW_ISO_INS_NO_SOPORTADA.to_bytes(2, "big")
        if apdu[2] != 0x04:
            raise NotImplementedError("selección por FileID e ISOReadBinary: M1-03b")
        nombre = apdu[5:5 + apdu[4]] if len(apdu) > 5 else b""
        self._perder_autenticacion()
        if nombre == a.DF_APLICACION_NDEF:
            self.aplicacion = True
        elif nombre == a.DF_PICC:
            self.aplicacion = False
        else:
            return a.SW_ISO_NO_ENCONTRADO.to_bytes(2, "big")
        return a.SW_ISO_OK.to_bytes(2, "big")

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
        if ins == a.INS_GET_FILE_COUNTERS:
            raise NotImplementedError("GetFileCounters (SDMReadCtr): M1-03b")
        comandos = {
            a.INS_GET_FILE_SETTINGS: (1, self._prep_get_file_settings, self._get_file_settings),
            a.INS_READ_DATA: (_TAM_CABECERA_DATOS, self._prep_read_data, self._read_data),
            a.INS_WRITE_DATA: (_TAM_CABECERA_DATOS, self._prep_write_data, self._write_data),
            a.INS_CHANGE_FILE_SETTINGS: (1, self._prep_change_file_settings,
                                         self._change_file_settings),
            a.INS_CHANGE_KEY: (1, self._prep_change_key, self._change_key),
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
        return bytes([0x00, archivo.opcion]) + derechos_en_cable(archivo.derechos) + tamano, False

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
        return bytes(archivo.datos[offset:offset + longitud]), False

    def _prep_write_data(self, cabecera: bytes) -> int:
        archivo = self._archivo(cabecera[0])
        return self._modo_acceso(archivo, archivo.escritura)

    def _write_data(self, cabecera: bytes, cuerpo: bytes):
        archivo = self._archivo(cabecera[0])
        offset, longitud = self._rango(cabecera, archivo, escritura=True)
        if len(cuerpo) != longitud:
            raise _Error(a.LENGTH_ERROR)
        archivo.datos[offset:offset + longitud] = cuerpo
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
        if cuerpo and cuerpo[0] & _OPCION_SDM:
            raise NotImplementedError("ChangeFileSettings con SDM: M1-03b")
        if len(cuerpo) != 3:
            raise _Error(a.LENGTH_ERROR)
        derechos = int.from_bytes(cuerpo[1:3], "little")
        condiciones = [(derechos >> d) & 0xF for d in (12, 8, 4, 0)]
        if cuerpo[0] & _OPCION_RFU or any(5 <= c < LIBRE for c in condiciones):
            raise _Error(a.PARAMETER_ERROR)  # clave inexistente (DS Tabla 71)
        archivo.opcion = cuerpo[0]
        archivo.derechos = derechos
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
