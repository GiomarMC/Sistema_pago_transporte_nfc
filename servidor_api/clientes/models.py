"""
Identidad de los pasajeros, separada de los operadores.

Un cliente entra con un usuario "cli-<documento>" que no pertenece a ningún grupo
de operadores. Los códigos de activación y OTP se guardan solo como hash.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.db.models import Q
from django.utils import timezone

MAX_INTENTOS_OTP = 5
PREFIJO_USUARIO = "cli-"


def expira_activacion():
    return timezone.now() + timedelta(hours=72)


def expira_otp():
    return timezone.now() + timedelta(minutes=10)


def usuario_cliente(documento):
    """Nombre de usuario de un cliente: el prefijo evita chocar con los operadores."""
    return f"{PREFIJO_USUARIO}{documento}"


def es_usuario_cliente(username):
    """El prefijo está reservado: sin distinguir mayúsculas, como el registro del panel."""
    return username.lower().startswith(PREFIJO_USUARIO)


def _digito_luhn(base):
    suma = 0
    for i, caracter in enumerate(reversed(base)):
        digito = int(caracter)
        if i % 2 == 0:
            digito *= 2
            if digito > 9:
                digito -= 9
        suma += digito
    return (10 - suma % 10) % 10


def numero_impreso(tarjeta):
    """Número visible en la tarjeta: id en 8 dígitos + control Luhn, p. ej. 0000 0123 0."""
    base = f"{tarjeta.id:08d}"
    numero = f"{base}{_digito_luhn(base)}"
    return " ".join(numero[i:i + 4] for i in range(0, len(numero), 4))


class Cliente(models.Model):
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                   related_name="cliente")
    cuenta = models.OneToOneField("transporte.Cuenta", on_delete=models.PROTECT,
                                  related_name="cliente")
    correo = models.EmailField(unique=True)
    correo_verificado = models.BooleanField(default=False)
    creado = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.usuario.username} · {self.correo}"


class CodigoHasheado(models.Model):
    """Código de un solo uso: el valor en claro solo existe al generarlo y enviarlo."""
    hash = models.CharField(max_length=128)

    class Meta:
        abstract = True

    def asignar_codigo(self, codigo):
        self.hash = make_password(codigo)

    def verificar_codigo(self, codigo):
        return check_password(codigo, self.hash)


class CodigoActivacion(CodigoHasheado):
    # CASCADE: al cancelar una emisión se borra la tarjeta pendiente (servicios.py)
    tarjeta = models.ForeignKey("transporte.Tarjeta", on_delete=models.CASCADE,
                                related_name="codigos_activacion")
    expira = models.DateTimeField(default=expira_activacion)
    usado_en = models.DateTimeField(null=True, blank=True)
    intentos = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return f"Código de activación · tarjeta {self.tarjeta_id}"


class CodigoOTP(CodigoHasheado):
    class Proposito(models.TextChoices):
        ACTIVACION = "activacion"
        INICIO = "inicio"
        CORREO = "correo"

    # Pertenece a un cliente o, si aún no existe, a una activación en curso
    cliente = models.ForeignKey(Cliente, null=True, blank=True, on_delete=models.CASCADE,
                                related_name="codigos_otp")
    token_activacion = models.CharField(max_length=64, null=True, blank=True, db_index=True,
                                        help_text="SHA-256 del token de activación")
    proposito = models.CharField(max_length=10, choices=Proposito.choices)
    expira = models.DateTimeField(default=expira_otp)
    intentos = models.PositiveSmallIntegerField(default=0)
    usado = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(cliente__isnull=False, token_activacion__isnull=True)
                           | Q(cliente__isnull=True, token_activacion__isnull=False)),
                name="otp_cliente_o_token",
            ),
            models.CheckConstraint(condition=Q(intentos__lte=MAX_INTENTOS_OTP),
                                   name="otp_intentos_max"),
        ]

    def __str__(self):
        return f"OTP {self.proposito} · {self.expira:%Y-%m-%d %H:%M}"


class Dispositivo(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="dispositivos")
    huella = models.CharField(max_length=64, help_text="SHA-256 del identificador de la cookie")
    nombre = models.CharField(max_length=120, blank=True, help_text="Resumen del user-agent")
    ultimo_uso = models.DateTimeField(default=timezone.now)
    confiable_hasta = models.DateTimeField(null=True, blank=True,
                                           help_text="Vacío = no confiable")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["cliente", "huella"],
                                               name="dispositivo_huella_unica")]

    def __str__(self):
        return f"{self.nombre or 'Dispositivo'} · cliente {self.cliente_id}"


class IntentoAcceso(models.Model):
    documento = models.CharField(max_length=20)
    ip = models.GenericIPAddressField(null=True, blank=True)
    exito = models.BooleanField()
    fecha = models.DateTimeField(auto_now_add=True)
    motivo = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        indexes = [
            models.Index(fields=["documento", "-fecha"], name="intento_doc_fecha_idx"),
            models.Index(fields=["ip", "-fecha"], name="intento_ip_fecha_idx"),
        ]

    def __str__(self):
        return f"{self.documento} · {'éxito' if self.exito else 'fallo'} · {self.fecha:%Y-%m-%d %H:%M}"
