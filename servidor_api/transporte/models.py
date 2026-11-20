"""
Modelo de datos del sistema de transporte.

El servidor es la fuente de verdad del saldo (Cuenta.saldo). La tarjeta lleva
una copia firmada que se reconcilia con los eventos que suben los validadores.
Todos los montos están en céntimos de sol.
"""

import secrets
from datetime import datetime

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone


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
    # La versión de TARIFAS (C1 5.6) es la marca unix de la última modificación
    modificada = models.DateTimeField(auto_now=True)

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
    # Correo para notificaciones. Solo se usa si está verificado (correo_verificado_en informado).
    correo = models.EmailField(blank=True)
    correo_verificado_en = models.DateTimeField(
        null=True, blank=True, help_text="Cuándo confirmó el cliente su correo (vacío = no verificado)")

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
    reembolsada_en = models.DateTimeField(null=True, blank=True)
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


def nueva_clave_hmac():
    """32 bytes aleatorios en hexadecimal: la clave HMAC-SHA256 de un validador (C1)."""
    return secrets.token_bytes(32).hex()


class Validador(models.Model):
    """Dispositivo de un bus. Se autentica con el token de su usuario y firma sus paquetes
    de la sincronización v2 con su propia clave HMAC."""
    numero = models.PositiveIntegerField(unique=True,
                                         help_text="Id grabado en la tarjeta (1-899)")
    descripcion = models.CharField(max_length=80, blank=True, help_text="Bus, placa, ruta...")
    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                   related_name="validador")
    ultima_sync = models.DateTimeField(null=True, blank=True)
    # Estado de conexión del tópico MQTT sp/v/{n}/conexion (lo escribe registrar_conexion)
    conexion_en = models.DateTimeField(null=True, blank=True)
    conexion_activa = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)
    clave_hmac = models.CharField(
        max_length=64, default=nueva_clave_hmac, editable=False,
        validators=[RegexValidator(r"^[0-9a-f]{64}$", "64 caracteres hexadecimales")],
        help_text="Clave HMAC-SHA256 de sus paquetes. Ver y rotar: manage.py clave_validador")

    # --- Sincronización v2 (C1) ---
    class Enlace(models.IntegerChoices):     # campo enlace del HOLA (C1 5.4)
        WIFI = 1, "WiFi"
        BLE = 3, "BLE (celular del conductor)"
        REENVIADO = 4, "Reenviado por otro bus"

    ultimo_evento_contiguo = models.PositiveBigIntegerField(
        default=0, help_text="Mayor N tal que se recibieron todos sus eventos 1..N (C1 5.7)")
    version_listas = models.PositiveBigIntegerField(
        default=0, help_text="Última versión de LISTAS que dijo tener en su HOLA")
    ultimo_hola = models.JSONField(null=True, blank=True,
                                   help_text="Campos del último HOLA recibido")
    ultimo_enlace = models.PositiveSmallIntegerField(choices=Enlace.choices,
                                                     null=True, blank=True)
    mqtt_clave = models.CharField(
        max_length=255, blank=True, editable=False,
        help_text="Hash de su contraseña MQTT (vacío = sin acceso a MQTT)")

    class Meta:
        verbose_name_plural = "validadores"

    def __str__(self):
        return f"Validador {self.numero} {self.descripcion}".strip()

    # --- Lo último que dijo el validador en su HOLA (C1 5.4), para el panel ---
    def _hola(self, campo):
        return (self.ultimo_hola or {}).get(campo)

    @property
    def hola_en(self):
        """Cuándo llegó el último HOLA (None si nunca)."""
        recibido = self._hola("recibido")
        return datetime.fromisoformat(recibido) if recibido else None

    @property
    def canal(self) -> str:
        """Enlace del último HOLA; el 2 (4G) quedó reservado y sale como desconocido."""
        enlace = self._hola("enlace")
        if enlace is None:
            return ""
        return dict(self.Enlace.choices).get(enlace, "desconocido")

    @property
    def eventos_pendientes(self):
        return self._hola("pendientes")

    @property
    def consumo_bytes(self):
        """Bytes enviados y recibidos hoy, según el último HOLA."""
        if self._hola("bytes_tx_hoy") is None:
            return None
        return self._hola("bytes_tx_hoy") + (self._hola("bytes_rx_hoy") or 0)

    @property
    def firmware(self):
        """Versión de firmware (AAAAMMDD) del último HOLA."""
        return self._hola("version_firmware")

    @property
    def clave_hmac_bytes(self) -> bytes:
        return bytes.fromhex(self.clave_hmac)

    def nueva_clave_mqtt(self) -> str:
        """Le da una contraseña MQTT nueva y devuelve el texto en claro (solo se
        guarda su hash). La anterior deja de valer al regenerar las credenciales."""
        from transporte.sync_v2 import mqtt

        clave = mqtt.nueva_clave()
        self.mqtt_clave = mqtt.hash_mosquitto(clave)
        self.save(update_fields=["mqtt_clave"])
        return clave

    def rotar_clave_hmac(self) -> str:
        """Le da una clave nueva. La anterior deja de valer en cuanto se guarda."""
        self.clave_hmac = nueva_clave_hmac()
        self.save(update_fields=["clave_hmac"])
        return self.clave_hmac


