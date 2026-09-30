#!/usr/bin/env python3
"""
Devuelve una tarjeta de pruebas a su estado de fábrica: borra el área del
sistema, quita la contraseña y desactiva la protección. El saldo no se pierde
(está en la cuenta); la tarjeta queda anulada en el servidor.

    python3 restablecer_tarjeta.py [--si]

El contador NFC no se puede poner a cero: es así por diseño del chip.
"""

import argparse
import sys

import api_cliente
from api_cliente import ErrorAPI, SinConexion
from tarjeta import claves, formato
from tarjeta.ntag215 import (CFGLCK, NFC_CNT_EN, NFC_CNT_PWD_PROT, PAG_CFG0, PAG_CFG1,
                             PAG_PACK, PAG_PWD, PROT, ErrorTarjeta, NTAG215)


def main():
    p = argparse.ArgumentParser(description="Restablece una tarjeta de pruebas")
    p.add_argument("--si", action="store_true", help="no pedir confirmación")
    args = p.parse_args()

    t = NTAG215.esperar()
    uid_hex = t.uid.hex().upper()
    print(f"Tarjeta detectada: UID {uid_hex}")
    if not args.si and input("¿Borrar los datos del sistema de esta tarjeta? (s/N) ").lower() != "s":
        sys.exit("Cancelado")
    try:
        if not t.es_ntag215():
            raise ErrorTarjeta("no es una NTAG215")
        pwd, pack = claves.contrasena(t.uid)
        if not t.puede_leer(formato.PAG_INICIO):
            try:
                if t.autenticar(pwd) != pack:
                    raise ErrorTarjeta("PACK incorrecto")
            except ErrorTarjeta:
                raise ErrorTarjeta("la tarjeta tiene una contraseña que no es de este sistema")
        cfg = t.leer(PAG_CFG0, PAG_CFG1)
        if cfg[4] & CFGLCK:
            raise ErrorTarjeta("la configuración está bloqueada de forma permanente")

        t.escribir_paginas(formato.PAG_INICIO, bytes(formato.TAM_AREA))
        t.escribir(PAG_CFG0, cfg[0:3] + b"\xFF")  # AUTH0 = FF: sin protección
        access = cfg[4] & ~(PROT | NFC_CNT_EN | NFC_CNT_PWD_PROT | 0x07)
        t.escribir(PAG_CFG1, bytes([access]) + cfg[5:8])
        t.escribir(PAG_PWD, b"\xFF\xFF\xFF\xFF")  # valores de fábrica
        t.escribir(PAG_PACK, b"\x00\x00\x00\x00")

        t.reactivar()
        if not t.puede_leer(formato.PAG_INICIO):
            raise ErrorTarjeta("verificación: la tarjeta sigue protegida")
    except ErrorTarjeta as e:
        sys.exit(f"[X] No se pudo restablecer: {e}")
    finally:
        t.cerrar()

    print("[OK] Tarjeta restablecida a valores de fábrica")
    try:
        r = api_cliente.llamar("POST", f"/api/tarjetas/uid/{uid_hex}/anular/")
        print(f"  Anulada en el servidor ({r['anuladas']} emisión/es)")
    except (ErrorAPI, SinConexion) as e:
        print(f"[!] No se pudo anular en el servidor ({e}). Hazlo luego desde el panel de "
              "administración: queda activa allí aunque la tarjeta ya no tiene datos.")


if __name__ == "__main__":
    main()
