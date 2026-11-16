"""
Libro mayor de doble partida (M10): cada céntimo que entra, se mueve o sale del
sistema queda en un Asiento con dos o más Partidas que suman cero.

Convención de signos: monto > 0 es debe, monto < 0 es haber. El saldo de un
cliente es el negativo de la suma de las partidas de su SALDO_CLIENTE_<id> (es
lo que SubePe le debe: un pasivo).

    Recarga web de S/ 10.00     CAJA_PASARELA    +1000   SALDO_CLIENTE_7  -1000
    Viaje de S/ 1.30            SALDO_CLIENTE_7  +130    INGRESOS_PASAJES -130

Asientos y partidas son inmutables: no se modifican ni se borran (ni desde el
ORM ni con SQL: lo impiden también triggers de PostgreSQL). Un error se corrige
con un asiento de reverso. Cuenta.saldo queda como una caché del libro.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

PREFIJO_CLIENTE = "SALDO_CLIENTE_"


class RegistroInmutable(Exception):
    """Se intentó modificar o borrar un asiento o una partida ya registrados."""


class _QuerySetInmutable(models.QuerySet):
    def update(self, **kwargs):
        raise RegistroInmutable(f"{self.model.__name__}: el libro no se modifica")

    def delete(self):
        raise RegistroInmutable(f"{self.model.__name__}: el libro no se borra")


class _Inmutable(models.Model):
    """Se puede crear, nunca modificar ni borrar."""
    objects = _QuerySetInmutable.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise RegistroInmutable(f"{type(self).__name__} {self.pk}: el libro no se modifica; "
                                    "para corregir, registra un reverso")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RegistroInmutable(f"{type(self).__name__} {self.pk}: el libro no se borra; "
                                "para corregir, registra un reverso")


class CuentaContable(models.Model):
    class Naturaleza(models.TextChoices):
        ACTIVO = "activo"       # lo que SubePe tiene: cajas
        PASIVO = "pasivo"       # lo que SubePe debe: el saldo de cada cliente
        INGRESO = "ingreso"     # lo que SubePe gana: pasajes

    codigo = models.CharField(max_length=40, unique=True)
    nombre = models.CharField(max_length=120)
    naturaleza = models.CharField(max_length=10, choices=Naturaleza.choices)
    # Solo en las SALDO_CLIENTE_<id>. Si se borra la cuenta del cliente (emisión
    # cancelada, sin movimientos), se va también; con partidas, PROTECT lo impide.
    cuenta_cliente = models.OneToOneField("transporte.Cuenta", on_delete=models.CASCADE,
                                          null=True, blank=True, related_name="cuenta_contable")

    class Meta:
        verbose_name = "cuenta contable"
        verbose_name_plural = "cuentas contables"
        ordering = ["codigo"]
        constraints = [
            models.CheckConstraint(condition=models.Q(cuenta_cliente__isnull=True)
                                   | models.Q(naturaleza="pasivo"),
                                   name="cuenta_cliente_es_pasivo"),
        ]

    def __str__(self):
        return f"{self.codigo} · {self.nombre}"


class Asiento(_Inmutable):
    class Tipo(models.TextChoices):
        RECARGA = "recarga"
        VIAJE = "viaje"
        EMISION = "emision", "emisión"
        REVERSO = "reverso"
        AJUSTE = "ajuste"
        APERTURA = "apertura"

    fecha = models.DateTimeField(default=timezone.now, db_index=True,
                                 help_text="Cuándo ocurrió la operación (un viaje sin "
                                           "conexión llega después)")
    registrado = models.DateTimeField(auto_now_add=True, help_text="Cuándo entró al libro")
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    ref = models.CharField(max_length=80, unique=True,
                           help_text="Referencia única de la operación (idempotencia)")
    descripcion = models.CharField(max_length=200, blank=True)
    # PROTECT: un usuario con asientos no se borra (se desactiva); el libro no
    # puede perder quién hizo cada operación
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   null=True, blank=True, related_name="asientos")

    class Meta:
        ordering = ["-fecha", "-id"]

    def __str__(self):
        return f"{self.ref} ({self.tipo})"


class Partida(_Inmutable):
    asiento = models.ForeignKey(Asiento, on_delete=models.PROTECT, related_name="partidas")
    cuenta_contable = models.ForeignKey(CuentaContable, on_delete=models.PROTECT,
                                        related_name="partidas")
    monto = models.BigIntegerField(help_text="Céntimos de sol: > 0 debe, < 0 haber")

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=~models.Q(monto=0), name="partida_monto_no_cero"),
        ]

    def __str__(self):
        return f"{self.cuenta_contable.codigo} {self.monto:+d}"


def crear_saldo_cliente(sender, instance, created, raw=False, **_):
    """post_save de transporte.Cuenta: crea su SALDO_CLIENTE_<id>."""
    if created and not raw:
        CuentaContable.objects.get_or_create(cuenta_cliente=instance, defaults={
            "codigo": f"{PREFIJO_CLIENTE}{instance.pk}",
            "nombre": f"Saldo del cliente {instance.pk}",
            "naturaleza": CuentaContable.Naturaleza.PASIVO,
        })
