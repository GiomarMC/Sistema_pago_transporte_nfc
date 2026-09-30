#!/usr/bin/env python3
"""
Detecta el tipo exacto de tarjetas MIFARE Ultralight / NTAG con un ACR122U
y muestra su memoria.

Uso:
    python3 detectar_ultralight.py            # análisis completo + volcado de memoria
    python3 detectar_ultralight.py --resumen  # una línea por tarjeta (para revisar muchas)

Acerca las tarjetas de una en una. Ctrl+C para salir (muestra un recuento final).
"""

import argparse
import sys
import time
from collections import Counter

from smartcard.CardRequest import CardRequest
from smartcard.Exceptions import CardConnectionException, NoCardException
from smartcard.System import readers
from smartcard.scard import SCARD_LEAVE_CARD, SCARD_UNPOWER_CARD

# --- Tabla de respuestas a GET_VERSION (0x60) -------------------------------
# clave: los 8 bytes de la respuesta
# valor: (nombre, páginas totales, primera pág. usuario, última pág. usuario, tiene contador)
VERSIONES = {
    "0004030101000B03": ("MIFARE Ultralight EV1 (MF0UL11)", 20, 4, 15, True),
    "0004030201000B03": ("MIFARE Ultralight EV1 (MF0ULH11)", 20, 4, 15, True),
    "0004030101000E03": ("MIFARE Ultralight EV1 (MF0UL21)", 41, 4, 35, True),
    "0004030201000E03": ("MIFARE Ultralight EV1 (MF0ULH21)", 41, 4, 35, True),
    "0004040201000F03": ("NTAG213", 45, 4, 39, True),
    "0004040201001103": ("NTAG215", 135, 4, 129, True),
    "0004040201001303": ("NTAG216", 231, 4, 225, True),
}

# Tipos sin GET_VERSION
ULTRALIGHT_C = ("MIFARE Ultralight C (MF0ICU2)", 44, 4, 39, False)  # págs 44-47 = clave 3DES, no legibles
ULTRALIGHT = ("MIFARE Ultralight (MF0ICU1, original)", 16, 4, 15, False)

# Nombre de tarjeta en el ATR PC/SC (bytes 13-14)
NOMBRES_ATR = {
    "0001": "MIFARE Classic 1K",
    "0002": "MIFARE Classic 4K",
    "0003": "MIFARE Ultralight / NTAG",
    "0026": "MIFARE Mini",
}


class ErrorNFC(Exception):
    pass


# --- Comunicación con el ACR122U ---------------------------------------------

def apdu(conn, datos):
    """Envía un APDU y devuelve (respuesta, sw). Gestiona el 61xx del ACR122U."""
    try:
        resp, sw1, sw2 = conn.transmit(list(datos))
    except CardConnectionException as e:
        # Tras re-activar la tarjeta, pcscd puede marcar la conexión como "reiniciada"
        # y rechazar el siguiente envío. Se reconecta sin tocar la tarjeta y se reintenta.
        if "reset" not in str(e).lower():
            raise
        conn.disconnect()
        conn.connect(disposition=SCARD_LEAVE_CARD)
        resp, sw1, sw2 = conn.transmit(list(datos))
    if sw1 == 0x61:  # firmware antiguo: hay que pedir la respuesta aparte
        resp, sw1, sw2 = conn.transmit([0xFF, 0xC0, 0x00, 0x00, sw2])
    return resp, (sw1 << 8) | sw2


def pn532(conn, cmd):
    """Envía un comando al chip PN532 del lector (Direct Transmit)."""
    resp, sw = apdu(conn, [0xFF, 0x00, 0x00, 0x00, len(cmd)] + list(cmd))
    if sw != 0x9000 or len(resp) < 2:
        raise ErrorNFC(f"PN532 sw={sw:04X}")
    return resp


def comando_nativo(conn, cmd):
    """Envía un comando nativo de la tarjeta (InCommunicateThru, D4 42)."""
    resp = pn532(conn, [0xD4, 0x42] + list(cmd))
    # Respuesta: D5 43 <estado> <datos...>
    if resp[:2] != [0xD5, 0x43] or len(resp) < 3:
        raise ErrorNFC("respuesta inesperada del PN532")
    if resp[2] & 0x3F != 0x00:
        raise ErrorNFC(f"la tarjeta no respondió (estado PN532 {resp[2]:02X})")
    return resp[3:]


