"""Pasarela falsa, en memoria, para los tests (M0-05).

No hace llamadas de red ni guarda nada en la base de datos. Además de la interfaz C5
ofrece ayudas para preparar cada caso:

    falsa = PasarelaFalsa()
    cobro = falsa.crear_cobro(orden_id=..., monto=1000, ...)
    cabeceras, cuerpo = falsa.pagar(cobro.id_externo)    # estado "pagado" + webhook firmado
    falsa.fijar_estado(cobro.id_externo, "pagado", monto=999)  # consultar() con otro monto
    falsa.fallar("consultar")                            # la próxima llamada da ErrorPasarela

El cuerpo de sus webhooks es JSON con los campos de EventoPasarela:
``{"id_evento": "...", "tipo": "cobro.pagado", "id_externo": "..."}``.
"""

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from . import firma
from .base import (
    ESTADOS_COBRO, TIPOS_EVENTO, CobroCreado, ErrorPasarela, EstadoCobro, EventoPasarela,
    Transaccion,
)

SECRETO_DE_PRUEBA = "secreto-de-la-pasarela-falsa"
VIGENCIA = timedelta(minutes=30)
METODOS = ("crear_cobro", "consultar", "verificar_webhook", "reembolsar",
           "listar_transacciones")


@dataclass
class _Cobro:
    orden_id: UUID
    monto: int
    descripcion: str
    correo: str
    url_retorno: str
    expira: datetime
    estado: str = "pendiente"
    pagado_en: datetime | None = None
    reembolsos: list[tuple[str, int, datetime]] = field(default_factory=list)


