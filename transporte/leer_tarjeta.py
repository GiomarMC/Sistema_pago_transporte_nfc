#!/usr/bin/env python3
"""
Lee una tarjeta emitida, verifica sus firmas y la compara con la base de datos.

    python3 leer_tarjeta.py
"""

import sys

import api_cliente
from api_cliente import ErrorAPI, SinConexion
from config import TARIFAS, formato_soles
from tarjeta import claves, formato
from tarjeta.ntag215 import ErrorTarjeta, NTAG215


def main():
    t = NTAG215.esperar()
    uid_hex = t.uid.hex().upper()
    try:
        if not t.es_ntag215():
            sys.exit(f"UID {uid_hex}: no es una NTAG215")
        pwd, pack = claves.contrasena(t.uid)
        if not t.puede_leer(formato.PAG_INICIO):
            try:
                if t.autenticar(pwd) != pack:
                    raise ErrorTarjeta("PACK incorrecto")
            except ErrorTarjeta:
                sys.exit(f"UID {uid_hex}: protegida con una contraseña que no es de este sistema")
        area = t.leer(formato.PAG_INICIO, formato.PAG_FIN)
        try:
            contador = t.contador_nfc()
        except ErrorTarjeta:
            contador = None
        est = formato.decodificar(area, t.uid, claves.clave_firma(t.uid))
    except formato.ErrorFormato as e:
        sys.exit(f"UID {uid_hex}: {e}")
    except ErrorTarjeta as e:
        sys.exit(f"Error leyendo la tarjeta: {e}")
    finally:
        t.cerrar()

    cab = est.cabecera
    nombre_tarifa, precio = TARIFAS.get(cab.tarifa, (f"desconocida ({cab.tarifa})", 0))
    print(f"UID:          {uid_hex}")
    print(f"Estado:       {formato.ESTADOS.get(cab.estado, cab.estado)}")
    print(f"Tarjeta n.º:  {cab.id_tarjeta}   Cuenta: {cab.id_cuenta}")
    print(f"Tarifa:       {nombre_tarifa} ({formato_soles(precio)})")
    print(f"Emitida:      {cab.emision}")
    print(f"Contador NFC: {contador if contador is not None else 'no disponible'}")
    for i, r in enumerate(est.registros):
        marca = "<- vigente" if i == est.vigente else ""
        nombre = "AB"[i]
        if r is None:
            print(f"Registro {nombre}:   vacío o inválido")
        else:
            print(f"Registro {nombre}:   {formato_soles(r.saldo):>10}  op {r.operacion:<5} "
                  f"{r.fecha_hora.astimezone():%Y-%m-%d %H:%M:%S}  validador {r.validador:<4} "
                  f"recarga {r.recarga} {marca}")

    print()
    try:
        info = api_cliente.llamar("GET", f"/api/tarjetas/{cab.id_tarjeta}/")
    except ErrorAPI as e:
        print(f"[!] El servidor no reconoce la tarjeta: {e}" if e.estado == 404
              else f"[!] Error del servidor: {e}")
        return
    except SinConexion as e:
        print(f"[!] Sin conexión con el servidor ({e}): solo se muestran los datos de la tarjeta")
        return
    cuenta = info["cuenta"]
    print(f"Cliente:      {cuenta['nombre']} (DNI {cuenta['documento']})")
    print(f"En servidor:  tarjeta {info['estado']}, saldo de la cuenta {formato_soles(cuenta['saldo'])}")
    if info["uid"] != uid_hex:
        print("[!] El UID no coincide con el registrado: posible clon")
    if info["estado"] != "activa":
        print(f"[!] La tarjeta está '{info['estado']}' en el sistema")
    for r in info["recargas_pendientes"]:
        if r["seq"] > est.registro.recarga:
            print(f"Pendiente:    recarga n.º {r['seq']} de {formato_soles(r['monto'])} "
                  f"({r['origen']}) — llegará a la tarjeta en el próximo viaje")
    if cuenta["saldo"] != est.registro.saldo:
        print("[!] El saldo de la tarjeta no coincide con el de la cuenta "
              "(normal si hay viajes o recargas pendientes de sincronizar)")

if __name__ == "__main__":
    main()
