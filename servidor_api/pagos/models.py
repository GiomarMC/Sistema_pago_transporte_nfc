import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class ClienteCuenta(models.Model):
    """Enlace mínimo de titularidad hasta que M12 aporte el perfil de cliente."""

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    cuenta = models.OneToOneField("transporte.Cuenta", on_delete=models.PROTECT)


class OrdenRecarga(models.Model):
    class Estado(models.TextChoices):
        CREADA = "creada"
        PENDIENTE = "pendiente"
        PAGADA = "pagada"
        ACREDITADA = "acreditada"
        FALLIDA = "fallida"
        EXPIRADA = "expirada"
        REEMBOLSADA = "reembolsada"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cuenta = models.ForeignKey("transporte.Cuenta", on_delete=models.PROTECT)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    monto = models.PositiveIntegerField()
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.CREADA)
    pasarela = models.CharField(max_length=30, default="simulada")
    id_externo = models.CharField(max_length=100, null=True, blank=True)
    clave_idempotencia = models.UUIDField()
    url_pago = models.URLField(max_length=500, blank=True)
    creada = models.DateTimeField(auto_now_add=True)
    expira = models.DateTimeField()
    pagada_en = models.DateTimeField(null=True, blank=True)
    acreditada_en = models.DateTimeField(null=True, blank=True)
    recarga = models.OneToOneField("transporte.Recarga", null=True, blank=True, on_delete=models.PROTECT)
    datos_pasarela = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["pasarela", "id_externo"], name="orden_externo_unico"),
            models.UniqueConstraint(fields=["usuario", "clave_idempotencia"], name="orden_clave_usuario_unica"),
            models.CheckConstraint(condition=Q(monto__gte=100, monto__lte=20000), name="orden_monto_valido"),
        ]
        indexes = [models.Index(fields=["estado", "creada"], name="orden_estado_creada_idx")]


class EventoPasarela(models.Model):
    pasarela = models.CharField(max_length=30)
    id_evento = models.CharField(max_length=100)
    tipo = models.CharField(max_length=40, blank=True)
    cuerpo = models.BinaryField()
    firma_valida = models.BooleanField(default=False)
    recibido = models.DateTimeField(auto_now_add=True)
    procesado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["pasarela", "id_evento"], name="evento_pasarela_unico")]


class HistorialOrden(models.Model):
    orden = models.ForeignKey(OrdenRecarga, related_name="historial", on_delete=models.PROTECT)
    de = models.CharField(max_length=12)
    a = models.CharField(max_length=12)
    fecha = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=240)


class CobroSimulado(models.Model):
    id_externo = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    orden = models.OneToOneField(OrdenRecarga, on_delete=models.PROTECT)
    monto = models.PositiveIntegerField()
    estado = models.CharField(max_length=12, default="pendiente")
    creada = models.DateTimeField(auto_now_add=True)
    expira = models.DateTimeField()
    pagado_en = models.DateTimeField(null=True, blank=True)
    url_retorno = models.URLField(max_length=500, blank=True)
    id_reembolso = models.UUIDField(null=True, blank=True)