def reseleccionar(conn):
    """
    Tras un comando que la tarjeta no reconoce (NAK), esta queda 'muda'.
    Se libera el objetivo en el PN532 y se vuelve a activar la tarjeta;
    si eso falla, se apaga y enciende el campo RF.
    """
    try:
        pn532(conn, [0xD4, 0x52, 0x00])  # InRelease: olvida los objetivos anteriores
        resp = pn532(conn, [0xD4, 0x4A, 0x01, 0x00])  # InListPassiveTarget, ISO 14443-A
        if len(resp) >= 3 and resp[2] == 1:
            return
    except (ErrorNFC, CardConnectionException):
        pass
    conn.reconnect(disposition=SCARD_UNPOWER_CARD)


def obtener_uid(conn):
    resp, sw = apdu(conn, [0xFF, 0xCA, 0x00, 0x00, 0x00])
    if sw != 0x9000:
        raise ErrorNFC(f"no se pudo leer el UID (sw={sw:04X})")
    return bytes(resp)


def leer_4_paginas(conn, pagina, intentos=2):
    """
    READ nativo (0x30): devuelve 16 bytes = 4 páginas, o None si está protegido/no existe.
    Se usa el comando nativo en vez del APDU FF B0 para no mezclar el estado interno
    del ACR122U con nuestras re-selecciones.
    """
    for _ in range(intentos):
        try:
            resp = comando_nativo(conn, [0x30, pagina])
            if len(resp) == 16:
                return bytes(resp)
        except ErrorNFC:
            pass
        reseleccionar(conn)
    return None


# --- Detección ----------------------------------------------------------------

def tipo_desde_atr(atr):
    """Devuelve el nombre de la tarjeta según el ATR PC/SC, o None si no es memoria."""
    if len(atr) >= 15 and atr[4:6] == [0x80, 0x4F] and atr[7:12] == [0xA0, 0x00, 0x00, 0x03, 0x06]:
        codigo = f"{atr[13]:02X}{atr[14]:02X}"
        return NOMBRES_ATR.get(codigo, f"desconocida (código {codigo})")
    return None


def detectar(conn):
    """Identifica la variante. Devuelve (info, version_hex|None)."""
    # 1) GET_VERSION: responden Ultralight EV1 y NTAG21x
    try:
        v = bytes(comando_nativo(conn, [0x60]))
        if len(v) == 8:
            clave = v.hex().upper()
            if clave in VERSIONES:
                return VERSIONES[clave], clave
            familia = {0x03: "Ultralight", 0x04: "NTAG"}.get(v[2], f"tipo {v[2]:02X}")
            return (f"{familia} no catalogada (versión {clave})", None, 4, None, True), clave
    except ErrorNFC:
        reseleccionar(conn)

    # 2) AUTHENTICATE (1A 00): responde solo la Ultralight C (AF + 8 bytes)
    try:
        r = comando_nativo(conn, [0x1A, 0x00])
        reseleccionar(conn)  # dejamos la autenticación a medias: reiniciamos
        if len(r) == 9 and r[0] == 0xAF:
            return ULTRALIGHT_C, None
    except ErrorNFC:
        reseleccionar(conn)

    # 3) No responde a nada de lo anterior: Ultralight original
    return ULTRALIGHT, None


def leer_memoria(conn, total_paginas):
    """Lee la memoria página a página (de 4 en 4). None = página no legible."""
    paginas = []
    limite = total_paginas if total_paginas else 256
    for p in range(0, limite, 4):
        bloque = leer_4_paginas(conn, p)
        if bloque is None:
            if total_paginas is None:  # tamaño desconocido: paramos al primer error
                break
            reseleccionar(conn)
            paginas.extend([None] * 4)
            continue
        paginas.extend(bloque[i:i + 4] for i in range(0, 16, 4))
    # READ lee 4 páginas y en algunas tarjetas "da la vuelta": recortamos al tamaño real
    return paginas[:limite]


