"""Envío de notificaciones por correo a través de una bandeja de salida persistente.

Uso:
    notificar("ana@example.com", "otp", codigo="123456", minutos=5)

Cada plantilla vive en la app y tiene dos versiones:
  - notificaciones/<tipo>.txt   → la primera línea es el asunto; el resto, el texto plano.
  - notificaciones/<tipo>.html  → la versión HTML.

Reglas de envío:
  - El correo se intenta enviar solo después de confirmar la transacción (transaction.on_commit),
    así una transacción revertida no produce mensajes.
  - Los OTP (tipo "otp") se intentan enviar inmediatamente al confirmar.
  - Las demás notificaciones se envían en orden de creación. Si una falla o está esperando su
    reintento, las siguientes esperan: la cola nunca se salta un mensaje.
  - Un fallo de SMTP deja la notificación en "pendiente" con el error registrado y no hace
    fallar la operación que la originó.
  - Reintentos: tras cada fallo se espera 5, 15, 60 y 240 minutos (ESPERAS_MINUTOS).
    Con MAX_INTENTOS = 5 intentos en total, el quinto fallo la deja "fallida" y crea una
    Alerta. Lo ejecuta el comando `enviar_notificaciones`, por cron cada 5 minutos.
  - Cada ejecución procesa como máximo LIMITE_POR_EJECUCION notificaciones.
"""

import functools
import logging
from datetime import timedelta

from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template import Context
from django.template.loader import get_template
from django.utils import timezone

from transporte.models import Alerta

from .models import Notificacion

logger = logging.getLogger(__name__)

TIPO_OTP = "otp"
MAX_INTENTOS = 5
ESPERAS_MINUTOS = [5, 15, 60, 240]  # tras el 1.º, 2.º, 3.º y 4.º fallo
LIMITE_POR_EJECUCION = 50
ALERTA_FALLIDA = "notificacion fallida"


def espera_tras_fallo(intentos):
    """Tiempo que se espera antes del siguiente intento, tras el fallo número `intentos`."""
    return timedelta(minutes=ESPERAS_MINUTOS[intentos - 1])


def _renderizar(tipo, contexto):
    """Devuelve (asunto, texto, html) a partir de las plantillas del tipo indicado."""
    # El texto plano no se escapa: un "&" o "<" debe llegar tal cual.
    txt = get_template(f"notificaciones/{tipo}.txt").template.render(
        Context(contexto, autoescape=False)
    )
    html = get_template(f"notificaciones/{tipo}.html").render(contexto)

    lineas = txt.strip().splitlines()
    if not lineas or not lineas[0].strip():
        raise ValueError(f"La plantilla notificaciones/{tipo}.txt debe empezar con el asunto")
    asunto = lineas[0].strip()
    texto = "\n".join(lineas[1:]).strip()
    return asunto, texto, html


def notificar(destinatario, tipo, **contexto):
    """Guarda la notificación y programa su envío para cuando se confirme la transacción."""
    asunto, texto, html = _renderizar(tipo, contexto)
    notificacion = Notificacion.objects.create(
        destinatario=destinatario, tipo=tipo, asunto=asunto, texto=texto, html=html
    )
    if tipo == TIPO_OTP:
        transaction.on_commit(functools.partial(_intentar_envio, notificacion.pk))
    else:
        transaction.on_commit(functools.partial(_procesar, incluir_otp=False))
    return notificacion


def _enviar_correo(notificacion):
    mensaje = EmailMultiAlternatives(
        subject=notificacion.asunto,
        body=notificacion.texto,
        to=[notificacion.destinatario],
    )
    mensaje.attach_alternative(notificacion.html, "text/html")
    mensaje.send()


def _intentar_envio(pk, ahora=None):
    """Intenta enviar una notificación. Devuelve False solo si el envío falló (bloquea la cola)."""
    ahora = ahora or timezone.now()
    with transaction.atomic():
        # Bloquea la fila para que dos procesos no envíen el mismo correo.
        notificacion = Notificacion.objects.select_for_update().get(pk=pk)
        if notificacion.estado != Notificacion.PENDIENTE:
            return True

        notificacion.intentos += 1
        try:
            _enviar_correo(notificacion)
        except Exception as exc:  # un fallo de envío nunca debe tumbar la operación original
            notificacion.error = f"{type(exc).__name__}: {exc}"
            if notificacion.intentos >= MAX_INTENTOS:
                notificacion.estado = Notificacion.FALLIDA
                notificacion.proximo_intento = None
                Alerta.objects.create(
                    tipo=ALERTA_FALLIDA,
                    detalle=(f"Notificación {pk} ({notificacion.tipo} a {notificacion.destinatario}) "
                             f"fallida tras {MAX_INTENTOS} intentos: {notificacion.error}"),
                )
            else:
                notificacion.proximo_intento = ahora + espera_tras_fallo(notificacion.intentos)
            notificacion.save(update_fields=["intentos", "error", "estado", "proximo_intento"])
            logger.warning(
                "No se pudo enviar la notificación %s (intento %s): %s",
                pk, notificacion.intentos, notificacion.error,
            )
            return False

        notificacion.estado = Notificacion.ENVIADA
        notificacion.enviada_en = ahora
        notificacion.proximo_intento = None
        notificacion.save(update_fields=["intentos", "estado", "enviada_en", "proximo_intento"])
        return True


def _procesar(ahora=None, limite=LIMITE_POR_EJECUCION, incluir_otp=True):
    """Intenta enviar las pendientes disponibles, en orden de creación.

    - Una notificación que todavía espera su reintento se salta si es OTP; si no lo es,
      detiene la cola.
    - Un fallo de envío de una notificación que no es OTP también detiene la cola.
    Devuelve cuántas notificaciones se intentaron (como mucho `limite`).
    """
    ahora = ahora or timezone.now()
    pendientes = (
        Notificacion.objects.filter(estado=Notificacion.PENDIENTE)
        .order_by("creada", "pk")
        .values_list("pk", "tipo", "proximo_intento")
    )
    intentadas = 0
    for pk, tipo, proximo in pendientes:
        if intentadas >= limite:
            break
        es_otp = tipo == TIPO_OTP
        if es_otp and not incluir_otp:
            continue
        if proximo is not None and proximo > ahora:
            if es_otp:
                continue
            break
        intentadas += 1
        if not _intentar_envio(pk, ahora) and not es_otp:
            break
    return intentadas


def procesar_pendientes(ahora=None, limite=LIMITE_POR_EJECUCION):
    """Reintenta las notificaciones pendientes que ya están disponibles.

    Lo usa el comando `enviar_notificaciones`. Devuelve cuántas se intentaron.
    """
    return _procesar(ahora=ahora, limite=limite, incluir_otp=True)
