#!/usr/bin/env python3
"""
Personaliza una NTAG 424 DNA de fábrica para SubePe (M1-06, #40), en el orden de
docs/ntag424-personalizacion.md §13, y comprueba el resultado.

    python3 personalizar_424.py                          # tarjeta en el ACR122U
    python3 personalizar_424.py --restablecer            # la vuelve a fábrica (tarjetas de prueba)
    python3 personalizar_424.py --simulador              # con Simulador424, sin tarjeta
    python3 personalizar_424.py --simulador --restablecer

Deja la tarjeta configurada (claves, permisos y NDEF con SUN) con el área del
archivo 03 vacía: los datos del cliente los graba la emisión.

Si se corta a mitad (tarjeta retirada), basta con volver a ejecutarlo: el estado de
cada clave se mira con GetKeyVersion (00 = de fábrica, 01 = ya de SubePe) y los
demás pasos se pueden repetir.

Maestras: claves/maestras_424.json, fuera del repositorio y con permisos 600:
    {"version": 1, "k0": "<32 hex>", "k1": "...", "k2": "...", "k3": "...", "k4": "..."}
k3 es la clave común K3; las demás son las maestras AN10922 de cada ranura (§6.1).
Tienen que coincidir con las de los validadores (k1, k2) y del servidor (SUN_K3,
SUN_K4_MAESTRAS). Con --simulador, si no existe el archivo, se usan maestras
aleatorias que no se guardan.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from config import RAIZ
from tarjeta import an10922, ev2
from tarjeta.base import TAM_AREA, AutenticacionRechazada, ErrorTarjeta
from tarjeta.ntag424 import FULL, MAC, NTAG424, PLAIN

RUTA_MAESTRAS = RAIZ / "claves" / "maestras_424.json"
CERO = bytes(16)
URL = b"mi.subepe.app/t?p="
PLANTILLA_NDEF = bytes.fromhex("004AD1014655") + b"\x04" + URL + b"0" * 32 + b"&m=" + b"0" * 16  # §8.1, 76 B
OFFSET_P, OFFSET_M = 0x19, 0x3C

# ChangeFileSettings (CmdData sin el FileNo)
AJUSTES_02 = bytes.fromhex("4000E0C1FF34" "190000" "3C0000" "3C0000")  # §8.3
AJUSTES_03 = bytes.fromhex("032012")                                    # §9
FABRICA_02 = bytes.fromhex("00E0EE")                                    # DS Tabla 8, p. 12
FABRICA_03 = bytes.fromhex("033023")

# GetFileSettings esperado: FileType 00, FileOption, AccessRights, tamaño (3 B) [y SDM]
TAM_02, TAM_03 = (256).to_bytes(3, "little"), (128).to_bytes(3, "little")
ESPERADO = {2: b"\x00" + AJUSTES_02[:3] + TAM_02 + AJUSTES_02[3:], 3: b"\x00" + AJUSTES_03 + TAM_03}
DE_FABRICA = {2: b"\x00" + FABRICA_02 + TAM_02, 3: b"\x00" + FABRICA_03 + TAM_03}

_MODO = {0x00: PLAIN, 0x01: MAC, 0x03: FULL}
_SIN_CLAVE = (0xE, 0xF)


class ErrorPersonalizacion(Exception):
    """La tarjeta no quedó como se esperaba, o tiene claves que no son de estas maestras."""


class Maestras:
    """Maestras de las ranuras 0, 1, 2 y 4 y la clave común K3 (§6.1).

    Cualquier objeto con `version` y `claves(uid) -> [K0..K4]` sirve en su lugar
    (por ejemplo, uno que pida las claves al servidor)."""

    def __init__(self, k: dict[int, bytes], version: int = 1):
        if sorted(k) != [0, 1, 2, 3, 4] or any(len(v) != 16 for v in k.values()):
            raise ValueError("hacen falta k0..k4 de 16 bytes")
        if not 1 <= version <= 0xFF:
            raise ValueError("la versión va de 1 a 255 (00 es la de fábrica)")
        self._k, self.version = dict(k), version

    @classmethod
    def desde_archivo(cls, ruta: Path) -> "Maestras":
        if os.name == "posix" and ruta.stat().st_mode & 0o077:
            print(f"[!] {ruta} lo pueden leer otros usuarios: chmod 600 {ruta}")
        datos = json.loads(ruta.read_text())
        return cls({n: bytes.fromhex(datos[f"k{n}"]) for n in range(5)}, datos.get("version", 1))

    @classmethod
    def aleatorias(cls) -> "Maestras":
        return cls({n: os.urandom(16) for n in range(5)})

    def claves(self, uid: bytes) -> list[bytes]:
        return [self._k[3] if n == 3 else an10922.diversificar_aes128(self._k[n], uid, n, self.version)
                for n in range(5)]


# --- Pasos --------------------------------------------------------------------------

def _clave_actual(chip: NTAG424, numero: int, nuevas: list[bytes], version: int) -> bytes:
    """La clave que tiene hoy la tarjeta en la ranura, según su KeyVer."""
    actual = chip.version_clave(numero)
    if actual == 0:
        return CERO
    if actual == version:
        return nuevas[numero]
    raise ErrorPersonalizacion(f"K{numero} tiene la versión {actual:02X}: no es de fábrica ni de estas maestras")


def _autenticar(chip: NTAG424, numero: int, nuevas: list[bytes], version: int) -> bytes:
    chip.seleccionar_aplicacion()
    clave = _clave_actual(chip, numero, nuevas, version)
    try:
        chip.autenticar(numero, clave)
    except AutenticacionRechazada as e:
        origen = "de fábrica" if clave == CERO else "de estas maestras"
        raise ErrorPersonalizacion(f"la tarjeta no acepta la K{numero} {origen}: {e}") from e
    return clave


def _escritura(ajustes: bytes) -> tuple[int, str]:
    """(clave que escribe, modo) según GetFileSettings: ReadWrite o, si no, Write."""
    lectura_escritura, escritura = ajustes[2] >> 4, ajustes[3] & 0xF
    clave = lectura_escritura if lectura_escritura not in _SIN_CLAVE else escritura
    if clave in _SIN_CLAVE:
        raise ErrorPersonalizacion("el archivo 03 no tiene clave de escritura")
    return clave, _MODO[ajustes[1] & 0x03]


def personalizar(chip: NTAG424, maestras, salida=print) -> int:
    """§13 pasos 1-6 y 8. Devuelve el SDMReadCtr de la lectura de comprobación."""
    nuevas, version = maestras.claves(chip.uid), maestras.version
    k0 = _autenticar(chip, 0, nuevas, version)
    salida(f"  1-2. Autenticada con la K0 {'de fábrica' if k0 == CERO else 'de SubePe (se retoma)'}")
    chip.escribir_datos(2, 0, PLANTILLA_NDEF, PLAIN)
    salida("  3.   NDEF con la plantilla del SUN en el archivo 02")
    chip.cambiar_ajustes(3, AJUSTES_03)
    salida("  4.   Archivo 03: lectura K1, escritura K2, Full")
    chip.cambiar_ajustes(2, AJUSTES_02)
    salida("  5.   Archivo 02: SDM con PICCData (K3) y SDMMAC (K4)")
    for numero in (1, 2, 3, 4):
        if _clave_actual(chip, numero, nuevas, version) == CERO:
            chip.cambiar_clave(numero, nuevas[numero], CERO, version)
    if k0 == CERO:
        chip.cambiar_clave(0, nuevas[0], CERO, version)
    salida(f"  6.   Claves K1-K4 y K0 cambiadas (KeyVer {version:02X})")
    contador = verificar(chip, maestras)
    salida(f"  8.   Comprobado: ajustes, claves y SUN (SDMReadCtr {contador})")
    return contador


def verificar(chip: NTAG424, maestras) -> int:
    """§13 paso 8, cada parte en una sesión nueva."""
    nuevas, version = maestras.claves(chip.uid), maestras.version
    if _autenticar(chip, 0, nuevas, version) == CERO:
        raise ErrorPersonalizacion("K0 sigue siendo la de fábrica")
    for numero in range(5):
        if chip.version_clave(numero) != version:
            raise ErrorPersonalizacion(f"K{numero} no tiene la versión {version:02X}")
    for archivo, esperado in ESPERADO.items():
        if chip.ajustes_archivo(archivo) != esperado:
            raise ErrorPersonalizacion(f"los ajustes del archivo {archivo:02d} no son los de la especificación")
    chip.seleccionar_aplicacion()
    chip.autenticar(1, nuevas[1])
    chip.leer_datos(3, 0, TAM_AREA)
    chip.autenticar(2, nuevas[2])
    contador = comprobar_sun(chip.leer_ndef(), chip.uid, nuevas[3], nuevas[4])
    chip.seleccionar_aplicacion()
    return contador


def comprobar_sun(ndef: bytes, uid: bytes, k3: bytes, k4: bytes) -> int:
    """Comprueba el enlace SUN como el servidor (AN12196 §3.4.2 y §3.4.4.2.1) y devuelve SDMReadCtr."""
    if ndef[:OFFSET_P] != PLANTILLA_NDEF[:OFFSET_P]:
        raise ErrorPersonalizacion("el NDEF no tiene la plantilla del SUN")
    try:
        p = bytes.fromhex(ndef[OFFSET_P:OFFSET_P + 32].decode())
        m = bytes.fromhex(ndef[OFFSET_M:OFFSET_M + 16].decode())
    except ValueError as e:
        raise ErrorPersonalizacion("la tarjeta no rellenó p y m") from e
    claro = ev2.aes_cbc_descifrar(k3, ev2.IV_CERO, p)
    if claro[0] != 0xC7 or claro[1:8] != uid:
        raise ErrorPersonalizacion("PICCData no se descifra con K3 o no lleva este UID")
    contador = claro[8:11]
    sesion = ev2.cmac(k4, bytes.fromhex("3CC300010080") + uid + contador)
    if not ev2.mac_igual(m, ev2.mac_truncado(sesion, b"")):
        raise ErrorPersonalizacion("el SDMMAC no coincide con K4")
    return int.from_bytes(contador, "little")


def restablecer(chip: NTAG424, maestras, salida=print) -> None:
    """Vuelve a la configuración de fábrica: área y NDEF a cero, ajustes de DS Tabla 8 y claves a cero."""
    nuevas, version = maestras.claves(chip.uid), maestras.version
    chip.seleccionar_aplicacion()
    clave, modo = _escritura(chip.ajustes_archivo(3))
    _autenticar(chip, clave, nuevas, version)
    chip.escribir_datos(3, 0, bytes(TAM_AREA), modo)
    salida(f"  1. Área del archivo 03 borrada (con K{clave})")
    k0 = _autenticar(chip, 0, nuevas, version)
    chip.escribir_datos(2, 0, bytes(len(PLANTILLA_NDEF)), PLAIN)
    salida("  2. NDEF del archivo 02 borrado")
    chip.cambiar_ajustes(3, FABRICA_03)
    chip.cambiar_ajustes(2, FABRICA_02)
    salida("  3. Ajustes de fábrica en los archivos 02 y 03 (sin SDM)")
    for numero in (1, 2, 3, 4):
        anterior = _clave_actual(chip, numero, nuevas, version)
        if anterior != CERO:
            chip.cambiar_clave(numero, CERO, anterior, 0)
    if k0 != CERO:
        chip.cambiar_clave(0, CERO, k0, 0)
    salida("  4. Claves K1-K4 y K0 a cero (KeyVer 00)")
    verificar_fabrica(chip)
    salida("  5. Comprobado: como de fábrica")


def verificar_fabrica(chip: NTAG424) -> None:
    chip.seleccionar_aplicacion()
    for numero in range(5):
        if chip.version_clave(numero) != 0:
            raise ErrorPersonalizacion(f"K{numero} no volvió a la versión 00")
    for archivo, esperado in DE_FABRICA.items():
        if chip.ajustes_archivo(archivo) != esperado:
            raise ErrorPersonalizacion(f"los ajustes del archivo {archivo:02d} no son los de fábrica")
    chip.autenticar(0, CERO)
    chip.autenticar(2, CERO)  # de fábrica, el 03 se lee con K2
    if chip.leer_datos(3, 0, TAM_AREA) != bytes(TAM_AREA):
        raise ErrorPersonalizacion("el área del archivo 03 no quedó vacía")
    if chip.leer_ndef()[:len(PLANTILLA_NDEF)] != bytes(len(PLANTILLA_NDEF)):
        raise ErrorPersonalizacion("el NDEF del archivo 02 no quedó vacío")
    chip.seleccionar_aplicacion()


# --- Programa -----------------------------------------------------------------------

def _cargar_maestras(ruta: Path, simulador: bool):
    if ruta.exists():
        return Maestras.desde_archivo(ruta)
    if simulador:
        print("Sin archivo de maestras: se usan maestras aleatorias, solo para esta ejecución.")
        return Maestras.aleatorias()
    sys.exit(f"Falta {ruta}. Formato: {{\"version\": 1, \"k0\": \"<32 hex>\", ..., \"k4\": \"<32 hex>\"}} "
             "(ver la ayuda del programa). Sin él no se puede personalizar.")


def _ejecutar(chip: NTAG424, maestras, restablecer_tarjeta: bool) -> None:
    uid = chip.uid.hex().upper()
    if restablecer_tarjeta:
        print(f"Restableciendo la tarjeta {uid}...")
        restablecer(chip, maestras)
        print("Tarjeta restablecida a la configuración de fábrica.")
    else:
        print(f"Personalizando la tarjeta {uid}...")
        personalizar(chip, maestras)
        print("Tarjeta personalizada. El área del archivo 03 queda vacía hasta la emisión.")


def main():
    p = argparse.ArgumentParser(description="Personaliza (o restablece) una NTAG 424 DNA para SubePe")
    p.add_argument("--restablecer", action="store_true", help="vuelve a fábrica una tarjeta de prueba")
    p.add_argument("--simulador", action="store_true", help="usa Simulador424 en lugar del ACR122U")
    p.add_argument("--maestras", type=Path, default=RUTA_MAESTRAS, help=f"por omisión {RUTA_MAESTRAS}")
    args = p.parse_args()
    maestras = _cargar_maestras(args.maestras, args.simulador)
    try:
        if args.simulador:
            from tarjeta.simulador424 import Simulador424
            sim = Simulador424(uid=b"\x04" + os.urandom(6))
            chip = NTAG424(sim.transmitir, sim.uid)
            if args.restablecer:  # el simulador empieza de fábrica: primero se personaliza
                _ejecutar(chip, maestras, False)
            _ejecutar(chip, maestras, args.restablecer)
            return
        from tarjeta.lector import esperar_conexion
        from tarjeta.ntag424 import chip_pcsc
        chip = chip_pcsc(esperar_conexion())
        try:
            _ejecutar(chip, maestras, args.restablecer)
        finally:
            chip.cerrar()
    except ErrorPersonalizacion as e:
        sys.exit(f"Error: {e}")
    except ErrorTarjeta as e:
        sys.exit(f"Se cortó la comunicación con la tarjeta ({e}). Vuelva a acercarla y ejecute el programa otra vez: "
                 "retoma donde quedó.")


if __name__ == "__main__":
    main()
