#!/usr/bin/env python3
"""
Emite (personaliza) una tarjeta NTAG215 para un cliente.

  Cliente nuevo:
    python3 emitir_tarjeta.py --nombre "Ana Quispe" --dni 71234567 --tarifa estudiante --saldo 10.00

  Tarjeta de reemplazo (robo/pérdida) para una cuenta existente; conserva el saldo
  y bloquea las tarjetas anteriores de esa cuenta:
    python3 emitir_tarjeta.py --cuenta 3

  --forzar  reescribe una tarjeta que ya estaba emitida por este sistema.

Pasos: comprueba la tarjeta -> reserva el n.º de tarjeta en el servidor ->
graba cabecera y saldo firmados -> activa contraseña y contador NFC -> verifica
releyendo -> confirma en el servidor (si algo falla, cancela la reserva).
Con --saldo, a continuación hace una recarga en punto con la misma tarjeta.
"""

import argparse
import sys
from datetime import date

import api_cliente
from comprobante import abrir_comprobante
from api_cliente import ErrorAPI, SinConexion
from config import TARIFA_POR_NOMBRE, TARIFAS, formato_soles, parsear_soles
from recargar import ErrorRecarga, recargar_tarjeta
from tarjeta import claves, formato
from tarjeta.base import (ErrorTarjeta, TarjetaAjena, TarjetaPersonalizable, TipoNoSoportado,
                          esperar_tarjeta)


def preparar_acceso(t: TarjetaPersonalizable, forzar: bool):
    """Comprueba que la tarjeta (TarjetaFisica y Personalizable) se puede emitir y
    deja la sesión autenticada si hace falta."""
    if not t.protegida():
        ya_emitida = t.leer_area()[:2] == formato.MARCA
    else:
        # Protegida: solo seguimos si es nuestra (responde con el PACK esperado)
        try:
            t.autenticar()
        except ErrorTarjeta:
            raise ErrorTarjeta("la tarjeta tiene una contraseña que no es de este sistema")
        ya_emitida = True

    if ya_emitida and not forzar:
        raise ErrorTarjeta("la tarjeta ya está emitida (usa --forzar para reemitirla)")

    if t.configuracion_bloqueada():
        raise ErrorTarjeta("la configuración de la tarjeta está bloqueada de forma permanente")


def verificar(t: TarjetaPersonalizable, area: bytes):
    """Relee la tarjeta en una sesión nueva, como lo haría un validador."""
    t.reiniciar_sesion()
    if not t.protegida():
        raise ErrorTarjeta("verificación: la protección por contraseña no quedó activa")
    try:
        t.autenticar()
    except TarjetaAjena:
        raise ErrorTarjeta("verificación: PACK incorrecto")
    leida = t.leer_area()
    if leida != area:
        raise ErrorTarjeta("verificación: los datos leídos no coinciden con los grabados")
    estado = formato.decodificar(leida, t.uid, claves.clave_firma(t.uid))
    return estado, t.contador()