class PasarelaFalsa:
    nombre = "falsa"

    def __init__(self, secreto: str | None = None,
                 reloj: Callable[[], datetime] = timezone.now):
        # Si el proyecto no define PAGOS_SECRETO_WEBHOOK, firma con uno fijo de prueba
        self.secreto = secreto or getattr(settings, "PAGOS_SECRETO_WEBHOOK", "") \
            or SECRETO_DE_PRUEBA
        self.reloj = reloj
        self.cobros: dict[str, _Cobro] = {}
        self._fallos: dict[str, Exception] = {}

    # --- Interfaz C5 -----------------------------------------------------------------------

    def crear_cobro(self, *, orden_id: UUID, monto: int, descripcion: str,
                    correo: str, url_retorno: str) -> CobroCreado:
        self._quizas_fallar("crear_cobro")
        if not isinstance(monto, int) or isinstance(monto, bool) or monto <= 0:
            raise ValueError("el monto debe ser un entero positivo (céntimos)")
        id_externo = f"falsa_{uuid.uuid4().hex}"
        expira = self.reloj() + VIGENCIA
        self.cobros[id_externo] = _Cobro(orden_id, monto, descripcion, correo, url_retorno,
                                         expira)
        return CobroCreado(id_externo, f"https://pasarela-falsa.invalid/pagar/{id_externo}/",
                           expira)

    def consultar(self, id_externo: str) -> EstadoCobro:
        self._quizas_fallar("consultar")
        cobro = self._cobro(id_externo)
        if cobro.estado == "pendiente" and self.reloj() >= cobro.expira:
            cobro.estado = "expirado"
        return EstadoCobro(id_externo, cobro.estado, cobro.monto, cobro.pagado_en)

    def verificar_webhook(self, cabeceras: Mapping[str, str], cuerpo: bytes) -> EventoPasarela:
        self._quizas_fallar("verificar_webhook")
        firma.verificar(self.secreto, cabeceras, cuerpo, ahora=self.reloj().timestamp())
        try:
            datos = json.loads(cuerpo)
            evento = EventoPasarela(str(datos["id_evento"]), str(datos["tipo"]),
                                    str(datos["id_externo"]))
        except (ValueError, TypeError, KeyError) as e:
            raise ErrorPasarela(f"cuerpo del webhook inesperado: {e}") from None
        if evento.tipo not in TIPOS_EVENTO:
            raise ErrorPasarela(f"tipo de evento desconocido: {evento.tipo}")
        return evento

    def reembolsar(self, id_externo: str, monto: int) -> str:
        self._quizas_fallar("reembolsar")
        cobro = self._cobro(id_externo)
        if cobro.estado != "pagado":
            raise ValueError("solo se reembolsa un cobro pagado")
        devuelto = sum(m for _, m, _ in cobro.reembolsos)
        if not isinstance(monto, int) or isinstance(monto, bool) or not \
                0 < monto <= cobro.monto - devuelto:
            raise ValueError("monto de reembolso no válido")
        id_reembolso = f"falsa_reemb_{uuid.uuid4().hex}"
        cobro.reembolsos.append((id_reembolso, monto, self.reloj()))
        return id_reembolso

    def listar_transacciones(self, dia: date) -> list[Transaccion]:
        """Cobros pagados y reembolsos de `dia` (fecha local del servidor), por hora."""
        self._quizas_fallar("listar_transacciones")
        lista = []
        for id_externo, cobro in self.cobros.items():
            if cobro.pagado_en and timezone.localdate(cobro.pagado_en) == dia:
                lista.append(Transaccion(id_externo, cobro.monto, "pagado", cobro.pagado_en))
            for _, monto, fecha in cobro.reembolsos:
                if timezone.localdate(fecha) == dia:
                    lista.append(Transaccion(id_externo, monto, "reembolsado", fecha))
        return sorted(lista, key=lambda t: t.fecha)

    # --- Ayudas para los tests -------------------------------------------------------------

    def fijar_estado(self, id_externo: str, estado: str, *, monto: int | None = None,
                     pagado_en: datetime | None = None) -> None:
        """Fija lo que devolverá consultar(). `monto` permite simular un pago por otra cifra."""
        if estado not in ESTADOS_COBRO:
            raise ValueError(f"estado desconocido: {estado}")
        cobro = self._cobro(id_externo)
        cobro.estado = estado
        if monto is not None:
            cobro.monto = monto
        if estado == "pagado":
            cobro.pagado_en = pagado_en or cobro.pagado_en or self.reloj()
        else:
            cobro.pagado_en = None

    def generar_webhook(self, id_externo: str, tipo: str = "cobro.pagado", *,
                        id_evento: str | None = None,
                        t: int | None = None) -> tuple[dict[str, str], bytes]:
        """Devuelve (cabeceras, cuerpo) de un webhook firmado.

        `t` (hora unix de la firma) permite generar uno fuera de hora.
        """
        cuerpo = json.dumps({"id_evento": id_evento or f"evt_{uuid.uuid4().hex}", "tipo": tipo,
                             "id_externo": id_externo}).encode()
        t = int(self.reloj().timestamp()) if t is None else t
        return {firma.CABECERA: firma.firmar(self.secreto, cuerpo, t)}, cuerpo

    def pagar(self, id_externo: str) -> tuple[dict[str, str], bytes]:
        """Marca el cobro como pagado y devuelve el webhook "cobro.pagado" correspondiente."""
        self.fijar_estado(id_externo, "pagado")
        return self.generar_webhook(id_externo, "cobro.pagado")

    def fallar(self, metodo: str, error: Exception | None = None) -> None:
        """La próxima llamada a `metodo` lanza `error` (por defecto ErrorPasarela, de red)."""
        if metodo not in METODOS:
            raise ValueError(f"método desconocido: {metodo}")
        self._fallos[metodo] = error or ErrorPasarela("fallo simulado de la pasarela")

    # --- Internas --------------------------------------------------------------------------

    def _cobro(self, id_externo: str) -> _Cobro:
        try:
            return self.cobros[id_externo]
        except KeyError:
            raise ErrorPasarela(f"cobro desconocido: {id_externo}") from None

    def _quizas_fallar(self, metodo: str) -> None:
        error = self._fallos.pop(metodo, None)
        if error is not None:
            raise error
