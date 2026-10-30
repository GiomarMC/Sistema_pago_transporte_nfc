from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping, Protocol
from uuid import UUID


@dataclass(frozen=True)
class CobroCreado:
    id_externo: str
    url_pago: str
    expira: datetime


@dataclass(frozen=True)
class EstadoCobro:
    id_externo: str
    estado: str
    monto: int
    pagado_en: datetime | None


@dataclass(frozen=True)
class EventoPasarela:
    id_evento: str
    tipo: str
    id_externo: str


@dataclass(frozen=True)
class Transaccion:
    id_externo: str
    monto: int
    estado: str
    fecha: datetime


class FirmaInvalida(Exception):
    pass


class ErrorPasarela(Exception):
    pass


class Pasarela(Protocol):
    nombre: str

    def crear_cobro(self, *, orden_id: UUID, monto: int, descripcion: str,
                    correo: str, url_retorno: str) -> CobroCreado: ...
    def consultar(self, id_externo: str) -> EstadoCobro: ...
    def verificar_webhook(self, cabeceras: Mapping[str, str], cuerpo: bytes) -> EventoPasarela: ...
    def reembolsar(self, id_externo: str, monto: int) -> str: ...
    def listar_transacciones(self, dia: date) -> list[Transaccion]: ...
