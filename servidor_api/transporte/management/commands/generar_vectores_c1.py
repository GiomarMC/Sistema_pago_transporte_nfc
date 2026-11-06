import json
from dataclasses import asdict
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.core.management.base import BaseCommand

from transporte.sync_v2 import paquetes as p

# Claves de prueba fijas: NUNCA se usan fuera de los tests.
CLAVE_HMAC = bytes([0x11]) * 32
VALIDADOR = 101
ESCALAR_ECDSA = int.from_bytes(bytes([0x22]) * 32, "big")
CREADO = 1793000000        # 2026-10-26 (fijo para que los vectores no cambien)


def clave_ecdsa() -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(ESCALAR_ECDSA, ec.SECP256R1())


def _viaje(i: int) -> p.Evento:
    return p.Evento(id_evento=i, tipo=p.VIAJE, operacion=i, fecha=CREADO + 60 * i,
                    id_tarjeta=7, uid=bytes.fromhex("04AABBCCDDEEFF"), monto=-80,
                    saldo_final=1000 - 80 * i, contador=40 + i)


def _eventos_30() -> list[p.Evento]:
    eventos = [_viaje(i) for i in range(1, 29)]
    eventos.append(p.Evento(id_evento=29, tipo=p.INCIDENCIA, operacion=0, fecha=CREADO + 1800,
                            id_tarjeta=0, uid=b"", detalle="lista firma invalida"))
    eventos.append(p.Evento(id_evento=30, tipo=p.FRAUDE, operacion=29, fecha=CREADO + 1860,
                            id_tarjeta=9, uid=bytes.fromhex("A1B2C3D4"), saldo_final=500,
                            recarga_hasta=3, detalle="contador repetido"))
    return eventos


def casos_validos() -> list[tuple[str, str, p.Cabecera, p.Cuerpo]]:
    """(nombre, descripción, cabecera, cuerpo) de cada vector válido."""
    del_bus = dict(origen=VALIDADOR, destino=p.SERVIDOR, creado=CREADO)
    al_bus = dict(origen=p.SERVIDOR, destino=VALIDADOR, creado=CREADO)
    return [
        ("eventos_1", "EVENTOS con un viaje",
         p.Cabecera(p.EVENTOS, secuencia=1, **del_bus), p.Eventos([_viaje(1)])),
        ("eventos_30", "EVENTOS con el máximo de 30 (viajes, incidencia sin UID y fraude)",
         p.Cabecera(p.EVENTOS, secuencia=2, **del_bus), p.Eventos(_eventos_30())),
        ("hola", "HOLA por 4G con 3 eventos pendientes",
         p.Cabecera(p.HOLA, secuencia=3, **del_bus),
         p.Hola(version_listas=103, version_tarifas=CREADO - 86400, ultimo_evento=30,
                pendientes=3, ajenos=1, enlace=p.RED_4G, senal=-40, bytes_tx_hoy=12345,
                bytes_rx_hoy=6789, version_firmware=20261106)),
        ("acuse", "ACUSE hasta el evento 27",
         p.Cabecera(p.ACUSE, secuencia=1, **al_bus),
         p.Acuse(hasta_evento=27, hora_servidor=CREADO)),
        ("listas_normal", "LISTAS 100→103: bloqueo, desbloqueo y recarga pendiente",
         p.Cabecera(p.LISTAS, secuencia=2, **al_bus),
         p.Listas(100, 103, [p.EntradaLista(p.BLOQUEO, p.AGREGAR, a=9),
                             p.EntradaLista(p.BLOQUEO, p.QUITAR, a=4),
                             p.EntradaLista(p.RECARGA, p.PENDIENTE, a=12, b=3, c=2000)])),
        ("listas_urgente", "LISTAS urgente a todos los buses con un bloqueo",
         p.Cabecera(p.LISTAS, origen=p.SERVIDOR, destino=p.TODOS, secuencia=3, creado=CREADO),
         p.Listas(103, 104, [p.EntradaLista(p.BLOQUEO, p.AGREGAR, a=15)], urgente=True)),
        ("listas_foto_completa", "LISTAS desde 0: el validador borra y reconstruye",
         p.Cabecera(p.LISTAS, secuencia=4, **al_bus),
         p.Listas(0, 104, [p.EntradaLista(p.BLOQUEO, p.AGREGAR, a=9),
                           p.EntradaLista(p.BLOQUEO, p.AGREGAR, a=15),
                           p.EntradaLista(p.RECARGA, p.PENDIENTE, a=12, b=3, c=2000)])),
        ("listas_vacia", "LISTAS sin cambios (solo avanza la versión)",
         p.Cabecera(p.LISTAS, secuencia=5, **al_bus), p.Listas(104, 104)),
        ("tarifas", "TARIFAS con tres tarifas",
         p.Cabecera(p.TARIFAS, secuencia=6, **al_bus),
         p.Tarifas(CREADO - 86400, [p.Tarifa(1, 100, "General"), p.Tarifa(2, 50, "Estudiante"),
                                    p.Tarifa(3, 0, "Libre")])),
    ]


