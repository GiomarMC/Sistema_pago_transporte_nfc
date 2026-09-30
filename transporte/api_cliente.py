"""
Cliente de la API del servidor (solo biblioteca estándar).

La configuración está en transporte/api.json (no compartir: contiene tokens):
{
  "url": "http://127.0.0.1:8000",
  "token_operador": "...",                 # emisión, recargas, bloqueos (PC de atención)
  "validadores": {"101": "...", "102": "..."}  # un token por bus; en un bus real, solo el suyo
}
"""

import json
import urllib.error
import urllib.request

from config import RAIZ

RUTA_CONF = RAIZ / "api.json"


class ErrorAPI(Exception):
    """El servidor respondió, pero rechazó la operación."""

    def __init__(self, estado, mensaje):
        super().__init__(mensaje)
        self.estado = estado


class SinConexion(Exception):
    """No se pudo contactar con el servidor."""


def _conf():
    if not RUTA_CONF.exists():
        raise SystemExit(f"Falta {RUTA_CONF} con la URL del servidor y los tokens "
                         "(ver api_cliente.py)")
    return json.loads(RUTA_CONF.read_text())


def token_operador():
    return _conf()["token_operador"]


def token_validador(numero):
    token = _conf().get("validadores", {}).get(str(numero))
    if not token:
        raise SystemExit(f"No hay token para el validador {numero} en {RUTA_CONF}")
    return token


def _mensaje(cuerpo):
    try:
        datos = json.loads(cuerpo)
    except ValueError:
        return cuerpo.decode(errors="replace")[:200]
    if isinstance(datos, dict):
        for clave in ("error", "detail"):
            if clave in datos:
                return str(datos[clave])
        if "non_field_errors" in datos:
            return "; ".join(datos["non_field_errors"])
    return json.dumps(datos, ensure_ascii=False)


def llamar(metodo, ruta, datos=None, token=None, timeout=5):
    """Hace la petición y devuelve el JSON de respuesta (o None si no hay cuerpo)."""
    req = urllib.request.Request(
        _conf()["url"].rstrip("/") + ruta, method=metodo,
        data=None if datos is None else json.dumps(datos).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "Authorization": f"Token {token or token_operador()}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            cuerpo = resp.read()
            return json.loads(cuerpo) if cuerpo else None
    except urllib.error.HTTPError as e:
        raise ErrorAPI(e.code, _mensaje(e.read())) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise SinConexion(str(getattr(e, "reason", e))) from None
