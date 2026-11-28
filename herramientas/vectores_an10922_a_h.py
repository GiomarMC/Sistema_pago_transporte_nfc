"""
Convierte docs/contratos/vectores_an10922.json (lo genera transporte/generar_vectores_an10922.py, M1-05) en
pantalla_esp32/test/test_claves424/vectores.h, para que el firmware (M2-04, #47) compare sus claves con las de Python.

Uso (desde la raíz del repositorio):
    python3 herramientas/vectores_an10922_a_h.py               # escribe vectores.h
    python3 herramientas/vectores_an10922_a_h.py --comprobar   # falla si vectores.h no coincide con el JSON
"""

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ENTRADA = RAIZ / "docs" / "contratos" / "vectores_an10922.json"
SALIDA = RAIZ / "pantalla_esp32" / "test" / "test_claves424" / "vectores.h"

ERRORES = {  # motivo de los inválidos del JSON -> tt::ErrorClave esperado
    "ranura": "RANURA",
    "versión": "VERSION",
    "UID": "UID",
    "maestra": "MAESTRA",
}


def bytes_c(nombre: str, datos: bytes) -> str:
    cuerpo = ", ".join(f"0x{b:02X}" for b in datos)
    # Un arreglo de 0 bytes no es C++ válido: se reserva 1 y el largo va aparte
    return f"const uint8_t {nombre}[{max(len(datos), 1)}] = {{{cuerpo or '0x00'}}};"


def error_de(motivo: str) -> str:
    for palabra, error in ERRORES.items():
        if motivo.startswith(palabra):
            return error
    raise ValueError(f"motivo de inválido sin ErrorClave: {motivo!r}")


def generar(v: dict) -> str:
    lineas = [
        "// Vectores de AN10922 (M1-05, #39) generados por la implementación Python con claves de PRUEBA.",
        "// Generado automáticamente por herramientas/vectores_an10922_a_h.py: no editar a mano.",
        "#pragma once",
        "#include <stddef.h>",
        "#include <stdint.h>",
        "",
        "namespace vectores {",
        "",
        "struct Cmac { const uint8_t *clave, *mensaje; size_t largo; const uint8_t *mac; };",
        "struct Oficial { const char *fuente; const uint8_t *maestra, *m; size_t largoM; const uint8_t *clave; };",
        "struct Propio {",
        "  const char *nombre;",
        "  const uint8_t *maestra, *uid;",
        "  uint8_t ranura, version;",
        "  const uint8_t *m, *clave;",
        "};",
        "// `error` es el nombre del valor de tt::ErrorClave que debe devolverse",
        "struct Invalido {",
        "  const char *motivo, *error;",
        "  const uint8_t *maestra;",
        "  size_t largoMaestra;",
        "  const uint8_t *uid;",
        "  size_t largoUid;",
        "  uint8_t ranura;",
        "  unsigned version;",
        "};",
        "",
    ]
    filas = {"cmac": [], "oficiales": [], "propios": [], "invalidos": []}

    for i, c in enumerate(v["cmac"]):
        clave, msg, mac = (bytes.fromhex(c[k]) for k in ("clave", "mensaje", "cmac"))
        lineas += [bytes_c(f"CMAC_{i}_CLAVE", clave), bytes_c(f"CMAC_{i}_MSG", msg), bytes_c(f"CMAC_{i}_MAC", mac)]
        filas["cmac"].append(f"    {{CMAC_{i}_CLAVE, CMAC_{i}_MSG, {len(msg)}, CMAC_{i}_MAC}},")

    for i, o in enumerate(v["oficiales"]):
        maestra, m, clave = (bytes.fromhex(o[k]) for k in ("maestra", "m", "clave"))
        lineas += [f"// {o['fuente']}", bytes_c(f"OFICIAL_{i}_MAESTRA", maestra), bytes_c(f"OFICIAL_{i}_M", m),
                   bytes_c(f"OFICIAL_{i}_CLAVE", clave)]
        filas["oficiales"].append(f'    {{"{o["fuente"]}", OFICIAL_{i}_MAESTRA, OFICIAL_{i}_M, {len(m)}, '
                                  f"OFICIAL_{i}_CLAVE}},")

    for i, p in enumerate(v["propios"]):
        maestra, uid, m, clave = (bytes.fromhex(p[k]) for k in ("maestra", "uid", "m", "clave"))
        lineas += [bytes_c(f"PROPIO_{i}_MAESTRA", maestra), bytes_c(f"PROPIO_{i}_UID", uid),
                   bytes_c(f"PROPIO_{i}_M", m), bytes_c(f"PROPIO_{i}_CLAVE", clave)]
        filas["propios"].append(f'    {{"{p["nombre"]}", PROPIO_{i}_MAESTRA, PROPIO_{i}_UID, {p["ranura"]}, '
                                f"{p['version']}, PROPIO_{i}_M, PROPIO_{i}_CLAVE}},")

    for i, x in enumerate(v["invalidos"]):
        maestra, uid = bytes.fromhex(x["maestra"]), bytes.fromhex(x["uid"])
        lineas += [bytes_c(f"INVALIDO_{i}_MAESTRA", maestra), bytes_c(f"INVALIDO_{i}_UID", uid)]
        filas["invalidos"].append(f'    {{"{x["motivo"]}", "{error_de(x["motivo"])}", INVALIDO_{i}_MAESTRA, '
                                  f"{len(maestra)}, INVALIDO_{i}_UID, {len(uid)}, {x['ranura']}, {x['version']}}},")

    for nombre, tipo, clave in (("CMACS", "Cmac", "cmac"), ("OFICIALES", "Oficial", "oficiales"),
                                ("PROPIOS", "Propio", "propios"), ("INVALIDOS", "Invalido", "invalidos")):
        lineas += ["", f"const {tipo} {nombre}[] = {{", *filas[clave], "};",
                   f"const size_t N_{nombre} = sizeof({nombre}) / sizeof({nombre}[0]);"]
    lineas += ["", "}  // namespace vectores", ""]
    return "\n".join(lineas)


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--entrada", type=Path, default=ENTRADA)
    parser.add_argument("--salida", type=Path, default=SALIDA)
    parser.add_argument("--comprobar", action="store_true", help="no escribe: falla si la salida no coincide")
    args = parser.parse_args()
    vectores = json.loads(args.entrada.read_text(encoding="utf-8"))
    texto = generar(vectores)
    if args.comprobar:
        if not args.salida.exists() or args.salida.read_text(encoding="utf-8") != texto:
            sys.exit(f"{args.salida} no coincide con {args.entrada}: vuelve a generarlo")
        print(f"{args.salida} coincide con {args.entrada}")
        return
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(texto, encoding="utf-8")
    print(f"{len(vectores['cmac'])} CMAC, {len(vectores['oficiales'])} oficiales, {len(vectores['propios'])} propios "
          f"y {len(vectores['invalidos'])} inválidos en {args.salida}")


if __name__ == "__main__":
    main()
