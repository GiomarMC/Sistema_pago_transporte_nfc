"""
Interfaz común de las tarjetas del sistema (contrato C3) y apertura según el tipo.

Los programas trabajan con el área lógica de 72 bytes (cabecera de 24 B en el
offset 0, registro A en el 32 y registro B en el 52) sin saber en qué chip
está guardada. Hoy solo existe la NTAG215; la NTAG 424 DNA llegará con M1-04.

Este módulo no importa pyscard: se puede usar (y probar) sin lector.
"""

from typing import Callable, Protocol, runtime_checkable

TAM_AREA = 72
TAM_CABECERA = 24
TAM_REGISTRO = 20
OFFSET_REGISTROS = (32, 52)  # índice 0 = registro A, 1 = registro B


class ErrorTarjeta(Exception):
    """La tarjeta no respondió, se retiró o rechazó un comando."""


class TipoNoSoportado(ErrorTarjeta):
    """La tarjeta del lector no es de un tipo que el sistema sepa manejar."""

    def __init__(self, uid: bytes, mensaje: str):
        super().__init__(mensaje)
        self.uid = uid


class AutenticacionRechazada(ErrorTarjeta):
    """La tarjeta rechazó la autenticación (no emitida por el sistema o sin respuesta)."""


class TarjetaAjena(ErrorTarjeta):
    """La tarjeta aceptó la autenticación pero no prueba conocer las claves del
    sistema (NTAG215: el PACK no coincide)."""


@runtime_checkable
class TarjetaFisica(Protocol):
    """Contrato C3. Mismos nombres que en el firmware (lib/TarjetaTransporte)."""

    @property
    def uid(self) -> bytes:
        """7 bytes."""

    def autenticar(self) -> None:
        """215: PWD_AUTH + PACK. Lanza AutenticacionRechazada o TarjetaAjena."""

    def leer_area(self) -> bytes:
        """Exactamente 72 bytes (requiere autenticar() si la tarjeta está protegida)."""

    def escribir_cabecera(self, datos: bytes) -> None:
        """24 bytes en el offset 0."""

    def escribir_registro(self, indice: int, datos: bytes) -> None:
        """20 bytes; indice 0 = A (offset 32), 1 = B (offset 52). Relee lo escrito y
        lanza ErrorTarjeta si no coincide: el registro anterior queda intacto."""

    def contador(self) -> int | None:
        """215: contador NFC de 24 bits (READ_CNT), siempre int.
        424: None (la NTAG 424 no tiene un contador de lecturas equivalente)."""


@runtime_checkable
class TarjetaAbierta(TarjetaFisica, Protocol):
    """Tarjeta conectada al lector de la PC. cerrar() la deja sin autenticar.
    (Ciclo de vida propio de Python: no forma parte del contrato C3.)"""

    tipo: str

    def cerrar(self) -> None: ...

    def __enter__(self) -> "TarjetaAbierta": ...

    def __exit__(self, *exc) -> None: ...


@runtime_checkable
class Personalizable(Protocol):
    """Operaciones de la estación de emisión (emitir_tarjeta, restablecer_tarjeta).
    Salvo protegida() y reiniciar_sesion(), requieren autenticar() antes si la
    tarjeta está protegida."""

    def protegida(self) -> bool:
        """True si el área del sistema no se puede leer sin autenticar."""

    def configuracion_bloqueada(self) -> bool:
        """True si la configuración del chip quedó bloqueada de forma permanente."""

    def personalizar(self, area: bytes) -> None:
        """Graba el área de 72 bytes y activa la protección con las claves de la tarjeta."""

    def restablecer(self) -> None:
        """Borra el área y devuelve la protección a los valores de fábrica."""

    def reiniciar_sesion(self) -> None:
        """Vuelve a activar la tarjeta: pierde la autenticación, como un toque nuevo."""


@runtime_checkable
class TarjetaPersonalizable(TarjetaAbierta, Personalizable, Protocol):
    """Lo que devuelve abrir_tarjeta() hoy (NTAG215)."""


Contrasena = Callable[[bytes], tuple[bytes, bytes]]


def abrir_tarjeta(conexion, contrasena: Contrasena | None = None) -> TarjetaPersonalizable:
    """Identifica la tarjeta de `conexion` (PC/SC) y devuelve su controlador.

    `contrasena(uid) -> (PWD, PACK)` es la derivación de la NTAG215; por omisión
    la de tarjeta.claves. Lanza TipoNoSoportado si no es una NTAG215 y
    ErrorTarjeta si se pierde la comunicación; en ambos casos la conexión queda
    cerrada.
    """
    from tarjeta.ntag215 import NTAG215, Tarjeta215

    if contrasena is None:
        from tarjeta.claves import contrasena
    try:
        chip = NTAG215(conexion)
    except ErrorTarjeta:
        _desconectar(conexion)
        raise
    try:
        es_215 = chip.es_ntag215()
    except ErrorTarjeta:
        chip.cerrar()
        raise
    if not es_215:
        chip.cerrar()
        raise TipoNoSoportado(chip.uid, "no es una NTAG215")
    return Tarjeta215(chip, contrasena)


def _desconectar(conexion) -> None:
    try:
        conexion.disconnect()
    except Exception:  # la tarjeta ya no está: no hay nada más que cerrar
        pass


def esperar_tarjeta(mensaje="Acerca una tarjeta al lector...",
                    timeout=None) -> TarjetaPersonalizable | None:
    """Espera una tarjeta en el lector y la abre. Con timeout (segundos) devuelve
    None si no aparece ninguna. Lanza TipoNoSoportado si no es de un tipo conocido."""
    from tarjeta.lector import esperar_conexion

    conexion = esperar_conexion(mensaje, timeout)
    return None if conexion is None else abrir_tarjeta(conexion)


def esperar_retirada() -> None:
    """Espera a que se retire la tarjeta del lector."""
    from tarjeta.lector import esperar_retirada as esperar

    esperar()