def leer_extras(conn, es_ntag, paginas, total):
    """Firma de originalidad y contadores (solo EV1 / NTAG)."""
    extras = {}
    try:
        firma = comando_nativo(conn, [0x3C, 0x00])
        extras["firma"] = bytes(firma).hex().upper() if len(firma) == 32 else None
    except ErrorNFC:
        reseleccionar(conn)
        extras["firma"] = None

    # EV1: 3 contadores (0, 1, 2). NTAG21x: solo el 2, y solo si NFC_CNT_EN (bit 4 de
    # ACCESS, primer byte de CFG1) está activado; si no, la tarjeta responde NAK.
    extras["contadores"] = {}
    indices = [0, 1, 2]
    if es_ntag:
        cfg1 = paginas[total - 3] if total and total - 3 < len(paginas) else None
        if cfg1 is None or not cfg1[0] & 0x10:
            extras["contadores"][2] = None
            indices = []
        else:
            indices = [2]
    for n in indices:
        try:
            c = comando_nativo(conn, [0x39, n])
            if len(c) == 3:
                extras["contadores"][n] = c[0] | (c[1] << 8) | (c[2] << 16)
        except ErrorNFC:
            reseleccionar(conn)
            extras["contadores"][n] = None
    return extras


# --- Presentación --------------------------------------------------------------

def hexs(b):
    return " ".join(f"{x:02X}" for x in b)


def ascii_(b):
    return "".join(chr(x) if 32 <= x < 127 else "." for x in b)


def etiqueta_pagina(p, info):
    nombre, total, u_ini, u_fin, _ = info
    if p <= 1:
        return "UID"
    if p == 2:
        return "UID / bloqueo"
    if p == 3:
        return "OTP / CC"
    if u_fin is not None and u_ini <= p <= u_fin:
        return "usuario"
    if total and ("EV1" in nombre or "NTAG" in nombre):
        cfg0 = total - 4
        return {cfg0: "CFG0", cfg0 + 1: "CFG1", cfg0 + 2: "PWD", cfg0 + 3: "PACK"}.get(p, "bloqueo dinámico")
    if "Ultralight C" in nombre:
        return {40: "bloqueo", 41: "contador 16 bits", 42: "AUTH0", 43: "AUTH1"}.get(p, "")
    return ""


def proteccion(paginas, info):
    """Interpreta AUTH0 (a partir de qué página se pide contraseña)."""
    nombre, total, *_ = info
    if not total:
        return "desconocida"
    if "Ultralight C" in nombre:
        pag, idx = 42, 0
    elif "EV1" in nombre or "NTAG" in nombre:
        pag, idx = total - 4, 3
    else:
        return "no tiene (Ultralight original: sin contraseña)"
    if pag >= len(paginas) or paginas[pag] is None:
        return "no legible"
    auth0 = paginas[pag][idx]
    if auth0 >= total:
        return f"desactivada (AUTH0={auth0:02X})"
    return f"ACTIVADA desde la página {auth0} (AUTH0={auth0:02X})"


def mostrar_completo(uid, atr, info, version, paginas, extras):
    nombre, total, u_ini, u_fin, tiene_contador = info
    print("=" * 64)
    print(f" Tipo:        {nombre}")
    print(f" UID:         {hexs(uid)}  ({len(uid)} bytes)")
    print(f" ATR:         {hexs(atr)}")
    if version:
        print(f" GET_VERSION: {version}")
    if total:
        print(f" Memoria:     {total} páginas ({total * 4} bytes)")
    if u_fin is not None:
        n = u_fin - u_ini + 1
        print(f" Usuario:     páginas {u_ini}-{u_fin} = {n * 4} bytes libres")
    print(f" Contraseña:  {proteccion(paginas, info)}")
    if extras:
        firma = extras.get("firma")
        if not firma:
            print(" Firma NXP:   no disponible")
        elif set(firma) == {"0"}:
            print(" Firma NXP:   TODO CEROS: probablemente chip compatible, no NXP original")
        else:
            print(f" Firma NXP:   {firma[:32]}…")
        for n, v in extras.get("contadores", {}).items():
            print(f" Contador {n}:  {v if v is not None else 'no disponible / deshabilitado'}")
    print("-" * 64)
    print(" Pág      Hex           ASCII  Zona")
    p = 0
    while p < len(paginas):
        # Agrupa páginas consecutivas iguales de la misma zona (p. ej. zona libre vacía)
        fin = p
        while (fin + 1 < len(paginas) and paginas[fin + 1] == paginas[p]
               and etiqueta_pagina(fin + 1, info) == etiqueta_pagina(p, info)):
            fin += 1
        rango = f"{p}" if fin == p else f"{p}-{fin}"
        zona = etiqueta_pagina(p, info)
        if paginas[p] is None:
            print(f" {rango:>7}  ?? ?? ?? ??   ....   {zona} (no legible)")
        else:
            print(f" {rango:>7}  {hexs(paginas[p])}   {ascii_(paginas[p])}   {zona}")
        p = fin + 1
    print("=" * 64)
    print()


