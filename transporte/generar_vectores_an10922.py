#!/usr/bin/env python3
"""Genera docs/contratos/vectores_an10922.json (M1-05, #39) para el firmware (M2-04, #47) y el servidor.

Determinista: las mismas entradas dan el mismo archivo byte a byte (sin fechas ni azar). Las claves son solo de
prueba. Secciones:
  - cmac:      RFC 4493, sección 4 (CMAC estándar con la clave 2b7e1516…)
  - oficiales: el ejemplo de AN10922 §2.2.1 (AES-128)
  - propios:   el contrato de SubePe para cada ranura, versión y UID de prueba
  - invalidos: entradas que deben rechazarse, con el motivo

    cd transporte && python3 generar_vectores_an10922.py            # reescribe el archivo
    cd transporte && python3 generar_vectores_an10922.py --comprobar  # falla si no coincide
"""

import argparse
import json
import sys
from pathlib import Path

from tarjeta import an10922

SALIDA = Path(__file__).resolve().parents[1] / "docs" / "contratos" / "vectores_an10922.json"
H = bytes.fromhex

_RFC_CLAVE = "2b7e151628aed2a6abf7158809cf4f3c"
_RFC_MENSAJE = ("6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e51"
                "30c81c46a35ce411e5fbc1191a0a52eff69f2445df4f9b17ad2b417be66c3710")
_RFC_ESPERADO = {0: "bb1d6929e95937287fa37d129b756746", 16: "070a16b46b4d4144f79bdd9dd04a287c",
                 40: "dfa66747de9ae63030ca32611497c827", 64: "51f0bebf7e3b9d92fc49741779363cfe"}

# AN10922 §2.2.1: maestra 00…FF, UID 04782E21801D80, AID 3042F5, SystemIdentifier "NXP Abu"
_OFICIAL_M = "04782e21801d80" + "3042f5" + b"NXP Abu".hex()
_OFICIAL_K = "a8dd63a3b89d54b37ca802473fda9175"

UIDS = ("04b3e121ca2a81", "04653e22ca2a81", "04112233445566")
MAESTRAS = {0: "00" * 16, 1: "22" * 16, 2: "11" * 16, 4: "44" * 16}  # solo pruebas, una por ranura


def vectores() -> dict:
    cmac = [{"clave": _RFC_CLAVE, "mensaje": _RFC_MENSAJE[:2 * n], "cmac": esperado}
            for n, esperado in _RFC_ESPERADO.items()]
    for v in cmac:
        assert an10922.cmac_aes128(H(v["clave"]), H(v["mensaje"])).hex() == v["cmac"], v
    oficial = an10922.an10922_aes128(H("00112233445566778899aabbccddeeff"), H(_OFICIAL_M)).hex()
    assert oficial == _OFICIAL_K, oficial

    propios = []
    for uid in UIDS:
        for ranura, maestra in MAESTRAS.items():
            for version in (1, 2, 0xFF):
                m = an10922.entrada_m(H(uid), ranura, version)
                k = an10922.diversificar_aes128(H(maestra), H(uid), ranura, version)
                nombre = f"k{ranura}_v{version}" + ("" if uid == UIDS[0] else f"_{uid}")
                propios.append({"nombre": nombre, "maestra": maestra, "uid": uid, "ranura": ranura,
                                "version": version, "m": m.hex(), "clave": k.hex()})
    invalidos = [
        {"motivo": "ranura 3 (K3 no se diversifica)", "maestra": MAESTRAS[1], "uid": UIDS[0],
         "ranura": 3, "version": 1},
        {"motivo": "ranura 5 inexistente", "maestra": MAESTRAS[1], "uid": UIDS[0], "ranura": 5, "version": 1},
        {"motivo": "versión 00h (clave de fábrica)", "maestra": MAESTRAS[1], "uid": UIDS[0], "ranura": 1, "version": 0},
        {"motivo": "versión mayor que FFh", "maestra": MAESTRAS[1], "uid": UIDS[0], "ranura": 1, "version": 256},
        {"motivo": "UID de 4 bytes", "maestra": MAESTRAS[1], "uid": "04b3e121", "ranura": 1, "version": 1},
        {"motivo": "UID de 8 bytes", "maestra": MAESTRAS[1], "uid": UIDS[0] + "00", "ranura": 1, "version": 1},
        {"motivo": "maestra de 15 bytes", "maestra": "22" * 15, "uid": UIDS[0], "ranura": 1, "version": 1},
        {"motivo": "maestra de 24 bytes (AES-192)", "maestra": "22" * 24, "uid": UIDS[0], "ranura": 1, "version": 1},
    ]
    for v in invalidos:
        try:
            an10922.diversificar_aes128(H(v["maestra"]), H(v["uid"]), v["ranura"], v["version"])
        except ValueError:
            continue
        raise AssertionError(f"debía rechazarse: {v['motivo']}")
    return {
        "descripcion": ("Diversificación AN10922 de SubePe (M1-05, #39). M = UID(7) || 'SubePe' || ranura || "
                        "versión || 00 (16 B); K = AES-CMAC(maestra, 01 || M). Claves solo de prueba."),
        "cmac": cmac,
        "oficiales": [{"fuente": "AN10922 §2.2.1 (AES-128)", "maestra": "00112233445566778899aabbccddeeff",
                       "m": _OFICIAL_M, "clave": _OFICIAL_K}],
        "propios": propios,
        "invalidos": invalidos,
    }


def texto() -> str:
    return json.dumps(vectores(), indent=2, ensure_ascii=False) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--comprobar", action="store_true", help="no escribe: falla si el archivo no coincide")
    args = ap.parse_args()
    nuevo = texto()
    if args.comprobar:
        if not SALIDA.exists() or SALIDA.read_text(encoding="utf-8") != nuevo:
            sys.exit(f"{SALIDA} no coincide con el generador")
        print(f"{SALIDA.name}: coincide")
        return
    SALIDA.write_text(nuevo, encoding="utf-8")
    print(f"Escrito {SALIDA} ({len(nuevo)} bytes)")


if __name__ == "__main__":
    main()
