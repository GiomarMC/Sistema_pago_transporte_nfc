#!/usr/bin/env python3
"""
Recarga de saldo. El servidor abona el monto a la cuenta al instante (es la
fuente de verdad); la tarjeta lo recibe cuando pasa por un lector.

  Remota (Yape, web...), sin tarjeta: llega a la tarjeta en su próximo viaje
    python3 recargar.py --dni 71988729 --monto 5.00 --origen yape

  En punto de recarga, con la tarjeta en el lector: se graba al momento
    python3 recargar.py --monto 5.00

  Solo grabar en la tarjeta las recargas pendientes (p. ej. si se retiró antes de tiempo)
    python3 recargar.py --aplicar
"""

import argparse
import sys
from datetime import datetime, timezone

import api_cliente
from api_cliente import ErrorAPI, SinConexion
from config import PUNTO_RECARGA, formato_soles, parsear_soles
from tarjeta import claves, formato
from tarjeta.ntag215 import ErrorTarjeta, NTAG215


class ErrorRecarga(Exception):
    pass


def recargar_tarjeta(t: NTAG215, monto: int):
    """Recarga en punto con la tarjeta ya conectada. Pide al servidor la recarga
    (y las remotas pendientes), graba el nuevo saldo y confirma la entrega.
    Devuelve (saldo anterior, saldo nuevo, abonado, datos de la cuenta)."""
    pwd, pack = claves.contrasena(t.uid)
    try:
        if t.autenticar(pwd) != pack:
            raise ErrorTarjeta("PACK")
    except ErrorTarjeta:
        raise ErrorRecarga("tarjeta no válida o no emitida por este sistema")
    clave = claves.clave_firma(t.uid)
    try:
        est = formato.decodificar(t.leer(formato.PAG_INICIO, formato.PAG_FIN), t.uid, clave)
    except formato.ErrorFormato as e:
        raise ErrorRecarga(str(e))
    contador = t.contador_nfc()
    cab, reg = est.cabecera, est.registro
    if cab.estado != formato.ACTIVA:
        raise ErrorRecarga("la tarjeta está bloqueada: no se recarga")

    # El servidor registra la recarga antes de tocar la tarjeta (el dinero ya se
    # cobró). Si la grabación falla, la recarga no se pierde: queda pendiente.
    try:
        resp = api_cliente.llamar("POST", "/api/recargas/", {
            "tarjeta": cab.id_tarjeta, "uid": t.uid.hex().upper(), "monto": monto,
            "recarga_en_tarjeta": reg.recarga})
    except ErrorAPI as e:
        raise ErrorRecarga(str(e))
    abono, hasta = resp["abono"], resp["hasta_seq"]
    if not abono:
        return reg.saldo, reg.saldo, 0, resp["cuenta"]

    nuevo = formato.Registro(reg.saldo + abono, reg.operacion + 1,
                             datetime.now(timezone.utc).replace(microsecond=0),
                             PUNTO_RECARGA, hasta)
    pagina = formato.PAG_REGISTROS[est.ranura_libre]
    datos = nuevo.a_bytes(t.uid, clave, cab)
    try:
        t.escribir_paginas(pagina, datos)
        if t.leer(pagina, pagina + len(datos) // 4 - 1) != datos:
            raise ErrorTarjeta("verificación de escritura")
    except ErrorTarjeta:
        raise ErrorRecarga("la tarjeta se retiró antes de terminar. La recarga ya está abonada "
                           "en la cuenta: se grabará en el próximo viaje o con  recargar.py --aplicar")

    try:
        api_cliente.llamar("POST", "/api/recargas/entregas/", {
            "tarjeta": cab.id_tarjeta, "hasta_seq": hasta, "operacion": nuevo.operacion,
            "contador": contador, "recarga": resp["recarga"]["id"] if resp["recarga"] else None})
    except (ErrorAPI, SinConexion) as e:
        # La tarjeta ya tiene el saldo; el servidor lo sabrá con el próximo viaje
        print(f"[!] No se pudo confirmar la entrega al servidor ({e}); se confirmará sola "
              "en el próximo viaje.")
    return reg.saldo, nuevo.saldo, abono, resp["cuenta"]


def recarga_remota(dni, monto, origen):
    try:
        resp = api_cliente.llamar("POST", "/api/recargas/",
                                  {"documento": dni, "monto": monto, "origen": origen})
    except ErrorAPI as e:
        sys.exit(f"[X] Recarga rechazada: {e}")
    cuenta, rec = resp["cuenta"], resp["recarga"]
    print(f"[OK] Recarga n.º {rec['seq']} de {formato_soles(monto)} ({origen}) para "
          f"{cuenta['nombre']}")
    print(f"  Saldo de la cuenta: {formato_soles(cuenta['saldo'])}")
    if resp["tiene_tarjeta_activa"]:
        print("  Llegará a la tarjeta en su próximo viaje (los buses la reciben al sincronizar).")
    else:
        print("  [!] El cliente no tiene tarjeta activa: se cargará en la próxima que se le emita.")


def main():
    p = argparse.ArgumentParser(description="Recarga de saldo")
    p.add_argument("--monto", help="monto en soles, p. ej. 5.00")
    p.add_argument("--dni", help="recarga remota (sin tarjeta) para este cliente")
    p.add_argument("--origen", default="yape", help="origen de la recarga remota (yape, web...)")
    p.add_argument("--aplicar", action="store_true",
                   help="solo grabar en la tarjeta las recargas pendientes")
    args = p.parse_args()

    if args.aplicar and (args.monto or args.dni):
        p.error("--aplicar no lleva monto ni DNI")
    monto = 0
    if not args.aplicar:
        if not args.monto:
            p.error("indica --monto (o --aplicar)")
        try:
            monto = parsear_soles(args.monto)
        except ValueError as e:
            p.error(str(e))

    try:
        if args.dni:
            recarga_remota(args.dni, monto, args.origen)
            return
        with NTAG215.esperar() as t:
            antes, despues, abono, cuenta = recargar_tarjeta(t, monto)
    except SinConexion as e:
        sys.exit(f"[X] Sin conexión con el servidor ({e}): no se puede recargar ahora")
    except (ErrorRecarga, ErrorTarjeta) as e:
        sys.exit(f"[X] {e}")

    if not abono:
        print(f"No hay recargas pendientes. Saldo en tarjeta: {formato_soles(antes)}")
        return
    print(f"[OK] Recarga grabada en la tarjeta de {cuenta['nombre']}")
    if monto and abono != monto:
        print(f"  Incluye recargas remotas pendientes: {formato_soles(abono - monto)}")
    print(f"  Abonado:            {formato_soles(abono)}")
    print(f"  Saldo en tarjeta:   {formato_soles(antes)} -> {formato_soles(despues)}")
    print(f"  Saldo de la cuenta: {formato_soles(cuenta['saldo'])}")


if __name__ == "__main__":
    main()
