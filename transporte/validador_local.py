"""
Almacenamiento local del validador y su cliente de sincronización.

Cada validador tiene su propia base SQLite (datos/validador_<id>.db) con todo
lo que necesita para cobrar sin conexión:
  - lista negra de tarjetas (copia de la última sincronización)
  - tarifas vigentes
  - recargas remotas pendientes de llegar a las tarjetas
  - cola de eventos (viajes e incidencias) pendientes de subir
  - último n.º de operación visto por tarjeta (detecta copias restauradas)
"""

import json
import sqlite3
from datetime import datetime, timezone

import api_cliente
from config import RAIZ, TARIFAS

ESQUEMA = """
CREATE TABLE IF NOT EXISTS ajustes (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lista_negra (
    tarjeta_id INTEGER PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS eventos (
    id           INTEGER PRIMARY KEY,
    tipo         TEXT NOT NULL,          -- viaje | incidencia | fraude
    fecha        TEXT NOT NULL,
    tarjeta_id   INTEGER NOT NULL,
    uid          TEXT NOT NULL,
    monto        INTEGER,
    saldo_final  INTEGER,
    operacion    INTEGER,
    contador     INTEGER,
    detalle      TEXT,
    recarga_hasta INTEGER,               -- recargas aplicadas en este viaje (hasta este seq)
    sincronizado INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS recargas (
    cuenta_id INTEGER NOT NULL,
    seq       INTEGER NOT NULL,
    monto     INTEGER NOT NULL,
    PRIMARY KEY (cuenta_id, seq)
);
CREATE TABLE IF NOT EXISTS ultima_operacion (
    tarjeta_id INTEGER PRIMARY KEY,
    operacion  INTEGER NOT NULL
);
"""


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BDValidador:
    def __init__(self, id_validador: int):
        self.id = id_validador
        ruta = RAIZ / "datos" / f"validador_{id_validador}.db"
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self.bd = sqlite3.connect(ruta)
        self.bd.row_factory = sqlite3.Row
        self.bd.executescript(ESQUEMA)
        if "recarga_hasta" not in {c["name"] for c in self.bd.execute("PRAGMA table_info(eventos)")}:
            self.bd.execute("ALTER TABLE eventos ADD COLUMN recarga_hasta INTEGER")

    # --- Consultas para cobrar -------------------------------------------------

    def bloqueada(self, id_tarjeta) -> bool:
        return self.bd.execute("SELECT 1 FROM lista_negra WHERE tarjeta_id = ?",
                               (id_tarjeta,)).fetchone() is not None

    def tarifa(self, codigo):
        guardadas = self._ajuste("tarifas")
        tabla = {int(c): tuple(v) for c, v in json.loads(guardadas).items()} if guardadas else TARIFAS
        return tabla.get(codigo)

    def ultima_operacion(self, id_tarjeta) -> int:
        f = self.bd.execute("SELECT operacion FROM ultima_operacion WHERE tarjeta_id = ?",
                            (id_tarjeta,)).fetchone()
        return f["operacion"] if f else 0

    def recargas_pendientes(self, id_cuenta, ultima_aplicada):
        """Recargas de la cuenta posteriores a la última aplicada en la tarjeta,
        solo si son correlativas (no se salta ninguna). Devuelve (monto total, hasta_seq)."""
        total, hasta = 0, ultima_aplicada
        for f in self.bd.execute("SELECT seq, monto FROM recargas WHERE cuenta_id = ? AND seq > ? "
                                 "ORDER BY seq", (id_cuenta, ultima_aplicada)):
            if f["seq"] != hasta + 1:
                break
            total, hasta = total + f["monto"], f["seq"]
        return total, hasta

    def existe_viaje(self, id_tarjeta, operacion) -> bool:
        return self.bd.execute(
            "SELECT 1 FROM eventos WHERE tipo = 'viaje' AND tarjeta_id = ? AND operacion = ?",
            (id_tarjeta, operacion)).fetchone() is not None

    # --- Registro --------------------------------------------------------------

    def registrar_viaje(self, id_tarjeta, uid, monto, saldo_final, operacion, contador,
                        recarga_hasta=None):
        self.bd.execute(
            "INSERT INTO eventos (tipo, fecha, tarjeta_id, uid, monto, saldo_final, operacion, "
            "contador, recarga_hasta) VALUES ('viaje', ?, ?, ?, ?, ?, ?, ?, ?)",
            (ahora(), id_tarjeta, uid, monto, saldo_final, operacion, contador, recarga_hasta))
        self.bd.execute(
            "INSERT INTO ultima_operacion (tarjeta_id, operacion) VALUES (?, ?) "
            "ON CONFLICT(tarjeta_id) DO UPDATE SET operacion = MAX(operacion, excluded.operacion)",
            (id_tarjeta, operacion))
        self.bd.commit()

    def registrar_incidencia(self, id_tarjeta, uid, detalle, fraude=False):
        """fraude=True pide al servidor que bloquee la tarjeta."""
        self.bd.execute(
            "INSERT INTO eventos (tipo, fecha, tarjeta_id, uid, detalle) VALUES (?, ?, ?, ?, ?)",
            ("fraude" if fraude else "incidencia", ahora(), id_tarjeta, uid, detalle))
        self.bd.commit()

    # --- Sincronización -----------------------------------------------------------

    def resumen(self) -> dict:
        pendientes = self.bd.execute(
            "SELECT COUNT(*) FROM eventos WHERE sincronizado = 0").fetchone()[0]
        bloqueadas = self.bd.execute("SELECT COUNT(*) FROM lista_negra").fetchone()[0]
        return {"pendientes": pendientes, "bloqueadas": bloqueadas,
                "ultima_sync": self._ajuste("ultima_sync") or "nunca"}

    def _enviar_http(self, peticion_json):
        respuesta = api_cliente.llamar("POST", "/api/sync/", json.loads(peticion_json),
                                       token=api_cliente.token_validador(self.id), timeout=10)
        return json.dumps(respuesta)

    def sincronizar(self, enviar=None):
        """Sube los eventos pendientes y descarga lista negra, recargas y tarifas.
        `enviar` recibe y devuelve JSON (por defecto, POST a la API); lanza
        excepción si no hay red. Devuelve (n.º eventos subidos, n.º tarjetas bloqueadas)."""
        pendientes = [dict(f) for f in self.bd.execute(
            "SELECT * FROM eventos WHERE sincronizado = 0 ORDER BY id")]
        for p in pendientes:
            del p["sincronizado"]
        enviar = enviar or self._enviar_http
        respuesta = json.loads(enviar(json.dumps({"validador": self.id, "eventos": pendientes})))
        if respuesta.get("validador", self.id) != self.id:
            raise RuntimeError(f"el token corresponde al validador {respuesta['validador']}, "
                               f"no al {self.id}")

        # Solo se marca lo que el servidor confirmó: si se corta la red antes de
        # recibir la respuesta, se reenvía todo y el servidor ignora los repetidos.
        self.bd.executemany("UPDATE eventos SET sincronizado = 1 WHERE id = ?",
                            [(i,) for i in respuesta["aceptados"]])
        self.bd.execute("DELETE FROM lista_negra")
        self.bd.executemany("INSERT INTO lista_negra (tarjeta_id) VALUES (?)",
                            [(i,) for i in respuesta["lista_negra"]])
        self.bd.execute("DELETE FROM recargas")
        self.bd.executemany("INSERT INTO recargas (cuenta_id, seq, monto) VALUES (?, ?, ?)",
                            [(r["cuenta_id"], r["seq"], r["monto"]) for r in respuesta["recargas"]])
        self._guardar_ajuste("tarifas", json.dumps(respuesta["tarifas"]))
        self._guardar_ajuste("ultima_sync", ahora())
        self.bd.commit()
        self.desfase = None
        if "hora_servidor" in respuesta:
            self.desfase = (datetime.now(timezone.utc)
                            - datetime.fromisoformat(respuesta["hora_servidor"])).total_seconds()
        return len(respuesta["aceptados"]), len(respuesta["lista_negra"])

    def _ajuste(self, clave):
        f = self.bd.execute("SELECT valor FROM ajustes WHERE clave = ?", (clave,)).fetchone()
        return f["valor"] if f else None

    def _guardar_ajuste(self, clave, valor):
        self.bd.execute("INSERT INTO ajustes (clave, valor) VALUES (?, ?) "
                        "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", (clave, valor))
