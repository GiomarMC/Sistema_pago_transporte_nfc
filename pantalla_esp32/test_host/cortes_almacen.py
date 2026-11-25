#!/usr/bin/env python3
"""Cortes de energía repetidos sobre la microSD real (M3-01/02), con el programa prueba_almacen.

    pio run -e prueba_almacen -t upload
    python3 pantalla_esp32/test_host/cortes_almacen.py --puerto /dev/ttyUSB0 --cortes 20

Reinicia la placa por el puerto serie (pulso en EN) en un instante al azar, a veces en pleno cobro, y en
cada arranque comprueba que:
  - ultimoId >= el último "C <id>" que se vio confirmado (ningún cobro confirmado se pierde);
  - la verificación de integridad sale OK (contenido igual al escrito, ids sin huecos);
  - la secuencia de paquetes que entrega sigue creciendo y nunca repite una anterior.
Un reinicio por EN no corta la alimentación de la tarjeta: es una prueba exigente de lo que el
programa deja escrito, pero un corte real (desenchufar el USB en pleno bucle) es aún más duro.
"""
import argparse
import random
import re
import sys
import time

sys.path.insert(0, "/home/giomarmc/.local/share/uv/tools/platformio/lib/python3.13/site-packages")
import serial  # noqa: E402


def leer_hasta(s, limite_s, condicion=None):
    """Líneas recibidas durante `limite_s` s, o hasta que condicion(lineas) sea cierta."""
    lineas, buf, t0 = [], b"", time.time()
    while time.time() - t0 < limite_s:
        buf += s.read(512)
        while b"\n" in buf:
            l, buf = buf.split(b"\n", 1)
            txt = l.decode("utf-8", "replace").strip()
            if txt:
                lineas.append(txt)
        if condicion and condicion(lineas):
            break
    return lineas


def reiniciar(s):
    s.dtr = False
    s.rts = True
    time.sleep(0.15)
    s.rts = False
    s.reset_input_buffer()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--puerto", default="/dev/ttyUSB0")
    ap.add_argument("--cortes", type=int, default=20)
    ap.add_argument("--semilla", type=int, default=7)
    a = ap.parse_args()
    random.seed(a.semilla)
    s = serial.Serial(a.puerto, 115200, timeout=0.3)

    # Empezar de cero: se espera a que el programa esté en su bucle y se le manda la B
    reiniciar(s)
    leer_hasta(s, 30, lambda ls: any(l == "LISTO" for l in ls))
    s.write(b"B")
    leer_hasta(s, 10, lambda ls: any("ARRANQUE estado nuevo" in l for l in ls))
    print("Datos borrados; empieza la prueba de cortes\n")

    ultimo_confirmado = 0
    mayor_secuencia = 0
    fallos, incidencias, total_confirmados, fallos_montaje = [], 0, 0, 0
    print(f"{'corte':>5} {'tras':>6} {'último C visto':>15} {'ultimoId al arrancar':>21} {'integridad':>11} {'secuencia':>10}")
    for corte in range(1, a.cortes + 1):
        # Una vida del programa: arranca, y a los pocos segundos (al azar) se corta
        espera = random.uniform(0.6, 9.0)
        lineas = leer_hasta(s, espera)
        for l in lineas:
            m = re.fullmatch(r"C (\d+)", l)
            if m:
                ultimo_confirmado = max(ultimo_confirmado, int(m.group(1)))
                total_confirmados += 1
        reiniciar(s)  # <- el corte
        # El arranque siguiente. Tras un reinicio brusco la microSD, que sigue alimentada, a veces no
        # contesta al montarla ("Card Failed! cmd: 0x00"): no es un fallo del almacenamiento. Se cuenta
        # aparte y se reinicia otra vez, hasta que monte.
        for intento in range(4):
            lineas = leer_hasta(s, 25, lambda ls: (any(l.startswith("INTEGRIDAD") for l in ls) and
                                                   any(l.startswith("TIEMPOS siguienteSecuencia") for l in ls)) or
                                any("microSD no disponible" in l for l in ls))
            if not any("microSD no disponible" in l for l in lineas):
                break
            fallos_montaje += 1
            reiniciar(s)
        arranque = next((l for l in lineas if l.startswith("ARRANQUE")), "")
        integr = next((l for l in lineas if l.startswith("INTEGRIDAD")), "")
        secu = next((l for l in lineas if l.startswith("TIEMPOS siguienteSecuencia")), "")
        incidencias += sum(1 for l in lineas if l.startswith("INCIDENCIA"))
        mu = re.search(r"ultimoId (\d+)", arranque)
        ultimo_id = int(mu.group(1)) if mu else -1
        ok_integridad = integr.rstrip().endswith("OK")
        ms = re.search(r"\(secuencia (\d+)\)", secu)
        secuencia = int(ms.group(1)) if ms else -1
        problema = []
        if ultimo_id < ultimo_confirmado:
            problema.append(f"SE PERDIERON COBROS: ultimoId {ultimo_id} < confirmado {ultimo_confirmado}")
        if not ok_integridad:
            problema.append(f"INTEGRIDAD: {integr or 'sin respuesta'}")
        if secuencia <= mayor_secuencia:
            problema.append(f"SECUENCIA REPETIDA o sin dato: {secuencia} <= {mayor_secuencia}")
        mayor_secuencia = max(mayor_secuencia, secuencia)
        print(f"{corte:>5} {espera:>5.1f}s {ultimo_confirmado:>15} {ultimo_id:>21} {'OK' if ok_integridad else 'FALLO':>11} {secuencia:>10}"
              + ("   <-- " + "; ".join(problema) if problema else ""))
        if problema:
            fallos.append((corte, problema))
            print("      líneas recibidas tras este corte:")
            for l in [x for x in lineas if not re.fullmatch(r"[CS] \d+", x)][:14]:
                print(f"        | {l[:150]}")
        ultimo_confirmado = max(ultimo_confirmado, ultimo_id)  # lo que quedó guardado cuenta como base del siguiente

    print(f"\nCobros confirmados vistos: {total_confirmados}. Registros incompletos recuperados (INCIDENCIA): {incidencias}.")
    print(f"Arranques en que la microSD no montó tras el reinicio brusco (se reintentaron): {fallos_montaje}.")
    if fallos:
        print(f"FALLOS en {len(fallos)} de {a.cortes} cortes")
        sys.exit(1)
    print(f"OK: {a.cortes} cortes sin perder ningún cobro confirmado, con integridad y secuencias sin repetirse")


if __name__ == "__main__":
    main()
