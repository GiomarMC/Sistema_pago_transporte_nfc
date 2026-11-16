"""Credenciales MQTT de la sincronización v2 (M9-05, C1 5.8).

Usuario MQTT = número de validador; contraseña = una aleatoria que se graba en la
ESP32. El servidor guarda solo su hash, en el formato del password_file de
Mosquitto 2 ($7$: PBKDF2-HMAC-SHA512), así el archivo de contraseñas se arma
desde la base de datos sin que la contraseña en claro esté guardada en ningún lado.
"""

import base64
import hashlib
import hmac
import secrets

ITERACIONES = 1000      # las mismas que usa mosquitto_passwd
USUARIO_PUENTE = "puente"


def nueva_clave() -> str:
    """Contraseña MQTT de un validador: 32 caracteres, sin símbolos raros para la NVS."""
    return secrets.token_urlsafe(24)


def hash_mosquitto(clave: str, sal: bytes | None = None, iteraciones: int = ITERACIONES) -> str:
    sal = sal if sal is not None else secrets.token_bytes(64)
    digest = hashlib.pbkdf2_hmac("sha512", clave.encode(), sal, iteraciones, 64)
    return (f"$7${iteraciones}${base64.b64encode(sal).decode()}"
            f"${base64.b64encode(digest).decode()}")


def verificar(clave: str, hash_guardado: str) -> bool:
    try:
        _, siete, iteraciones, sal, digest = hash_guardado.split("$")
        if siete != "7":
            return False
        esperado = hashlib.pbkdf2_hmac("sha512", clave.encode(), base64.b64decode(sal),
                                       int(iteraciones), 64)
        return hmac.compare_digest(esperado, base64.b64decode(digest))
    except ValueError:
        return False


def archivo_contrasenas(clave_puente: str) -> str:
    """Contenido del password_file: el puente y cada validador activo con clave MQTT."""
    from transporte.models import Validador

    lineas = []
    if clave_puente:
        lineas.append(f"{USUARIO_PUENTE}:{hash_mosquitto(clave_puente)}")
    for numero, hash_guardado in Validador.objects.filter(activo=True).exclude(mqtt_clave="") \
            .order_by("numero").values_list("numero", "mqtt_clave"):
        lineas.append(f"{numero}:{hash_guardado}")
    return "".join(f"{linea}\n" for linea in lineas)
