import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from pagos.models import CobroSimulado

from .base import CobroCreado, ErrorPasarela, EstadoCobro, EventoPasarela, FirmaInvalida, Transaccion


def firmar(cuerpo, instante=None):
    instante = int(time.time() if instante is None else instante)
    firma = hmac.new(settings.PAGOS_WEBHOOK_SECRET.encode(),
                     str(instante).encode() + b"." + cuerpo, hashlib.sha256).hexdigest()
    return {"X-Pagos-Timestamp": str(instante), "X-Pagos-Signature": firma}


class PasarelaSimulada:
    nombre = "simulada"

    def crear_cobro(self, *, orden_id, monto, descripcion, correo, url_retorno):
        expira = timezone.now() + timedelta(minutes=30)
        cobro, _ = CobroSimulado.objects.get_or_create(
            orden_id=orden_id, defaults={"monto": monto, "expira": expira,
                                          "url_retorno": url_retorno})
        if cobro.monto != monto:
            raise ErrorPasarela("monto del cobro inconsistente")
        base = settings.PAGOS_SIMULADOR_BASE_URL.rstrip("/")
        return CobroCreado(str(cobro.pk), f"{base}/pagos/simulador/{cobro.pk}/", cobro.expira)

    def consultar(self, id_externo):
        try:
            cobro = CobroSimulado.objects.get(pk=id_externo)
        except (CobroSimulado.DoesNotExist, ValueError, OverflowError) as exc:
            raise ErrorPasarela("cobro no encontrado") from exc
        estado = "expirado" if cobro.estado == "pendiente" and cobro.expira <= timezone.now() else cobro.estado
        return EstadoCobro(str(cobro.pk), estado, cobro.monto, cobro.pagado_en)

    def verificar_webhook(self, cabeceras, cuerpo):
        try:
            instante = int(cabeceras["X-Pagos-Timestamp"])
            firma = cabeceras["X-Pagos-Signature"]
        except (KeyError, TypeError, ValueError) as exc:
            raise FirmaInvalida("faltan datos de la firma") from exc
        if abs(time.time() - instante) > 300:
            raise FirmaInvalida("marca de tiempo fuera de ventana")
        esperada = firmar(cuerpo, instante)["X-Pagos-Signature"]
        if not hmac.compare_digest(firma, esperada):
            raise FirmaInvalida("firma incorrecta")
        try:
            datos = json.loads(cuerpo)
            return EventoPasarela(str(datos["id_evento"]), str(datos["tipo"]),
                                  str(datos["id_externo"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise ErrorPasarela("evento inválido") from exc

    def reembolsar(self, id_externo, monto):
        from django.db import transaction
        with transaction.atomic():
            cobro = CobroSimulado.objects.select_for_update().get(pk=id_externo)
            if cobro.monto != monto or cobro.estado not in ("pagado", "reembolsado"):
                raise ErrorPasarela("cobro no reembolsable")
            if cobro.id_reembolso is None:
                cobro.id_reembolso = uuid.uuid4()
                cobro.estado = "reembolsado"
                cobro.save(update_fields=["id_reembolso", "estado"])
            return str(cobro.id_reembolso)

    def listar_transacciones(self, dia):
        return [Transaccion(str(c.pk), c.monto, c.estado, c.pagado_en)
                for c in CobroSimulado.objects.filter(pagado_en__date=dia)
                if c.pagado_en]