def main():
    p = argparse.ArgumentParser(description="Emite una tarjeta de transporte (NTAG215)")
    p.add_argument("--nombre")
    p.add_argument("--dni", help="documento de identidad")
    p.add_argument("--tarifa", choices=sorted(TARIFA_POR_NOMBRE))
    p.add_argument("--saldo", default="0", help="recarga inicial en soles, p. ej. 10.50")
    p.add_argument("--cuenta", type=int, help="emitir tarjeta de reemplazo para esta cuenta")
    p.add_argument("--forzar", action="store_true", help="reemitir una tarjeta ya emitida")
    p.add_argument("--sin-comprobante", action="store_true", help="no abrir la hoja de activación")
    args = p.parse_args()

    if args.cuenta is None and not (args.nombre and args.dni and args.tarifa):
        p.error("indica --cuenta, o bien --nombre, --dni y --tarifa para un cliente nuevo")
    if args.cuenta is not None and (args.nombre or args.dni or args.tarifa or args.saldo != "0"):
        p.error("con --cuenta no se indican datos del cliente ni saldo (la recarga va aparte)")
    try:
        recarga = parsear_soles(args.saldo)
    except ValueError as e:
        p.error(str(e))

    try:
        t = esperar_tarjeta()
    except TipoNoSoportado as e:
        print(f"Tarjeta detectada: UID {e.uid.hex().upper()}")
        sys.exit(f"[X] No se emitió la tarjeta: {e}")
    uid_hex = t.uid.hex().upper()
    print(f"Tarjeta detectada: UID {uid_hex}")
    reserva = None
    try:
        preparar_acceso(t, args.forzar)

        # 1) Reserva en el servidor: crea la cuenta si es nueva y asigna n.º de tarjeta
        peticion = {"uid": uid_hex}
        if args.cuenta is not None:
            peticion["cuenta"] = args.cuenta
        else:
            peticion.update(nombre=args.nombre, documento=args.dni,
                            tarifa=TARIFA_POR_NOMBRE[args.tarifa])
        reserva = api_cliente.llamar("POST", "/api/emisiones/", peticion)

        # 2) Grabar con el saldo real de la cuenta, que ya incluye todas sus recargas
        #    (también las remotas que no llegaron a la tarjeta anterior)
        cab = formato.Cabecera(reserva["tarjeta"], reserva["cuenta"]["id"], reserva["tarifa"],
                               date.today())
        area = formato.codificar_emision(cab, reserva["saldo"], t.uid,
                                         claves.clave_firma(t.uid), reserva["recarga_inicial"])
        print("Grabando...")
        t.personalizar(area)
        estado, contador = verificar(t, area)

        # 3) Confirmar: activa la tarjeta y bloquea las anteriores de la cuenta
        confirmacion = api_cliente.llamar(
            "POST", f"/api/emisiones/{reserva['tarjeta']}/confirmar/", {"contador": contador})
    except (ErrorTarjeta, formato.ErrorFormato, ErrorAPI, SinConexion) as e:
        if reserva is not None:
            try:
                api_cliente.llamar("POST", f"/api/emisiones/{reserva['tarjeta']}/cancelar/")
            except (ErrorAPI, SinConexion):
                print(f"[!] No se pudo cancelar la reserva {reserva['tarjeta']} en el servidor")
        t.cerrar()
        sys.exit(f"[X] No se emitió la tarjeta: {e}")

    cuenta = reserva["cuenta"]
    nombre_tarifa, precio = TARIFAS.get(cab.tarifa, (cuenta["tarifa_nombre"], 0))
    print()
    print("[OK] Tarjeta emitida y verificada")
    print(f"  Cliente:     {cuenta['nombre']} (DNI {cuenta['documento']}) — cuenta {cuenta['id']}")
    print(f"  Tarjeta:     n.º {reserva['tarjeta']}, UID {uid_hex}")
    if confirmacion.get("numero_impreso"):
        print(f"  Número:      {confirmacion['numero_impreso']}")
    print(f"  Tarifa:      {nombre_tarifa} ({formato_soles(precio)} por viaje)")
    print(f"  Saldo:       {formato_soles(estado.registro.saldo)}")
    print(f"  Contador:    {contador}")
    for ant in confirmacion["bloqueadas"]:
        print(f"  Bloqueada:   tarjeta anterior n.º {ant['id']} (UID {ant['uid']})")
    for solicitud_id in confirmacion.get("reposiciones_entregadas", []):
        print(f"  Reposición:  solicitud n.º {solicitud_id} entregada")

    codigo = confirmacion.get("codigo_activacion")
    if codigo:
        print(f"  Activación:  {codigo} (vence {confirmacion['codigo_expira']})")
        if not args.sin_comprobante:
            try:
                html = api_cliente.llamar_html(
                    "POST", f"/api/tarjetas/{reserva['tarjeta']}/comprobante/", {"codigo": codigo})
                if not abrir_comprobante(html):
                    print("[!] El navegador no abrió el comprobante; conserva el código mostrado arriba.")
            except (ErrorAPI, SinConexion, OSError) as e:
                print(f"[!] La tarjeta está emitida, pero no se pudo abrir el comprobante: {e}")

    if recarga:
        try:
            _, saldo, _, _ = recargar_tarjeta(t, recarga)
            print(f"  Recarga:     {formato_soles(recarga)} -> saldo {formato_soles(saldo)}")
        except (ErrorRecarga, ErrorTarjeta, SinConexion) as e:
            print(f"[!] La tarjeta quedó emitida, pero la recarga inicial falló: {e}")
            print("    Repítela con:  python3 recargar.py --monto " + args.saldo)
    t.cerrar()


if __name__ == "__main__":
    main()
