"""
Convierte docs/contratos/vectores_c1.json (lo genera el servidor con
`python manage.py generar_vectores_c1`) en pantalla_esp32/test/test_protocolo/vectores.h,
para que el test de la ESP32 compare sus bytes con los de Python.

Uso (desde la raíz del repositorio):  python3 herramientas/vectores_a_h.py
"""

import argparse
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ENTRADA = RAIZ / "docs" / "contratos" / "vectores_c1.json"
SALIDA = RAIZ / "pantalla_esp32" / "test" / "test_protocolo" / "vectores.h"


def arreglo(nombre: str, datos: bytes) -> str:
    filas = [", ".join(f"0x{b:02X}" for b in datos[i:i + 16]) for i in range(0, len(datos), 16)]
    cuerpo = ",\n    ".join(filas)
    return f"const uint8_t {nombre}[{len(datos)}] = {{\n    {cuerpo}}};"


def generar(vectores: dict) -> str:
    claves = vectores["claves"]
    lineas = [
        "// Vectores del contrato C1 generados por la biblioteca Python (M0-01) con claves de",
        "// PRUEBA. Generado automáticamente por herramientas/vectores_a_h.py: no editar a mano.",
        "#pragma once",
        "#include <stddef.h>",
        "#include <stdint.h>",
        "",
        "namespace vectores {",
        "",
        "struct Valido {",
        "  const char *nombre;",
        "  uint8_t tipo;",
        "  uint16_t origen, destino;",
        "  uint32_t secuencia, creado;",
        "  const uint8_t *datos;",
        "  size_t largo;",
        "};",
        "",
        "struct Invalido {",
        "  const char *motivo;",
        "  const uint8_t *datos;",
        "  size_t largo;",
        "};",
        "",
        f"const uint16_t VALIDADOR = {claves['hmac']['validador']};",
        arreglo("CLAVE_HMAC", bytes.fromhex(claves["hmac"]["clave"])),
        arreglo("CLAVE_PUBLICA", bytes.fromhex(claves["ecdsa_p256"]["publica"])),
        "",
    ]
    validos, invalidos = [], []
    for i, v in enumerate(vectores["validos"]):
        nombre = f"VALIDO_{i}"
        lineas += [f"// {v['nombre']}: {v['descripcion']}", arreglo(nombre, bytes.fromhex(v["hex"]))]
        c = v["cabecera"]
        validos.append(f'    {{"{v["nombre"]}", 0x{c["tipo"]:02X}, {c["origen"]}, {c["destino"]}, '
                       f'{c["secuencia"]}, {c["creado"]}, {nombre}, sizeof({nombre})}},')
    for i, v in enumerate(vectores["invalidos"]):
        nombre = f"INVALIDO_{i}"
        lineas += [f"// {v['motivo']}: {v['descripcion']}", arreglo(nombre, bytes.fromhex(v["hex"]))]
        invalidos.append(f'    {{"{v["motivo"]}", {nombre}, sizeof({nombre})}},')
    lineas += [
        "",
        "const Valido VALIDOS[] = {", *validos, "};",
        "const size_t N_VALIDOS = sizeof(VALIDOS) / sizeof(VALIDOS[0]);",
        "",
        "const Invalido INVALIDOS[] = {", *invalidos, "};",
        "const size_t N_INVALIDOS = sizeof(INVALIDOS) / sizeof(INVALIDOS[0]);",
        "",
        "}  // namespace vectores",
        "",
    ]
    return "\n".join(lineas)


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--entrada", type=Path, default=ENTRADA)
    parser.add_argument("--salida", type=Path, default=SALIDA)
    args = parser.parse_args()
    vectores = json.loads(args.entrada.read_text())
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(generar(vectores))
    print(f"{len(vectores['validos'])} válidos y {len(vectores['invalidos'])} inválidos "
          f"en {args.salida}")


if __name__ == "__main__":
    main()
