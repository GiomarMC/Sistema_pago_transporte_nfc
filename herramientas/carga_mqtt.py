#!/usr/bin/env python3
"""Prueba de carga del servidor por MQTT (M21-02, #156).

Simula N validadores que hablan con el servidor igual que el firmware: cada uno entra a Mosquitto con su
usuario (su n.º) y su contraseña, manda un HOLA al conectar y cada `--hola-cada` s, y un EVENTOS con un viaje
cada `--cada` s, firmados con su clave HMAC. Mide la latencia del acuse: desde que publica un evento hasta que
recibe un ACUSE (firma ECDSA del servidor comprobada) con hasta_evento >= ese evento. Como el firmware, cada
EVENTOS lleva también los eventos todavía sin acuse (hasta 30), así que un acuse perdido no pierde el viaje.

Con `--medir-proyecto`, mide además cada 5 s la CPU, la RAM y la swap de los contenedores del proyecto de
Docker Compose, leyendo sus cgroups (cgroup v2, driver systemd).

Lo normal es lanzarlo con herramientas/carga/correr.sh, que levanta una copia aparte del servidor con
límites de memoria parecidos a los de la VM, crea los validadores y deja el informe en una carpeta.

    python3 herramientas/carga_mqtt.py --credenciales credenciales.json --clave-publica servidor.pem \\
        --puerto 18830 --duracion 1800 --salida resultados/ --medir-proyecto carga

credenciales.json: {"<n.º>": {"hmac": "<64 hex>", "mqtt": "<contraseña>", "tarjeta": <id>, "uid": "<hex>"}}
Requiere paho-mqtt y cryptography (los del servidor: servidor_api/requirements.txt).
"""

import argparse
import csv
import importlib.util
import json
import pathlib
import random
import statistics
import subprocess
import sys
import threading
import time

import paho.mqtt.client as mqtt
from cryptography.hazmat.primitives import serialization

RAIZ = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("paquetes_c1", RAIZ / "servidor_api/transporte/sync_v2/paquetes.py")
P = importlib.util.module_from_spec(_spec)
sys.modules["paquetes_c1"] = P  # los dataclass del módulo lo necesitan registrado para resolver sus tipos
_spec.loader.exec_module(P)

MONTO = 100                 # céntimos por viaje
SALDO_INICIAL = 20000       # la recarga que le da correr.sh a cada cuenta
FIRMWARE = 20261125


def percentil(valores, p):
    if not valores:
        return float("nan")
    orden = sorted(valores)
    k = (len(orden) - 1) * p / 100
    i = int(k)
    return orden[i] if i + 1 >= len(orden) else orden[i] + (orden[i + 1] - orden[i]) * (k - i)


