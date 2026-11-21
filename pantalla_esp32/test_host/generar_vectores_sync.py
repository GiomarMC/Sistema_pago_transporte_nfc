#!/usr/bin/env python3
"""Genera test_host/vectores_sync.h: paquetes del servidor firmados con una clave de PRUEBA.

Usa el propio código del servidor (servidor_api/transporte/sync_v2/paquetes.py), así que
los bytes son los que el servidor produciría. La firma ECDSA es determinista (RFC 6979):
volver a ejecutarlo da el mismo archivo. La clave privada es solo de pruebas.

    python3 pantalla_esp32/test_host/generar_vectores_sync.py
"""
import importlib.util
import pathlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

RAIZ = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("paquetes", RAIZ / "servidor_api/transporte/sync_v2/paquetes.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

CLAVE_PRIVADA = ec.derive_private_key(0x5A5A_1234_5678_9ABC_DEF0_1122_3344_5566_7788_99AA_BBCC_DDEE_FF00_1357_2468, ec.SECP256R1())
PUBLICA = CLAVE_PRIVADA.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
CLAVE_HMAC_107 = bytes((0x10 + i) & 0xFF for i in range(32))
HORA = 1_795_000_000  # unix: 2026-11-20


def servidor(tipo, destino, cuerpo, secuencia):
    cab = P.Cabecera(tipo, origen=P.SERVIDOR, destino=destino, secuencia=secuencia, creado=HORA)
    return P.empaquetar(cab, cuerpo, clave_ecdsa=CLAVE_PRIVADA)


def en_c(nombre, datos):
    filas = ",\n    ".join(", ".join(f"0x{b:02X}" for b in datos[i:i + 16]) for i in range(0, len(datos), 16))
    return f"static const uint8_t {nombre}[{len(datos)}] = {{\n    {filas}}};\n"


salida = ["// Generado por generar_vectores_sync.py: no editar a mano.\n#pragma once\n#include <stdint.h>\n\n"]
salida.append("// Clave pública ECDSA del servidor de PRUEBA y clave HMAC del validador 107 de PRUEBA\n")
salida.append(en_c("CLAVE_PUBLICA_PRUEBA", PUBLICA))
salida.append(en_c("CLAVE_HMAC_107", CLAVE_HMAC_107))
salida.append(f"static const uint32_t HORA_SERVIDOR_PRUEBA = {HORA};\n\n")

seq = 1000
for hasta in (30, 60, 90, 100, 130):
    salida.append(en_c(f"ACUSE_107_HASTA_{hasta}", servidor(P.ACUSE, 107, P.Acuse(hasta, HORA), seq)))
    seq += 1
salida.append(en_c("ACUSE_105_HASTA_12", servidor(P.ACUSE, 105, P.Acuse(12, HORA), seq)))
seq += 1
# Mismos eventos, otra hora: uno posterior (el servidor siguió su camino) y uno viejo (un reenvío malicioso)
salida.append(en_c("ACUSE_107_HASTA_60_100S_DESPUES", servidor(P.ACUSE, 107, P.Acuse(60, HORA + 100), seq)))
seq += 1
salida.append(en_c("ACUSE_107_HASTA_60_1H_ANTES", servidor(P.ACUSE, 107, P.Acuse(60, HORA - 3600), seq)))
seq += 1

listas = P.Listas(0x0A, 0x0C, (P.EntradaLista(P.BLOQUEO, P.AGREGAR, 4242), P.EntradaLista(P.RECARGA, P.PENDIENTE, 77, 3, 500)))
salida.append(en_c("LISTAS_107", servidor(P.LISTAS, 107, listas, seq)))
seq += 1
tarifas = P.Tarifas(HORA - 86400, (P.Tarifa(1, 130, "general"), P.Tarifa(2, 80, "estudiante")))
salida.append(en_c("TARIFAS_107", servidor(P.TARIFAS, 107, tarifas, seq)))

malo = bytearray(servidor(P.ACUSE, 107, P.Acuse(60, HORA), seq + 1))
malo[-1] ^= 0x01  # un bit de la firma cambiado
salida.append(en_c("ACUSE_107_FIRMA_ROTA", bytes(malo)))

(pathlib.Path(__file__).parent / "vectores_sync.h").write_text("".join(salida), encoding="utf-8")
print("vectores_sync.h generado:", len("".join(salida)), "caracteres")
