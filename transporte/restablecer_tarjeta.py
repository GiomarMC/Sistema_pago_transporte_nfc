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
from tarjeta.base import ErrorTarjeta, TipoNoSoportado, esperar_tarjeta


def main():
    p = argparse.ArgumentParser(description="Restablece una tarjeta de pruebas")
    p.add_argument("--si", action="store_true", help="no pedir confirmación")
    args = p.parse_args()

    try:
        t = esperar_tarjeta()
    except TipoNoSoportado as e:
        print(f"Tarjeta detectada: UID {e.uid.hex().upper()}")
        sys.exit(f"[X] No se pudo restablecer: {e}")
    uid_hex = t.uid.hex().upper()
    print(f"Tarjeta detectada: UID {uid_hex}")
    if not args.si and input("¿Borrar los datos del sistema de esta tarjeta? (s/N) ").lower() != "s":
        t.cerrar()
        sys.exit("Cancelado")
    try:
        if t.protegida():
            try:
                t.autenticar()
            except ErrorTarjeta:
                raise ErrorTarjeta("la tarjeta tiene una contraseña que no es de este sistema")
        # Lanza ErrorTarjeta sin escribir nada si la configuración está bloqueada
        t.restablecer()

        t.reiniciar_sesion()
        if t.protegida():
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