class Validador:
    def __init__(self, numero, datos, args, clave_publica, inicio):
        self.n = numero
        self.hmac = bytes.fromhex(datos["hmac"])
        self.tarjeta = int(datos["tarjeta"])
        self.uid = bytes.fromhex(datos["uid"])
        self.args, self.clave_publica = args, clave_publica
        self.secuencia = 0
        self.ultimo_evento = 0
        self.sin_acuse = {}          # id → Evento
        self.enviado_en = {}         # id → monotonic del primer envío
        self.latencias = []          # (id, segundos)
        self.acuse_hasta = 0
        self.version_listas = 0
        self.version_tarifas = 0
        self.paquetes = {"acuse": 0, "listas": 0, "tarifas": 0, "invalido": 0}
        self.desconexiones = 0
        self.lock = threading.Lock()
        # Cada validador empieza en un momento distinto dentro del primer intervalo, como buses reales
        self.proximo_evento = inicio + random.uniform(0, args.cada)
        self.proximo_hola = inicio + random.uniform(0, args.cada)
        self.cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"carga-{numero}",
                                   clean_session=True)
        self.cliente.username_pw_set(str(numero), datos["mqtt"])
        self.cliente.on_connect = self._al_conectar
        self.cliente.on_disconnect = self._al_desconectar
        self.cliente.on_message = self._al_recibir
        self.conectado = threading.Event()

    # --- MQTT ---

    def conectar(self):
        self.cliente.connect_async(self.args.host, self.args.puerto, keepalive=60)
        self.cliente.loop_start()

    def _al_conectar(self, cliente, _d, _f, codigo, _p):
        if codigo.is_failure:
            print(f"V{self.n}: Mosquitto rechazó la conexión: {codigo}", file=sys.stderr)
            return
        cliente.subscribe(f"sp/v/{self.n}/baja", qos=0)
        self.conectado.set()

    def _al_desconectar(self, _c, _d, _f, codigo, _p):
        if self.conectado.is_set():
            self.desconexiones += 1
        self.conectado.clear()

    def _al_recibir(self, _c, _d, mensaje):
        ahora = time.monotonic()
        try:
            paquete = P.desempaquetar(mensaje.payload, lambda _n: None, self.clave_publica)
        except P.PaqueteInvalido:
            with self.lock:
                self.paquetes["invalido"] += 1
            return
        cuerpo = paquete.cuerpo
        with self.lock:
            if paquete.cabecera.tipo == P.ACUSE:
                self.paquetes["acuse"] += 1
                hasta = cuerpo.hasta_evento
                for i in [i for i in self.sin_acuse if i <= hasta]:
                    del self.sin_acuse[i]
                    self.latencias.append((i, ahora - self.enviado_en[i]))
                self.acuse_hasta = max(self.acuse_hasta, hasta)
            elif paquete.cabecera.tipo == P.LISTAS:
                self.paquetes["listas"] += 1
                if not cuerpo.urgente:
                    self.version_listas = max(self.version_listas, cuerpo.version_hasta)
            elif paquete.cabecera.tipo == P.TARIFAS:
                self.paquetes["tarifas"] += 1
                self.version_tarifas = cuerpo.version

    # --- Paquetes ---

    def _publicar(self, tipo, cuerpo):
        self.secuencia += 1
        datos = P.empaquetar(P.Cabecera(tipo, self.n, P.SERVIDOR, self.secuencia, int(time.time())), cuerpo,
                             clave_hmac=self.hmac)
        return self.cliente.publish(f"sp/v/{self.n}/sube", datos, qos=0).rc == mqtt.MQTT_ERR_SUCCESS

    def hola(self):
        with self.lock:
            cuerpo = P.Hola(self.version_listas, self.version_tarifas, self.ultimo_evento, len(self.sin_acuse),
                            0, P.WIFI, -30, 0, 0, FIRMWARE)
        self._publicar(P.HOLA, cuerpo)

    def evento(self, ahora):
        with self.lock:
            self.ultimo_evento += 1
            k = self.ultimo_evento
            self.sin_acuse[k] = P.Evento(
                id_evento=k, tipo=P.VIAJE, operacion=k, fecha=int(time.time()), id_tarjeta=self.tarjeta,
                uid=self.uid, monto=-MONTO, saldo_final=SALDO_INICIAL - MONTO * k, contador=k)
            self.enviado_en[k] = ahora
            pendientes = [self.sin_acuse[i] for i in sorted(self.sin_acuse)][-P.MAX_EVENTOS:]
        self._publicar(P.EVENTOS, P.Eventos(tuple(pendientes)))

    def vuelta(self, ahora, fin_envios):
        if not self.conectado.is_set():
            return
        if ahora >= self.proximo_hola:
            self.hola()
            self.proximo_hola += self.args.hola_cada
        if ahora >= self.proximo_evento and ahora < fin_envios:
            self.evento(ahora)
            self.proximo_evento += self.args.cada


# --- Medición de los contenedores ------------------------------------------------------------------

def contenedores(proyecto):
    salida = subprocess.run(
        ["docker", "ps", "--no-trunc", "--filter", f"label=com.docker.compose.project={proyecto}",
         "--format", '{{.ID}} {{.Label "com.docker.compose.service"}}'],
        capture_output=True, text=True, check=True).stdout
    return dict(reversed(linea.split()) for linea in salida.splitlines() if linea.strip())


def _leer(ruta):
    try:
        return pathlib.Path(ruta).read_text()
    except OSError:
        return ""


