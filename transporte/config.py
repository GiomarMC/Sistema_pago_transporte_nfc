"""Configuración general del sistema de transporte."""

from pathlib import Path

RAIZ = Path(__file__).resolve().parent
RUTA_CLAVE_MAESTRA = RAIZ / "claves" / "clave_maestra.bin"

# Tarifas: código guardado en la tarjeta -> (nombre, precio en céntimos de sol).
# Las vigentes las define el servidor; estas solo se usan si un validador aún no
# ha sincronizado nunca.
TARIFAS = {
    1: ("general", 130),     # S/ 1.30
    2: ("estudiante", 80),   # S/ 0.80
}
TARIFA_POR_NOMBRE = {nombre: codigo for codigo, (nombre, _) in TARIFAS.items()}

# Identificador de validador que se registra cuando la operación la hace el emisor
VALIDADOR_EMISOR = 0
# Identificador del punto de recarga (se graba en la tarjeta como "validador")
PUNTO_RECARGA = 900


def formato_soles(centimos: int) -> str:
    signo = "-" if centimos < 0 else ""
    centimos = abs(centimos)
    return f"{signo}S/ {centimos // 100}.{centimos % 100:02d}"


def parsear_soles(texto: str) -> int:
    """'10', '10.5', '10.50', 'S/ 10.50' -> 1050 céntimos."""
    t = texto.replace("S/", "").replace(",", ".").strip()
    if not t:
        raise ValueError("monto vacío")
    entero, _, dec = t.partition(".")
    if not entero.isdigit() or (dec and not dec.isdigit()) or len(dec) > 2:
        raise ValueError(f"monto inválido: {texto!r} (usa p. ej. 10.50)")
    return int(entero) * 100 + int(dec.ljust(2, "0") or 0)
