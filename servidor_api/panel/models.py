from django.conf import settings
from django.db import models


class VerificacionLibro(models.Model):
    fecha = models.DateTimeField(auto_now_add=True)
    correcto = models.BooleanField()
    diferencias = models.JSONField(default=list)
    cuentas = models.PositiveIntegerField(default=0)
    asientos = models.PositiveIntegerField(default=0)


class ConciliacionPago(models.Model):
    fecha = models.DateTimeField(auto_now_add=True)
    dia = models.DateField()
    transacciones = models.PositiveIntegerField()
    ordenes = models.PositiveIntegerField()
    diferencias = models.JSONField(default=list)


class SolicitudReposicion(models.Model):
    class Estado(models.TextChoices):
        SOLICITADA = "solicitada"
        LISTA = "lista"
        ENTREGADA = "entregada"

    cliente = models.ForeignKey("pagos.ClienteCuenta", on_delete=models.PROTECT)
    tarjeta = models.ForeignKey("transporte.Tarjeta", on_delete=models.PROTECT)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.SOLICITADA)
    creada = models.DateTimeField(auto_now_add=True)
    lista_en = models.DateTimeField(null=True, blank=True)
    entregada_en = models.DateTimeField(null=True, blank=True)
    gestionada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                       on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tarjeta"], name="reposicion_unica_tarjeta")]
