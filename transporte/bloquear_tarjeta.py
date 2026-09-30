#!/usr/bin/env python3
"""
Bloquea tarjetas por robo o pérdida (en el servidor). Los validadores lo
recibirán en su próxima sincronización. El saldo sigue en la cuenta: se
recupera emitiendo una tarjeta nueva con  emitir_tarjeta.py --cuenta N

    python3 bloquear_tarjeta.py --tarjeta 2
    python3 bloquear_tarjeta.py --dni 71988729      # todas las tarjetas activas del cliente

También se puede hacer desde el panel de administración (Tarjetas -> acción "Bloquear").
"""

import argparse
import sys

import api_cliente
from api_cliente import ErrorAPI, SinConexion


def main():
    p = argparse.ArgumentParser(description="Bloquea tarjetas por robo o pérdida")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--tarjeta", type=int, help="n.º de tarjeta")
    g.add_argument("--dni", help="bloquear todas las tarjetas activas de este cliente")
    p.add_argument("--motivo", default="reporte de robo o pérdida")
    args = p.parse_args()

    try:
        if args.tarjeta is not None:
            t = api_cliente.llamar("POST", f"/api/tarjetas/{args.tarjeta}/bloquear/",
                                   {"motivo": args.motivo})
            bloqueadas = [{"id": t["id"], "uid": t["uid"]}]
        else:
            cuenta = api_cliente.llamar("GET", f"/api/cuentas/?documento={args.dni}")
            bloqueadas = api_cliente.llamar("POST", f"/api/cuentas/{cuenta['id']}/bloquear/",
                                            {"motivo": args.motivo})["bloqueadas"]
    except ErrorAPI as e:
        sys.exit(f"[X] {'No encontrado' if e.estado == 404 else e}")
    except SinConexion as e:
        sys.exit(f"[X] Sin conexión con el servidor: {e}")

    if not bloqueadas:
        sys.exit("No había tarjetas activas que bloquear")
    for t in bloqueadas:
        print(f"Bloqueada tarjeta n.º {t['id']} (UID {t['uid']})")
    print("Los validadores la rechazarán a partir de su próxima sincronización.")


if __name__ == "__main__":
    main()
