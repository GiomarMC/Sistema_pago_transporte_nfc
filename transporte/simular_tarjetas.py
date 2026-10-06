"""
Tarjetas virtuales para probar el validador de la ESP32 sin tarjetas físicas.

Se usa desde el puente:  python3 puente_nfc.py --simular [--intervalo 5]

El puente deja de usar el ACR122U y contesta a la ESP32 como si tuviera delante
una NTAG215 de verdad: PWD_AUTH, FAST_READ, READ_CNT y escritura de páginas. Las
tarjetas se emiten con la clave maestra real, así que la ESP32 ejecuta su lógica
de cobro completa y muestra en pantalla exactamente lo que mostraría con una
tarjeta física. Cada prueba se presenta unos segundos después de la anterior.

Los cobros simulados SÍ se guardan en la microSD (eventos.txt). Las tarjetas
usan números a partir de 900000000 para distinguirlas de las reales.
"""

import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Callable

from puente_nfc import Puente, hora
from tarjeta import claves, formato
from tarjeta.ntag215 import VERSION_NTAG215, ErrorTarjeta

PWD_FABRICA = b"\xff\xff\xff\xff"


class TarjetaVirtual:
    """NTAG215 en memoria con la misma interfaz que usa el puente (uid, comando,
    escribir, cerrar)."""

    def __init__(self, uid, area=b"", pwd=PWD_FABRICA, pack=b"\x00\x00", protegida=True):
        self.uid = uid
        self.paginas = bytearray(135 * 4)
        self.paginas[formato.PAG_INICIO * 4:formato.PAG_INICIO * 4 + len(area)] = area
        self.pwd = pwd            # None: acepta cualquier contraseña (tarjeta de otro sistema)
        self.pack = pack
        self.protegida = protegida
        self.autenticada = False
        self.contador = 0
        self.cortar_lectura = False
        self.cortar_escritura_tras = None  # n.º de páginas que se escriben antes de "retirarla"

    def copia(self):
        t = TarjetaVirtual(self.uid, pwd=self.pwd, pack=self.pack, protegida=self.protegida)
        t.paginas[:] = self.paginas
        t.contador = self.contador
        return t

    def _exigir_acceso(self, pagina, cmd):
        if self.protegida and pagina >= formato.PAG_INICIO and not self.autenticada:
            raise ErrorTarjeta(f"la tarjeta rechazó el comando {cmd:02X}")

    def comando(self, cmd: bytes) -> bytes:
        op = cmd[0]
        if op == 0x1B:  # PWD_AUTH
            if self.pwd is not None and cmd[1:5] != self.pwd:
                self.autenticada = False
                raise ErrorTarjeta("la tarjeta rechazó el comando 1B")
            self.autenticada = True
            return self.pack
        if op == 0x3A:  # FAST_READ
            desde, hasta = cmd[1], cmd[2]
            self._exigir_acceso(hasta, op)
            if self.cortar_lectura:
                self.cortar_lectura = False
                raise ErrorTarjeta("la tarjeta se retiró del lector")
            return bytes(self.paginas[desde * 4:(hasta + 1) * 4])
        if op == 0x30:  # READ (4 páginas)
            self._exigir_acceso(cmd[1] + 3, op)
            return bytes(self.paginas[cmd[1] * 4:(cmd[1] + 4) * 4])
        if op == 0x39:  # READ_CNT
            self._exigir_acceso(formato.PAG_INICIO, op)
            return self.contador.to_bytes(3, "little")
        if op == 0x60:  # GET_VERSION
            return VERSION_NTAG215
        raise ErrorTarjeta(f"la tarjeta rechazó el comando {op:02X}")

    def escribir(self, pagina: int, datos: bytes):
        self._exigir_acceso(pagina, 0xA2)
        if self.cortar_escritura_tras is not None:
            if self.cortar_escritura_tras == 0:
                self.cortar_escritura_tras = None
                raise ErrorTarjeta("la tarjeta se retiró del lector")
            self.cortar_escritura_tras -= 1
        self.paginas[pagina * 4:pagina * 4 + 4] = datos

    def cerrar(self):
        self.autenticada = False


def emitir(id_tarjeta, tarifa, saldo, estado=formato.ACTIVA):
    """Tarjeta emitida y protegida igual que con emitir_tarjeta.py."""
    uid = b"\x04" + os.urandom(6)
    cab = formato.Cabecera(id_tarjeta, id_tarjeta, tarifa, date.today(), estado)
    area = formato.codificar_emision(cab, saldo, uid, claves.clave_firma(uid))
    pwd, pack = claves.contrasena(uid)
    return TarjetaVirtual(uid, area, pwd, pack)


@dataclass
class Prueba:
    nombre: str
    esperado: str
    tarjeta: Callable[[], TarjetaVirtual]