def muestra_cgroup(id_contenedor):
    base = pathlib.Path(f"/sys/fs/cgroup/system.slice/docker-{id_contenedor}.scope")
    cpu = 0
    for linea in _leer(base / "cpu.stat").splitlines():
        if linea.startswith("usage_usec "):
            cpu = int(linea.split()[1])
    return {"cpu_usec": cpu, "ram": int(_leer(base / "memory.current") or 0),
            "swap": int(_leer(base / "memory.swap.current") or 0),
            "ram_max": _leer(base / "memory.max").strip()}


def medir(proyecto, salida, parar, cada=5.0):
    servicios = contenedores(proyecto)
    if not servicios:
        print(f"No hay contenedores del proyecto {proyecto!r}: no se mide", file=sys.stderr)
        return
    anterior = {s: muestra_cgroup(i) for s, i in servicios.items()}
    t_anterior = time.monotonic()
    t0 = t_anterior
    with open(salida, "w", newline="") as archivo:
        csv_ = csv.writer(archivo, lineterminator="\n")
        csv_.writerow(["t_s", "servicio", "cpu_pct", "ram_mib", "swap_mib"])
        while not parar.wait(cada):
            ahora = time.monotonic()
            for servicio, ident in servicios.items():
                m = muestra_cgroup(ident)
                cpu = (m["cpu_usec"] - anterior[servicio]["cpu_usec"]) / 1e6 / (ahora - t_anterior) * 100
                csv_.writerow([round(ahora - t0, 1), servicio, round(cpu, 2), round(m["ram"] / 2**20, 1),
                               round(m["swap"] / 2**20, 2)])
                anterior[servicio] = m
            t_anterior = ahora
            archivo.flush()


def resumen_recursos(ruta):
    filas = list(csv.DictReader(open(ruta)))
    por_servicio = {}
    for f in filas:
        por_servicio.setdefault(f["servicio"], []).append(f)
    tiempos = sorted({float(f["t_s"]) for f in filas})
    total_ram = [sum(float(f["ram_mib"]) for f in filas if float(f["t_s"]) == t) for t in tiempos]
    total_cpu = [sum(float(f["cpu_pct"]) for f in filas if float(f["t_s"]) == t) for t in tiempos]
    total_swap = [sum(float(f["swap_mib"]) for f in filas if float(f["t_s"]) == t) for t in tiempos]
    servicios = {}
    for s, fs in sorted(por_servicio.items()):
        cpu = [float(f["cpu_pct"]) for f in fs]
        ram = [float(f["ram_mib"]) for f in fs]
        swap = [float(f["swap_mib"]) for f in fs]
        servicios[s] = {"cpu_media_pct": round(statistics.mean(cpu), 2), "cpu_p95_pct": round(percentil(cpu, 95), 2),
                        "cpu_max_pct": round(max(cpu), 2), "ram_media_mib": round(statistics.mean(ram), 1),
                        "ram_max_mib": round(max(ram), 1), "swap_max_mib": round(max(swap), 2),
                        "muestras_con_swap": sum(1 for x in swap if x > 0)}
    return {"muestras": len(tiempos), "servicios": servicios,
            "total": {"cpu_media_pct": round(statistics.mean(total_cpu), 2),
                      "cpu_p95_pct": round(percentil(total_cpu, 95), 2),
                      "cpu_max_pct": round(max(total_cpu), 2),
                      "ram_media_mib": round(statistics.mean(total_ram), 1),
                      "ram_max_mib": round(max(total_ram), 1),
                      "swap_max_mib": round(max(total_swap), 2),
                      "muestras_con_swap": sum(1 for x in total_swap if x > 0)}}


