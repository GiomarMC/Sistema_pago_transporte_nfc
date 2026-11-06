from django.db import models


class Notificacion(models.Model):
    """Bandeja de salida: cada correo queda guardado antes de intentar enviarlo."""

    PENDIENTE = "pendiente"
    ENVIADA = "enviada"
    FALLIDA = "fallida"
    ESTADOS = [
        (PENDIENTE, "Pendiente"),
        (ENVIADA, "Enviada"),
        (FALLIDA, "Fallida"),
    ]

    destinatario = models.EmailField()
    tipo = models.CharField(max_length=50)
    asunto = models.CharField(max_length=255)
    texto = models.TextField(help_text="Versión en texto plano.")
    html = models.TextField(help_text="Versión HTML.")
    estado = models.CharField(max_length=10, choices=ESTADOS, default=PENDIENTE, db_index=True)
    intentos = models.PositiveIntegerField(default=0)
    creada = models.DateTimeField(auto_now_add=True)
    enviada_en = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, help_text="Detalle del último error de envío.")
    proximo_intento = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text="Antes de esta hora no se reintenta (vacío = disponible ya).")

    class Meta:
        ordering = ["creada", "pk"]

    def __str__(self):
        return f"{self.tipo} → {self.destinatario} ({self.estado})"
