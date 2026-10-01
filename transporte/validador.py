#!/usr/bin/env python3
"""
Validador de bus: cobra el pasaje al acercar la tarjeta. Funciona sin conexión
y sincroniza con el servidor cuando puede.

    python3 validador.py --id 101                 # con "red": sincroniza al inicio y cada 30 s
    python3 validador.py --id 101 --sin-red       # simula un bus sin conexión
    python3 validador.py --id 101 --sincronizar   # solo sincroniza y sale
    python3 validador.py --id 101 --pantalla auto           # con pantalla ESP32 (o el puerto:
                                                            # /dev/ttyUSB0, COM3, /dev/cu.usbserial-...)

Por cada toque: autentica la tarjeta, verifica firmas, comprueba lista negra y
saldo, graba el nuevo saldo en el registro libre, verifica la escritura y deja
el viaje en la cola local.
"""

import argparse
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone

from api_cliente import ErrorAPI, SinConexion
from config import formato_soles
from pantalla import Pantalla, SinPantalla
from tarjeta import claves, formato
from tarjeta.ntag215 import ErrorTarjeta, NTAG215
from validador_local import BDValidador

# Un segundo toque en este mismo validador dentro de este margen no se cobra
MARGEN_DOBLE_TOQUE = timedelta(seconds=60)


class Rechazo(Exception):
    """motivo y detalle se muestran en la pantalla; aviso=True significa que el
    pasajero debe reintentar (pantalla ámbar) en vez de un rechazo definitivo."""

    def __init__(self, motivo, detalle="", aviso=False):
        super().__init__(f"{motivo} ({detalle})" if detalle else motivo)
        self.motivo, self.detalle, self.aviso = motivo, detalle, aviso


# Resultado de un cobro aceptado: texto para la consola y líneas para la pantalla
Cobro = namedtuple("Cobro", "mensaje saldo lineas")


def grabar_bloqueo(t, cab, clave):
    """Escribe el estado 'bloqueada' en la propia tarjeta: así la rechazan también
    los validadores que aún no tienen la lista negra actualizada."""
    cab.estado = formato.BLOQUEADA
    t.escribir_paginas(formato.PAG_CABECERA, cab.a_bytes(t.uid, clave))