# --- Principal --------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--credenciales", required=True, type=pathlib.Path)
    ap.add_argument("--clave-publica", required=True, type=pathlib.Path,
                    help="PEM de la clave pública ECDSA del servidor (para comprobar los ACUSE)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--puerto", type=int, default=1883)
    ap.add_argument("--validadores", type=int, default=None, help="cuántos usar (por defecto, todos)")
    ap.add_argument("--cada", type=float, default=10, help="s entre eventos de cada validador")
    ap.add_argument("--hola-cada", type=float, default=300, help="s entre HOLA (el firmware: 5 min)")
    ap.add_argument("--duracion", type=float, default=1800, help="s de envío de eventos")
    ap.add_argument("--gracia", type=float, default=30, help="s de espera de los últimos acuses")
    ap.add_argument("--salida", required=True, type=pathlib.Path)
    ap.add_argument("--medir-proyecto", help="proyecto de Docker Compose cuyos contenedores se miden")
    args = ap.parse_args()

    credenciales = json.loads(args.credenciales.read_text())
    clave_publica = serialization.load_pem_public_key(args.clave_publica.read_bytes())
    numeros = sorted(int(n) for n in credenciales)[:args.validadores]
    args.salida.mkdir(parents=True, exist_ok=True)

    parar_medicion = threading.Event()
    hilo_medicion = None
    if args.medir_proyecto:
        hilo_medicion = threading.Thread(target=medir, args=(args.medir_proyecto, args.salida / "recursos.csv",
                                                             parar_medicion), daemon=True)
        hilo_medicion.start()

    inicio = time.monotonic() + 2
    buses = [Validador(n, credenciales[str(n)], args, clave_publica, inicio) for n in numeros]
    for b in buses:
        b.conectar()
    limite = time.monotonic() + 30
    while time.monotonic() < limite and not all(b.conectado.is_set() for b in buses):
        time.sleep(0.2)
    sin_conectar = [b.n for b in buses if not b.conectado.is_set()]
    if sin_conectar:
        print(f"No conectaron: {sin_conectar}", file=sys.stderr)

    fin_envios = inicio + args.duracion
    fin = fin_envios + args.gracia
    proximo_aviso = time.monotonic() + 60
    print(f"{len(buses)} validadores, 1 evento cada {args.cada:g} s durante {args.duracion:g} s", flush=True)
    while time.monotonic() < fin:
        ahora = time.monotonic()
        for b in buses:
            b.vuelta(ahora, fin_envios)
        if ahora >= proximo_aviso:
            lat = [s for b in buses for _, s in b.latencias]
            print(f"  {ahora - inicio:6.0f} s · {sum(b.ultimo_evento for b in buses)} eventos · "
                  f"{len(lat)} con acuse · p95 {percentil(lat, 95) * 1000:.0f} ms", flush=True)
            proximo_aviso += 60
        time.sleep(0.05)

    desconexiones = sum(b.desconexiones for b in buses)   # antes de cerrar: las del final no cuentan
    for b in buses:
        b.cliente.loop_stop()
        b.cliente.disconnect()
    parar_medicion.set()
    if hilo_medicion:
        hilo_medicion.join()

    with open(args.salida / "latencias.csv", "w", newline="") as archivo:
        csv_ = csv.writer(archivo, lineterminator="\n")
        csv_.writerow(["validador", "evento", "latencia_ms"])
        for b in buses:
            for i, s in sorted(b.latencias):
                csv_.writerow([b.n, i, round(s * 1000, 1)])

    lat = [s for b in buses for _, s in b.latencias]
    enviados = sum(b.ultimo_evento for b in buses)
    resumen = {
        "configuracion": {"validadores": len(buses), "cada_s": args.cada, "duracion_s": args.duracion,
                          "hola_cada_s": args.hola_cada},
        "eventos": {"enviados": enviados, "con_acuse": len(lat),
                    "sin_acuse": sum(len(b.sin_acuse) for b in buses)},
        "latencia_ms": {"p50": round(percentil(lat, 50) * 1000, 1), "p95": round(percentil(lat, 95) * 1000, 1),
                        "p99": round(percentil(lat, 99) * 1000, 1), "max": round(max(lat) * 1000, 1) if lat else None,
                        "media": round(statistics.mean(lat) * 1000, 1) if lat else None},
        "paquetes_recibidos": {k: sum(b.paquetes[k] for b in buses) for k in buses[0].paquetes},
        "desconexiones": desconexiones,
        "sin_conectar": sin_conectar,
    }
    if hilo_medicion and (args.salida / "recursos.csv").exists():
        resumen["recursos"] = resumen_recursos(args.salida / "recursos.csv")
    (args.salida / "resumen.json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False))
    print(json.dumps(resumen, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
