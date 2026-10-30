"""Libro en céntimos: positivo = debe, negativo = haber."""

from django.conf import settings
from django.db import models


class InmutableError(ValueError):
    pass


class CuentaContable(models.Model):
    class Naturaleza(models.TextChoices):
        ACTIVO = "activo"
        PASIVO = "pasivo"
        INGRESO = "ingreso"

    codigo = models.CharField(max_length=64, unique=True)
    nombre = models.CharField(max_length=120)
    naturaleza = models.CharField(max_length=10, choices=Naturaleza.choices)
    cuenta_cliente = models.OneToOneField(
        "transporte.Cuenta", null=True, blank=True, on_delete=models.PROTECT,
        related_name="cuenta_contable",
    )

    def __str__(self):
        return self.codigo


class Asiento(models.Model):
    fecha = models.DateTimeField()
    tipo = models.CharField(max_length=20)
    ref = models.CharField(max_length=80, unique=True)
    descripcion = models.TextField(blank=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.PROTECT)
    confirmado = models.BooleanField(default=False, editable=False)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise InmutableError("un asiento confirmado no se modifica")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise InmutableError("un asiento confirmado no se elimina")

    def __str__(self):
        return f"{self.ref} ({self.tipo})"


class Partida(models.Model):
    asiento = models.ForeignKey(Asiento, related_name="partidas", on_delete=models.PROTECT)
    cuenta_contable = models.ForeignKey(CuentaContable, on_delete=models.PROTECT)
    monto = models.BigIntegerField(help_text="Céntimos; debe positivo, haber negativo")

    class Meta:
        constraints = [models.CheckConstraint(condition=~models.Q(monto=0), name="partida_no_cero")]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise InmutableError("una partida confirmada no se modifica")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise InmutableError("una partida confirmada no se elimina")


class CorteHistorico(models.Model):
    """Límite de movimientos anteriores a la apertura, sin recontabilizarlos."""

    cuenta = models.OneToOneField("transporte.Cuenta", primary_key=True,
                                  on_delete=models.PROTECT)
    ultimo_movimiento_id = models.BigIntegerField(default=0)
