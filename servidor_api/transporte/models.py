"""
Modelo de datos del sistema de transporte.

El servidor es la fuente de verdad del saldo (Cuenta.saldo). La tarjeta lleva
una copia firmada que se reconcilia con los eventos que suben los validadores.
Todos los montos están en céntimos de sol.
"""

from django.conf import settings
from django.db import models


def soles(centimos):
    signo = "-" if centimos < 0 else ""
    centimos = abs(centimos)
    return f"{signo}S/ {centimos // 100}.{centimos % 100:02d}"


class Tarifa(models.Model):
    """Código grabado en la tarjeta -> precio. Editable en el admin; los
    validadores reciben los cambios en su siguiente sincronización."""
    codigo = models.PositiveSmallIntegerField(primary_key=True)
    nombre = models.CharField(max_length=40, unique=True)
    precio = models.PositiveIntegerField(help_text="Céntimos de sol")

    class Meta:
        ordering = ["codigo"]

    def __str__(self):
        return f"{self.nombre} ({soles(self.precio)})"


class Cuenta(models.Model):
    nombre = models.CharField(max_length=120)
    documento = models.CharField(max_length=20, unique=True)
    tarifa = models.ForeignKey(Tarifa, on_delete=models.PROTECT)
    saldo = models.IntegerField(default=0, help_text="Céntimos de sol (saldo real)")
    creada = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.id} · {self.nombre} ({self.documento})"


class Tarjeta(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente"   # reservada, aún no grabada
        ACTIVA = "activa"
        BLOQUEADA = "bloqueada"   # robo, pérdida o fraude
        ANULADA = "anulada"       # reemplazada o restablecida

    uid = models.CharField(max_length=20, db_index=True)
    cuenta = models.ForeignKey(Cuenta, on_delete=models.PROTECT, related_name="tarjetas")
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    emitida = models.DateTimeField(auto_now_add=True)
    bloqueada_en = models.DateTimeField(null=True, blank=True)
    # Última recarga de la cuenta incluida en el saldo con que se grabó la tarjeta
    recarga_inicial = models.PositiveIntegerField(default=0)
    # La emisión creó la cuenta (si se cancela, la cuenta vacía se elimina)
    creo_cuenta = models.BooleanField(default=False)

    def __str__(self):
        return f"Tarjeta {self.id} · {self.uid} ({self.estado})"

    class Meta:
        indexes = [models.Index(fields=["estado"], name="tarjeta_estado_idx")]


class Recarga(models.Model):
    cuenta = models.ForeignKey(Cuenta, on_delete=models.PROTECT, related_name="recargas")
    seq = models.PositiveIntegerField(help_text="Correlativo por cuenta: 1, 2, 3...")
    monto = models.PositiveIntegerField(help_text="Céntimos de sol")
    origen = models.CharField(max_length=20)        # yape | punto | web | emision ...
    creada = models.DateTimeField(auto_now_add=True)
    aplicada_en = models.DateTimeField(null=True, blank=True,
                                       help_text="Cuándo llegó a la tarjeta (vacío = pendiente)")
    aplicada_por = models.PositiveIntegerField(null=True, blank=True)
    tarjeta = models.ForeignKey(Tarjeta, null=True, blank=True, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["cuenta", "seq"], name="recarga_seq_unica")]
        ordering = ["cuenta", "seq"]
        indexes = [models.Index(fields=["aplicada_en"], name="recarga_aplicada_idx")]

    def __str__(self):
        return f"Recarga {self.seq} · cuenta {self.cuenta_id} · {soles(self.monto)}"


class Movimiento(models.Model):
    fecha = models.DateTimeField()
    cuenta = models.ForeignKey(Cuenta, on_delete=models.PROTECT, related_name="movimientos")
    tarjeta = models.ForeignKey(Tarjeta, null=True, blank=True, on_delete=models.PROTECT)
    tipo = models.CharField(max_length=20)          # emision | recarga | viaje
    monto = models.IntegerField(help_text="Céntimos (negativo = cobro)")
    saldo_final = models.IntegerField()
    operacion = models.PositiveIntegerField(null=True, blank=True,
                                            help_text="N.º de operación grabado en la tarjeta")
    validador = models.PositiveIntegerField(null=True, blank=True)
    ref = models.CharField(max_length=40, null=True, blank=True, unique=True,
                           help_text="Id único del origen (p. ej. V101-57): evita duplicados")
    contador = models.PositiveIntegerField(null=True, blank=True, help_text="Contador NFC")

    class Meta:
        ordering = ["-fecha", "-id"]
        indexes = [
            models.Index(fields=["tipo", "-fecha"], name="mov_tipo_fecha_idx"),
            models.Index(fields=["validador", "-fecha"], name="mov_val_fecha_idx"),
        ]

    def __str__(self):
        return f"{self.tipo} {soles(self.monto)} · cuenta {self.cuenta_id}"


class Alerta(models.Model):
    fecha = models.DateTimeField(auto_now_add=True)
    tarjeta = models.ForeignKey(Tarjeta, null=True, blank=True, on_delete=models.SET_NULL)
    tipo = models.CharField(max_length=60)
    detalle = models.TextField(blank=True)
    revisada = models.BooleanField(default=False)
    revisada_en = models.DateTimeField(null=True, blank=True)
    revisada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="alertas_revisadas",
    )

    class Meta:
        ordering = ["-fecha", "-id"]
        indexes = [models.Index(fields=["revisada", "-fecha"], name="alerta_rev_fecha_idx")]

    def __str__(self):
        return f"{self.tipo} · {self.fecha:%Y-%m-%d %H:%M}"


class Validador(models.Model):
    """Dispositivo de un bus. Se autentica con el token de su usuario."""
    numero = models.PositiveIntegerField(unique=True,
                                         help_text="Id grabado en la tarjeta (1-899)")
    descripcion = models.CharField(max_length=80, blank=True, help_text="Bus, placa, ruta...")
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                   related_name="validador")
    ultima_sync = models.DateTimeField(null=True, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "validadores"

    def __str__(self):
        return f"Validador {self.numero} {self.descripcion}".strip()