def cobrar(t: NTAG215, bdv: BDValidador):
    """Devuelve un Cobro. Lanza Rechazo si no se permite el paso."""
    uid_hex = t.uid.hex().upper()
    pwd, pack = claves.contrasena(t.uid)
    try:
        if t.autenticar(pwd) != pack:
            raise Rechazo("Tarjeta no válida", "No reconocida")
    except ErrorTarjeta:
        raise Rechazo("Tarjeta no válida", "No emitida por el sistema")

    clave = claves.clave_firma(t.uid)
    try:
        est = formato.decodificar(t.leer(formato.PAG_INICIO, formato.PAG_FIN), t.uid, clave)
    except formato.ErrorFormato as e:
        bdv.registrar_incidencia(0, uid_hex, f"tarjeta alterada: {e}", fraude=True)
        raise Rechazo("Tarjeta no válida", "Datos alterados")
    contador = t.contador_nfc()
    cab, reg = est.cabecera, est.registro

    if cab.estado == formato.BLOQUEADA:
        raise Rechazo("Tarjeta bloqueada", "Acuda a atención")
    if bdv.bloqueada(cab.id_tarjeta):
        grabar_bloqueo(t, cab, clave)
        bdv.registrar_incidencia(cab.id_tarjeta, uid_hex,
                                 "intento de uso de tarjeta bloqueada; bloqueo grabado en la tarjeta")
        raise Rechazo("Tarjeta bloqueada", "Acuda a atención")
    if reg.operacion < bdv.ultima_operacion(cab.id_tarjeta):
        bdv.registrar_incidencia(cab.id_tarjeta, uid_hex,
                                 f"operación {reg.operacion} menor que la ya vista: "
                                 "saldo restaurado de una copia antigua", fraude=True)
        raise Rechazo("Tarjeta no válida", "Acuda a atención")

    tarifa = bdv.tarifa(cab.tarifa)
    if tarifa is None:
        raise Rechazo("Tarifa desconocida", f"Código {cab.tarifa}")
    nombre_tarifa, precio = tarifa

    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    if reg.validador == bdv.id and ahora - reg.fecha_hora < MARGEN_DOBLE_TOQUE:
        # Ya cobrado aquí hace un momento. Si la verificación de aquel cobro falló
        # (tarjeta retirada justo al final), el viaje se registra ahora.
        if not bdv.existe_viaje(cab.id_tarjeta, reg.operacion):
            bdv.registrar_viaje(cab.id_tarjeta, uid_hex, precio, reg.saldo, reg.operacion, contador,
                                reg.recarga or None)
        return Cobro("ya pagado", reg.saldo,
                     ("Ya pagado", f"Saldo {formato_soles(reg.saldo)}", ""))

    # Recargas remotas (Yape, web...) que aún no llegaron a esta tarjeta
    abono, recarga_hasta = bdv.recargas_pendientes(cab.id_cuenta, reg.recarga)
    disponible = reg.saldo + abono
    if disponible < precio:
        raise Rechazo("Saldo insuficiente", f"Saldo {formato_soles(disponible)}")

    nuevo = formato.Registro(disponible - precio, reg.operacion + 1, ahora, bdv.id, recarga_hasta)
    pagina = formato.PAG_REGISTROS[est.ranura_libre]
    datos = nuevo.a_bytes(t.uid, clave, cab)
    try:
        t.escribir_paginas(pagina, datos)
        if t.leer(pagina, pagina + len(datos) // 4 - 1) != datos:
            raise ErrorTarjeta("verificación de escritura")
    except ErrorTarjeta:
        # El registro anterior sigue intacto: no se cobró
        raise Rechazo("Acerque de nuevo", "Lectura interrumpida", aviso=True)

    bdv.registrar_viaje(cab.id_tarjeta, uid_hex, precio, nuevo.saldo, nuevo.operacion, contador,
                        recarga_hasta if abono else None)
    mensaje = f"pasaje {nombre_tarifa} {formato_soles(precio)}"
    if abono:
        mensaje += f" (+recarga {formato_soles(abono)})"
    return Cobro(mensaje, nuevo.saldo, (
        f"{nombre_tarifa.capitalize()} {formato_soles(precio)}",
        f"Saldo {formato_soles(nuevo.saldo)}",
        f"Recarga +{formato_soles(abono)}" if abono else ""))


def intentar_sincronizar(bdv, sin_red):
    """Devuelve True si se sincronizó con el servidor."""
    if sin_red:
        return False
    try:
        subidos, bloqueadas = bdv.sincronizar()
        print(f"  [sync] {subidos} eventos subidos, {bloqueadas} tarjetas en lista negra")
        if bdv.desfase is not None and abs(bdv.desfase) > 60:
            print(f"  [!] el reloj de este validador difiere {bdv.desfase:+.0f} s del servidor")
        return True
    except SinConexion as e:  # sin red o servidor caído: se reintenta más tarde
        print(f"  [sync] sin conexión con el servidor ({e}); se reintentará")
    except ErrorAPI as e:
        print(f"  [sync] el servidor rechazó la sincronización ({e.estado}): {e}")
    return False


def main():
    p = argparse.ArgumentParser(description="Validador de bus")
    p.add_argument("--id", type=int, default=101, help="número de validador (bus)")
    p.add_argument("--sin-red", action="store_true", help="simular que no hay conexión")
    p.add_argument("--sync-cada", type=int, default=30, help="segundos entre sincronizaciones")
    p.add_argument("--sincronizar", action="store_true", help="solo sincronizar y salir")
    p.add_argument("--pantalla", metavar="PUERTO",
                   help="puerto de la pantalla ESP32 ('auto' para detectarlo; o /dev/ttyUSB0, COM3...)")
    args = p.parse_args()

    bdv = BDValidador(args.id)
    if args.sincronizar:
        intentar_sincronizar(bdv, False)
        print(bdv.resumen())
        return

    r = bdv.resumen()
    print(f"Validador {args.id} — {'SIN RED' if args.sin_red else 'con red'} | "
          f"eventos pendientes: {r['pendientes']} | lista negra: {r['bloqueadas']} | "
          f"última sync: {r['ultima_sync']}")
    pantalla = SinPantalla()
    if args.pantalla:
        try:
            pantalla = Pantalla(args.pantalla)
            print(f"Pantalla conectada en {args.pantalla}")
        except Exception as e:
            print(f"  [!] No se pudo usar la pantalla en {args.pantalla}: {e}. "
                  "Se cobra sin pantalla.")

    def actualizar_espera(con_red):
        pantalla.espera(f"Bus {args.id}", "En linea" if con_red else "Sin conexion")

    actualizar_espera(intentar_sincronizar(bdv, args.sin_red))
    print("Esperando tarjetas (Ctrl+C para salir)...\n")

    proxima_sync = time.monotonic() + args.sync_cada
    try:
        while True:
            t = NTAG215.esperar(mensaje=None, timeout=1.0)
            if t is None:
                pantalla.mantener()
                if time.monotonic() >= proxima_sync:
                    actualizar_espera(intentar_sincronizar(bdv, args.sin_red))
                    proxima_sync = time.monotonic() + args.sync_cada
                continue

            inicio = time.perf_counter()
            hora = datetime.now().strftime("%H:%M:%S")
            try:
                cobro = cobrar(t, bdv)
                ms = (time.perf_counter() - inicio) * 1000
                pantalla.pasa(*cobro.lineas)
                print(f"{hora}  [PASA]      {cobro.mensaje:<44} saldo "
                      f"{formato_soles(cobro.saldo):>10}  ({ms:.0f} ms)")
            except Rechazo as e:
                ms = (time.perf_counter() - inicio) * 1000
                (pantalla.aviso if e.aviso else pantalla.rechazo)(e.motivo, e.detalle)
                etiqueta = "[REINTENTE]" if e.aviso else "[RECHAZADA]"
                print(f"{hora}  {etiqueta} {str(e):<44} {'':>16}  ({ms:.0f} ms)")
            except ErrorTarjeta as e:
                pantalla.aviso("Acerque de nuevo", "Lectura fallida")
                print(f"{hora}  [ERROR]     {e}. Vuelva a acercar la tarjeta")
            finally:
                t.cerrar()
            NTAG215.esperar_retirada()
    except KeyboardInterrupt:
        print("\nCerrando validador.")
        intentar_sincronizar(bdv, args.sin_red)


if __name__ == "__main__":
    main()