def empaquetar(cabecera: p.Cabecera, cuerpo: p.Cuerpo) -> bytes:
    return p.empaquetar(cabecera, cuerpo, clave_hmac=CLAVE_HMAC, clave_ecdsa=clave_ecdsa())


def casos_invalidos() -> list[tuple[str, str, bytes]]:
    """(motivo, descripción, bytes): se parte de un HOLA válido y se rompe un campo."""
    _, _, cabecera, cuerpo = next(c for c in casos_validos() if c[0] == "hola")
    base = empaquetar(cabecera, cuerpo)
    largo = int.from_bytes(base[16:18], "little")
    return [
        ("magia", "magia 'XP' en vez de 'SP'", b"XP" + base[2:]),
        ("version", "versión 2", base[:2] + bytes([2]) + base[3:]),
        ("largo", "el campo largo dice 1 byte más que el cuerpo",
         base[:16] + (largo + 1).to_bytes(2, "little") + base[18:]),
        ("firma", "último byte de la firma alterado", base[:-1] + bytes([base[-1] ^ 0x01])),
    ]


def _cuerpo_en_claro(cuerpo: p.Cuerpo) -> dict:
    return json.loads(json.dumps(asdict(cuerpo), default=lambda b: b.hex()))


def construir_vectores() -> dict:
    publica = clave_ecdsa().public_key().public_bytes(serialization.Encoding.X962,
                                                      serialization.PublicFormat.UncompressedPoint)
    return {
        "contrato": "C1",
        "version": p.VERSION,
        "nota": "Generado con `python manage.py generar_vectores_c1`. Little-endian. "
                "La firma ECDSA es determinista (RFC 6979), así que los bytes se comparan "
                "tal cual. Claves solo de prueba.",
        "claves": {
            "hmac": {"validador": VALIDADOR, "clave": CLAVE_HMAC.hex()},
            "ecdsa_p256": {"privada": f"{ESCALAR_ECDSA:064x}", "publica": publica.hex()},
        },
        "validos": [
            {"nombre": nombre, "descripcion": descripcion, "tipo": type(cuerpo).__name__,
             "cabecera": asdict(cabecera), "cuerpo": _cuerpo_en_claro(cuerpo),
             "hex": empaquetar(cabecera, cuerpo).hex()}
            for nombre, descripcion, cabecera, cuerpo in casos_validos()
        ],
        "invalidos": [
            {"motivo": motivo, "descripcion": descripcion, "hex": datos.hex()}
            for motivo, descripcion, datos in casos_invalidos()
        ],
    }


def ruta_por_defecto() -> Path:
    return settings.BASE_DIR.parent / "docs" / "contratos" / "vectores_c1.json"


class Command(BaseCommand):
    help = "Genera docs/contratos/vectores_c1.json con paquetes C1 de prueba"

    def add_arguments(self, parser):
        parser.add_argument("--salida", type=Path, default=None,
                            help="ruta del JSON (por defecto docs/contratos/vectores_c1.json)")

    def handle(self, salida, **_):
        salida = salida or ruta_por_defecto()
        salida.parent.mkdir(parents=True, exist_ok=True)
        vectores = construir_vectores()
        salida.write_text(json.dumps(vectores, indent=2, ensure_ascii=False) + "\n")
        self.stdout.write(f"{len(vectores['validos'])} válidos y {len(vectores['invalidos'])} "
                          f"inválidos en {salida}")