def mostrar_resumen(uid, info, extras):
    nombre, total, u_ini, u_fin, _ = info
    libres = f"{(u_fin - u_ini + 1) * 4} B usuario" if u_fin is not None else "?"
    cont = ""
    if extras and extras.get("contadores"):
        vals = [v for v in extras["contadores"].values() if v is not None]
        cont = f" | contador: {'sí' if vals else 'no'}"
    print(f"{hexs(uid):<22} | {nombre:<40} | {libres}{cont}")


# --- Bucle principal -----------------------------------------------------------

def esperar_retirada(lector):
    while True:
        c = lector.createConnection()
        try:
            c.connect()
            c.disconnect()
        except (NoCardException, CardConnectionException):
            return
        time.sleep(0.3)


def procesar(conn, args, recuento, vistas):
    atr = conn.getATR()
    uid = obtener_uid(conn)
    tipo_atr = tipo_desde_atr(atr)

    if tipo_atr != "MIFARE Ultralight / NTAG":
        desc = tipo_atr or "tarjeta ISO 14443-4 (p. ej. NTAG 424, DESFire)"
        print(f"{hexs(uid):<22} | {desc} — no es Ultralight, se omite")
        recuento[desc] += 1
        return

    info, version = detectar(conn)
    nombre = info[0]
    # Primero la memoria (hace falta CFG1 para saber si el contador está activo)
    paginas = leer_memoria(conn, info[1])
    extras = leer_extras(conn, "NTAG" in nombre, paginas, info[1]) if version else None

    if args.resumen:
        mostrar_resumen(uid, info, extras)
    else:
        mostrar_completo(uid, atr, info, version, paginas, extras)

    if uid not in vistas:
        vistas.add(uid)
        recuento[nombre] += 1


def main():
    parser = argparse.ArgumentParser(description="Detecta tarjetas Ultralight/NTAG con ACR122U")
    parser.add_argument("--resumen", action="store_true", help="una línea por tarjeta")
    parser.add_argument("--una", action="store_true",
                        help="analiza la tarjeta que ya está en el lector y termina")
    args = parser.parse_args()

    lista = readers()
    if not lista:
        sys.exit("No se encontró ningún lector. ¿Está conectado y pcscd activo?")
    lector = lista[0]
    print(f"Lector: {lector}")
    print("Acerca una tarjeta (Ctrl+C para terminar)...\n")

    recuento = Counter()
    vistas = set()
    if args.una:
        conn = lector.createConnection()
        try:
            conn.connect(disposition=SCARD_LEAVE_CARD)
        except NoCardException:
            sys.exit("No hay ninguna tarjeta en el lector.")
        procesar(conn, args, recuento, vistas)
        return
    try:
        while True:
            servicio = CardRequest(readers=[lector], timeout=None, newcardonly=True).waitforcard()
            conn = servicio.connection
            try:
                conn.connect(disposition=SCARD_LEAVE_CARD)
                procesar(conn, args, recuento, vistas)
            except (ErrorNFC, CardConnectionException, NoCardException) as e:
                print(f"Error leyendo la tarjeta (¿la retiraste muy rápido?): {e}")
            finally:
                try:
                    conn.disconnect()
                except Exception:
                    pass
            esperar_retirada(lector)
    except KeyboardInterrupt:
        print("\n\nRecuento de tarjetas distintas:")
        for nombre, n in recuento.most_common():
            print(f"  {n:3d} × {nombre}")
        print(f"  Total: {len(vistas)} Ultralight/NTAG distintas")


if __name__ == "__main__":
    main()
