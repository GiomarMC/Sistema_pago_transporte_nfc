#!/usr/bin/env python3
"""Genera test_host/vectores_listas.h: LISTAS y TARIFAS del servidor firmados con la clave de PRUEBA (M4-02).

Es la misma clave que usa generar_vectores_sync.py (la pública está en vectores_sync.h), y el mismo código
del servidor (servidor_api/transporte/sync_v2/paquetes.py). Sirven para lo que los vectores del contrato no
traen: una foto completa en tres paquetes encadenados, paquetes que saltan o repiten versiones, urgentes que
ya no hacen falta, tarifas viejas... La firma ECDSA es determinista: volver a ejecutarlo da el mismo archivo.

    python3 pantalla_esp32/test_host/generar_vectores_listas.py
"""
import importlib.util
import pathlib

from cryptography.hazmat.primitives.asymmetric import ec

RAIZ = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("paquetes", RAIZ / "servidor_api/transporte/sync_v2/paquetes.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

CLAVE_PRIVADA = ec.derive_private_key(0x5A5A_1234_5678_9ABC_DEF0_1122_3344_5566_7788_99AA_BBCC_DDEE_FF00_1357_2468, ec.SECP256R1())
HORA = 1_795_000_000
VALIDADOR = 107
TODOS = 0xFFFF


def servidor(tipo, destino, cuerpo, secuencia):
    cab = P.Cabecera(tipo, origen=P.SERVIDOR, destino=destino, secuencia=secuencia, creado=HORA)
    return P.empaquetar(cab, cuerpo, clave_ecdsa=CLAVE_PRIVADA)


def en_c(nombre, datos):
    filas = ",\n    ".join(", ".join(f"0x{b:02X}" for b in datos[i:i + 16]) for i in range(0, len(datos), 16))
    return f"static const uint8_t {nombre}[{len(datos)}] = {{\n    {filas}}};\n"


def bloqueo(i, op=P.AGREGAR):
    return P.EntradaLista(P.BLOQUEO, op, i)


def recarga(cuenta, seq, monto, op=P.PENDIENTE):
    return P.EntradaLista(P.RECARGA, op, cuenta, seq, monto)


ACTUAL = 200
BASE = 0x8000_0000 | ((ACTUAL & 0xFFFF) << 15)  # versiones intermedias de una foto: nunca una versión real

salida = ["// Generado por generar_vectores_listas.py: no editar a mano.\n#pragma once\n#include <stdint.h>\n\n"]
salida.append("// Versiones: la foto llega a 200; las intermedias son BASE + 1 y BASE + 2\n")
salida.append(f"static const uint32_t FOTO_INTERMEDIA_1 = {BASE + 1}u, FOTO_INTERMEDIA_2 = {BASE + 2}u;\n\n")

paquetes = [
    # foto completa en tres paquetes encadenados: 0 -> BASE+1 -> BASE+2 -> 200
    ("FOTO_1", VALIDADOR, P.Listas(0, BASE + 1, (bloqueo(1000), bloqueo(1001), bloqueo(1002)))),
    ("FOTO_2", VALIDADOR, P.Listas(BASE + 1, BASE + 2, (bloqueo(1003), bloqueo(1004)))),
    ("FOTO_3", VALIDADOR, P.Listas(BASE + 2, ACTUAL, (bloqueo(1005), recarga(50, 1, 300), recarga(50, 2, 400)))),
    # cambios en orden a partir de 200
    ("N1_200_205", VALIDADOR, P.Listas(200, 205, (bloqueo(2000), bloqueo(1001, P.QUITAR), recarga(60, 1, 100)))),
    ("N2_205_210", VALIDADOR, P.Listas(205, 210, (bloqueo(2001), recarga(60, 2, 150), recarga(50, 1, 0, P.APLICADA)))),
    ("N3_205_212", VALIDADOR, P.Listas(205, 212, (bloqueo(3000), bloqueo(3002)))),
    ("N4_212_214", VALIDADOR, P.Listas(212, 214, (bloqueo(3001, P.QUITAR), bloqueo(3003)))),
    ("N_PARA_108", 108, P.Listas(200, 205, (bloqueo(2000),))),
    ("N_HASTA_MENOR", VALIDADOR, P.Listas(210, 205, ())),
    # urgentes (a todos los buses, 1 entrada): version_desde = version - 1
    ("U_3000_212", TODOS, P.Listas(211, 212, (bloqueo(3000),), urgente=True)),
    ("U_3001_213", TODOS, P.Listas(212, 213, (bloqueo(3001),), urgente=True)),
    ("U_4000_VIEJO", TODOS, P.Listas(202, 203, (bloqueo(4000),), urgente=True)),
    ("U_DESBLOQUEO_5000", TODOS, P.Listas(300, 301, (bloqueo(5000, P.QUITAR),), urgente=True)),
]
seq = 2000
for nombre, destino, cuerpo in paquetes:
    salida.append(en_c(f"LISTAS_{nombre}", servidor(P.LISTAS, destino, cuerpo, seq)))
    seq += 1

tarifas = [
    ("A", HORA - 86400, ((1, 130, "general"), (2, 80, "estudiante"))),
    ("B", HORA, ((1, 150, "general"), (2, 80, "estudiante"), (3, 0, "libre"))),
    ("VIEJA", HORA - 2 * 86400, ((1, 90, "general"),)),
]
for nombre, version, filas in tarifas:
    cuerpo = P.Tarifas(version, tuple(P.Tarifa(c, p, n) for c, p, n in filas))
    salida.append(en_c(f"TARIFAS_{nombre}", servidor(P.TARIFAS, VALIDADOR, cuerpo, seq)))
    seq += 1
salida.append(f"static const uint32_t TARIFAS_A_VERSION = {HORA - 86400}u, TARIFAS_B_VERSION = {HORA}u, TARIFAS_VIEJA_VERSION = {HORA - 2 * 86400}u;\n")

# ACUSE para un segundo validador (108), para las pruebas de intercambio entre buses (M8-03)
for hasta in (30,):
    salida.append(en_c(f"ACUSE_108_HASTA_{hasta}", servidor(P.ACUSE, 108, P.Acuse(hasta, HORA), seq)))
    seq += 1

(pathlib.Path(__file__).parent / "vectores_listas.h").write_text("".join(salida), encoding="utf-8")
print("vectores_listas.h generado:", len("".join(salida)), "caracteres")
