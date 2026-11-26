"""
Lector PC/SC (ACR122U): esperar una tarjeta y esperar a que se retire.

pyscard se importa al llamar a las funciones, no al importar el módulo, para que
el resto del paquete se pueda usar y probar sin lector ni pyscard instalados.
"""

import time

from tarjeta.base import ErrorTarjeta


def esperar_conexion(mensaje="Acerca una tarjeta al lector...", timeout=None):
    """Espera a que haya una tarjeta en el lector y devuelve la conexión PC/SC.
    Con timeout (segundos) devuelve None si no aparece ninguna."""
    from smartcard.Exceptions import CardConnectionException, NoCardException
    from smartcard.System import readers
    from smartcard.scard import SCARD_LEAVE_CARD

    lista = readers()
    if not lista:
        raise ErrorTarjeta("no se encontró ningún lector (¿conectado? ¿pcscd activo?)")
    avisado = False
    limite = None if timeout is None else time.monotonic() + timeout
    while True:
        conexion = lista[0].createConnection()
        try:
            conexion.connect(disposition=SCARD_LEAVE_CARD)
            return conexion
        except (NoCardException, CardConnectionException):
            if mensaje and not avisado:
                print(mensaje)
                avisado = True
            if limite is not None and time.monotonic() >= limite:
                return None
            time.sleep(0.1)


def esperar_retirada():
    from smartcard.Exceptions import CardConnectionException, NoCardException
    from smartcard.System import readers
    from smartcard.scard import SCARD_LEAVE_CARD

    lector = readers()[0]
    while True:
        conexion = lector.createConnection()
        try:
            conexion.connect(disposition=SCARD_LEAVE_CARD)
            conexion.disconnect()
        except (NoCardException, CardConnectionException):
            return
        time.sleep(0.2)
