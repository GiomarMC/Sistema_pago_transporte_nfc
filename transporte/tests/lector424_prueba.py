"""Lado lector mínimo para probar el simulador (no es el controlador de M1-04)."""

from tarjeta import apdu424 as a
from tarjeta import ev2

PLAIN, MAC, FULL = "plain", "mac", "full"
SW_OK = 0x9100


def autenticar(tarjeta, numero: int, clave: bytes, rnd_a: bytes = bytes(range(16))):
    """AuthenticateEV2First completo; devuelve la Sesion o lanza AssertionError."""
    datos, sw = a.separar_sw(tarjeta.transmitir(a.nativo(a.INS_AUTH_EV2_FIRST, bytes([numero, 0]))))
    if sw != 0x91AF:
        raise AssertionError(f"parte 1: SW {sw:04X}")
    rnd_b = ev2.aes_cbc_descifrar(clave, ev2.IV_CERO, datos)
    parte2 = ev2.aes_cbc_cifrar(clave, ev2.IV_CERO, rnd_a + ev2.rotar_izquierda(rnd_b))
    datos, sw = a.separar_sw(tarjeta.transmitir(a.nativo(a.INS_FRAME_ADICIONAL, parte2)))
    if sw != SW_OK:
        raise AssertionError(f"parte 2: SW {sw:04X}")
    claro = ev2.aes_cbc_descifrar(clave, ev2.IV_CERO, datos)
    if ev2.rotar_derecha(claro[4:20]) != rnd_a:
        raise AssertionError("la tarjeta no devolvió RndA rotado")
    enc, mac = ev2.claves_sesion(clave, rnd_a, rnd_b)
    return ev2.Sesion(enc, mac, claro[:4])


def enviar(tarjeta, sesion, ins: int, cabecera: bytes, datos: bytes = b"", modo: str = PLAIN):
    """Envía un comando con la mensajería indicada.

    Devuelve (datos de respuesta, SW, sesión siguiente o None si se perdió)."""
    if modo == PLAIN:
        cuerpo = datos
    else:
        if modo == FULL and datos:
            datos = sesion.cifrar_comando(datos)
        cuerpo = datos + sesion.mac_comando(ins, cabecera, datos)
    respuesta, sw = a.separar_sw(tarjeta.transmitir(a.nativo(ins, cabecera + cuerpo)))
    if sw != SW_OK:
        return respuesta, sw, None
    siguiente = sesion.avanzada() if sesion is not None else None
    if modo == PLAIN or siguiente is None:
        return respuesta, sw, siguiente
    respuesta, mac = respuesta[:-ev2.TAM_MAC], respuesta[-ev2.TAM_MAC:]
    if mac != siguiente.mac_respuesta(a.OPERATION_OK, respuesta):
        raise AssertionError("MAC de la respuesta incorrecto")
    if modo == FULL and respuesta:
        respuesta = siguiente.descifrar_respuesta(respuesta)
    return respuesta, sw, siguiente


def cabecera_datos(archivo: int, offset: int, longitud: int) -> bytes:
    return bytes([archivo]) + offset.to_bytes(3, "little") + longitud.to_bytes(3, "little")


def seleccionar_aplicacion(tarjeta) -> None:
    _, sw = a.separar_sw(tarjeta.transmitir(a.seleccionar_df(a.DF_APLICACION_NDEF)))
    if sw != 0x9000:
        raise AssertionError(f"ISOSelectFile: SW {sw:04X}")
