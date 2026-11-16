"""Contrato C5: interfaz de pasarela de pago.

Cada pasarela (la falsa de los tests, la simulada y, más adelante, una real) es un
adaptador que cumple `Pasarela`. El resto del servidor solo usa esta interfaz y obtiene
el adaptador activo con `pagos.pasarelas.obtener_pasarela()`, según `PAGOS_PASARELA`.

Reglas para cualquier adaptador:

1. Nunca se acredita por la redirección del navegador. Solo se acredita tras un webhook
   verificado y un `consultar()` que confirme "pagado" con el mismo monto.
2. El webhook simulado se firma así:
   ``X-Firma: t=<unix>,v1=<hex HMAC-SHA256(PAGOS_SECRETO_WEBHOOK, "<t>." + cuerpo)>``.
   Se rechaza si t difiere más de 300 s de la hora del servidor (ver `firma.py`).
3. Nunca se guardan datos de tarjetas bancarias: el pago ocurre en la `url_pago` de la
   pasarela.

Los montos son enteros en céntimos, como en el resto del servidor.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class CobroCreado:
    id_externo: str
    url_pago: str
    expira: datetime


@dataclass(frozen=True)
class EstadoCobro:
    id_externo: str
    estado: str  # "pendiente" | "pagado" | "fallido" | "expirado"
    monto: int
    pagado_en: datetime | None


@dataclass(frozen=True)
class EventoPasarela:
    id_evento: str
    tipo: str  # "cobro.pagado" | "cobro.fallido" | "reembolso.hecho"
    id_externo: str


@dataclass(frozen=True)
class Transaccion:
    id_externo: str
    monto: int
    estado: str
    fecha: datetime


class FirmaInvalida(Exception):
    """El webhook no viene de la pasarela: firma ausente, alterada o fuera de hora."""


class ErrorPasarela(Exception):
    """Red, timeout o respuesta inesperada: se reintenta."""


class Pasarela(Protocol):
    nombre: str

    def crear_cobro(self, *, orden_id: UUID, monto: int, descripcion: str,
                    correo: str, url_retorno: str) -> CobroCreado: ...

    def consultar(self, id_externo: str) -> EstadoCobro: ...

    def verificar_webhook(self, cabeceras: Mapping[str, str], cuerpo: bytes) -> EventoPasarela: ...

    def reembolsar(self, id_externo: str, monto: int) -> str: ...  # id del reembolso

    def listar_transacciones(self, dia: date) -> list[Transaccion]: ...


ESTADOS_COBRO = ("pendiente", "pagado", "fallido", "expirado")
TIPOS_EVENTO = ("cobro.pagado", "cobro.fallido", "reembolso.hecho")