def crear_pruebas():
    # Números nuevos en cada ejecución: la ESP32 recuerda en la microSD el último
    # n.º de operación de cada tarjeta y tomaría una repetición por una copia antigua
    base = 900_000_000 + (int(time.time()) % 1_000_000) * 100
    general = emitir(base + 1, 1, 1000)
    original_general = general.copia()  # copia de antes de cobrar, para la prueba de restauración
    estudiante = emitir(base + 2, 2, 500)

    alterada = emitir(base + 6, 1, 1000)
    off = (formato.PAG_REGISTROS[0] - formato.PAG_INICIO) * 4
    inicio = formato.PAG_INICIO * 4 + off
    alterada.paginas[inicio:inicio + 4] = (99999).to_bytes(4, "big", signed=True)  # saldo editado a mano

    corte_lectura = emitir(base + 8, 1, 1000)
    corte_lectura.cortar_lectura = True
    corte_escritura = emitir(base + 9, 1, 300)

    def cortar_escritura():
        corte_escritura.cortar_escritura_tras = 2  # graba 2 de las 5 páginas del registro
        return corte_escritura

    return [
        Prueba("Tarjeta general con S/ 10.00",
               "VERDE  PASE | General S/ 1.30 | Saldo S/ 8.70", lambda: general),
        Prueba("La misma tarjeta otra vez (antes de 60 s)",
               "VERDE  Ya pagado | Saldo S/ 8.70 (no cobra otra vez)", lambda: general),
        Prueba("Tarjeta de estudiante con S/ 5.00",
               "VERDE  PASE | Estudiante S/ 0.80 | Saldo S/ 4.20", lambda: estudiante),
        Prueba("Tarjeta general con S/ 0.50",
               "ROJO  Saldo insuficiente | Saldo S/ 0.50", lambda: emitir(base + 3, 1, 50)),
        Prueba("Tarjeta bloqueada",
               "ROJO  Tarjeta bloqueada | Acuda a atencion",
               lambda: emitir(base + 4, 1, 1000, formato.BLOQUEADA)),
        Prueba("Tarjeta NTAG215 sin emitir (de fábrica)",
               "ROJO  Tarjeta no valida | No emitida por el sistema",
               lambda: TarjetaVirtual(b"\x04" + os.urandom(6), protegida=False)),
        Prueba("Tarjeta de otro sistema (otra contraseña)",
               "ROJO  Tarjeta no valida | No reconocida",
               lambda: TarjetaVirtual(b"\x04" + os.urandom(6), pwd=None, pack=b"\x12\x34")),
        Prueba("Saldo editado a mano (firma inválida)",
               "ROJO  Tarjeta no valida | Datos alterados", lambda: alterada),
        Prueba("Copia antigua de la tarjeta general (saldo restaurado a S/ 10.00)",
               "ROJO  Tarjeta no valida | Acuda a atencion", lambda: original_general),
        Prueba("Tarifa que el validador no conoce (código 9)",
               "ROJO  Tarifa desconocida | Codigo 9", lambda: emitir(base + 7, 9, 1000)),
        Prueba("Tarjeta retirada durante la lectura",
               "AMBAR  Acerque de nuevo | Lectura fallida", lambda: corte_lectura),
        Prueba("Tarjeta con S/ 3.00 retirada a mitad de la escritura",
               "AMBAR  Acerque de nuevo | Lectura interrumpida (no cobra)", cortar_escritura),
        Prueba("La misma tarjeta acercada de nuevo",
               "VERDE  PASE | General S/ 1.30 | Saldo S/ 1.70 (cobra una sola vez)",
               lambda: corte_escritura),
    ]


class PuenteSimulado(Puente):
    def __init__(self, ser, config=None, detalle=False, intervalo=5):
        super().__init__(ser, config, detalle)
        self.intervalo = intervalo
        self.pruebas = crear_pruebas()
        self.indice = 0
        self.proxima = time.monotonic() + intervalo
        print(f"MODO SIMULACIÓN: {len(self.pruebas)} pruebas, una cada {intervalo} s. "
              "No se usa el lector ACR122U.")

    def orden_espera(self, ms):
        self.cerrar_sesion()
        restante = self.proxima - time.monotonic()
        if self.indice >= len(self.pruebas) or restante > 0:
            time.sleep(min(int(ms) / 1000, max(restante, 0.05)))
            self.responder("NADA")
            return
        prueba = self.pruebas[self.indice]
        self.indice += 1
        tarjeta = prueba.tarjeta()
        tarjeta.contador += 1  # el contador NFC sube en cada lectura
        self.sesion = tarjeta
        print(f"\n{hora()}  PRUEBA {self.indice}/{len(self.pruebas)}: {prueba.nombre}")
        print(f"          Esperado en pantalla: {prueba.esperado}")
        self.responder("OK " + tarjeta.uid.hex().upper())

    def orden_retirada(self, ms):
        # La tarjeta virtual se "retira" en cuanto termina el cobro
        self.proxima = time.monotonic() + self.intervalo
        if self.indice == len(self.pruebas):
            print(f"\n{hora()}  Simulación terminada. Ctrl+C para salir.")
            self.indice += 1
        self.responder("OK")