class CambioLista(models.Model):
    """Un cambio de lista negra o de recargas, con versión global creciente (C1 5.5).

    Los validadores piden los cambios desde su version_listas. a, b y c son los
    campos de la entrada de 16 B: BLOQUEO → a = id de tarjeta; RECARGA → a = id
    de cuenta, b = seq, c = monto en céntimos."""

    class Tipo(models.IntegerChoices):
        BLOQUEO = 1, "Bloqueo"
        RECARGA = 2, "Recarga"

    # op de BLOQUEO: 1 = agregar a la lista negra, 2 = quitar.
    # op de RECARGA: 1 = pendiente, 2 = ya aplicada (borrar).
    AGREGAR, QUITAR = 1, 2
    PENDIENTE, APLICADA = 1, 2

    version = models.BigAutoField(primary_key=True)
    tipo = models.PositiveSmallIntegerField(choices=Tipo.choices)
    op = models.PositiveSmallIntegerField()
    a = models.PositiveBigIntegerField()
    b = models.PositiveBigIntegerField(default=0)
    c = models.PositiveBigIntegerField(default=0)
    urgente = models.BooleanField(default=False,
                                  help_text="Se publica además como LISTAS urgente a todos")
    creado = models.DateTimeField(default=timezone.now)
    difundido_en = models.DateTimeField(null=True, blank=True,
                                        help_text="Cuándo se publicó el paquete urgente")

    class Meta:
        ordering = ["version"]
        verbose_name = "cambio de lista"
        verbose_name_plural = "cambios de lista"
        constraints = [
            models.CheckConstraint(condition=models.Q(tipo__in=[1, 2]),
                                   name="cambio_tipo_valido"),
            models.CheckConstraint(condition=models.Q(op__in=[1, 2]), name="cambio_op_valida"),
            # Los campos van como uint32 en la entrada de C1
            models.CheckConstraint(
                condition=models.Q(a__lte=0xFFFFFFFF, b__lte=0xFFFFFFFF, c__lte=0xFFFFFFFF),
                name="cambio_campos_uint32"),
        ]
        indexes = [
            models.Index(fields=["urgente", "difundido_en"], name="cambio_urgente_idx",
                         condition=models.Q(urgente=True, difundido_en__isnull=True)),
        ]

    def __str__(self):
        return f"v{self.version} · {self.get_tipo_display()} op {self.op} · {self.a}"


class EventoRecibido(models.Model):
    """Id de evento ya recibido de un validador: reenviarlo no hace nada (C1 5.7)."""

    class Via(models.TextChoices):
        MQTT = "mqtt", "MQTT"
        HTTP = "http", "HTTP"
        BLE = "ble", "BLE (celular del conductor)"
        ESPNOW = "espnow", "ESP-NOW (otro bus)"

    validador = models.ForeignKey(Validador, on_delete=models.CASCADE,
                                  related_name="eventos_recibidos")
    id_evento = models.PositiveBigIntegerField()
    recibido = models.DateTimeField(auto_now_add=True)
    via = models.CharField(max_length=6, choices=Via.choices)

    class Meta:
        verbose_name = "evento recibido"
        verbose_name_plural = "eventos recibidos"
        constraints = [
            models.UniqueConstraint(fields=["validador", "id_evento"],
                                    name="evento_recibido_unico"),
        ]

    def __str__(self):
        return f"Validador {self.validador_id} · evento {self.id_evento} ({self.via})"
