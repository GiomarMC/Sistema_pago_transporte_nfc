import hashlib
import json
from uuid import UUID

from django.db import IntegrityError, transaction
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from transporte.models import Alerta

from .models import CobroSimulado, EventoPasarela, OrdenRecarga
from .pasarelas import obtener_pasarela
from .pasarelas.base import ErrorPasarela, FirmaInvalida
from .pasarelas.simulada import firmar
from .servicios import confirmar_cobro


@csrf_exempt
@require_http_methods(["POST"])
def webhook(request, pasarela):
    try:
        adaptador = obtener_pasarela(pasarela)
    except ErrorPasarela:
        return HttpResponse(status=404)
    cuerpo = request.body
    try:
        evento = adaptador.verificar_webhook(request.headers, cuerpo)
    except FirmaInvalida:
        # Sin identidad confiable, se conserva una huella para auditoría sin
        # permitir que el atacante reclame el ID de un evento válido futuro.
        EventoPasarela.objects.create(
            pasarela=pasarela, id_evento="invalido-" + hashlib.sha256(cuerpo + str(timezone.now()).encode()).hexdigest(),
            cuerpo=cuerpo, firma_valida=False)
        return HttpResponse(status=400)
    except ErrorPasarela:
        return HttpResponse(status=400)
    try:
        with transaction.atomic():
            registro, creado = EventoPasarela.objects.get_or_create(
                pasarela=pasarela, id_evento=evento.id_evento,
                defaults={"tipo": evento.tipo, "cuerpo": cuerpo, "firma_valida": True})
    except IntegrityError:
        registro = EventoPasarela.objects.get(pasarela=pasarela, id_evento=evento.id_evento)
        creado = False
    if not creado:
        if registro.cuerpo != cuerpo or registro.tipo != evento.tipo:
            Alerta.objects.create(tipo="pago inconsistente", detalle=f"Evento {evento.id_evento} reutilizado")
            return HttpResponse(status=409)
        if registro.procesado_en:
            return HttpResponse(status=200)
    orden = OrdenRecarga.objects.filter(pasarela=pasarela, id_externo=evento.id_externo).first()
    if not orden:
        Alerta.objects.create(tipo="pago inconsistente", detalle=f"Cobro desconocido {evento.id_externo}")
        registro.procesado_en = timezone.now()
        registro.save(update_fields=["procesado_en"])
        return HttpResponse(status=200)
    try:
        if evento.tipo == "cobro.pagado":
            confirmado = confirmar_cobro(orden)
            if not confirmado:
                # El evento queda disponible para reintento si la pasarela aún no confirma.
                return HttpResponse(status=202)
        elif evento.tipo == "cobro.fallido":
            from .servicios import revisar_orden
            revisar_orden(orden)
        elif evento.tipo == "reembolso.hecho":
            # El simulador concluye el reembolso en la llamada autorizada al servicio.
            # Un proveedor real deberá validar su ID y estado antes de finalizar.
            pass
        else:
            return HttpResponse(status=400)
    except ErrorPasarela:
        return HttpResponse(status=503)
    registro.procesado_en = timezone.now()
    registro.save(update_fields=["procesado_en"])
    return HttpResponse(status=200)


@require_http_methods(["GET", "POST"])
def simulador(request, id_externo):
    cobro = get_object_or_404(CobroSimulado, pk=id_externo)
    if request.method == "GET":
        return render(request, "pagos/simulador.html", {"cobro": cobro})
    if cobro.estado != "pendiente" or cobro.expira <= timezone.now():
        return HttpResponse(status=409)
    decision = request.POST.get("decision")
    if decision not in ("aprobar", "rechazar"):
        return HttpResponse(status=400)
    with transaction.atomic():
        cobro = CobroSimulado.objects.select_for_update().get(pk=id_externo)
        if cobro.estado != "pendiente":
            return HttpResponse(status=409)
        cobro.estado = "pagado" if decision == "aprobar" else "fallido"
        cobro.pagado_en = timezone.now() if decision == "aprobar" else None
        cobro.save(update_fields=["estado", "pagado_en"])
    if request.POST.get("webhook") != "ninguno":
        tipo = "cobro.pagado" if decision == "aprobar" else "cobro.fallido"
        veces = 2 if request.POST.get("webhook") == "doble" else 1
        cuerpo = json.dumps({"id_evento": str(cobro.pk) + "-" + tipo,
                             "tipo": tipo, "id_externo": str(cobro.pk)}).encode()
        # En el simulador local, se programa el mismo procesamiento tras confirmar
        # el cobro; no se hacen solicitudes HTTP hacia un host arbitrario.
        def entregar():
            from django.test import RequestFactory
            import time
            time.sleep(1)
            for _ in range(veces):
                req = RequestFactory().post(reverse("pagos:webhook", args=["simulada"]),
                                            data=cuerpo, content_type="application/json",
                                            **{"HTTP_" + k.upper().replace("-", "_"): v
                                               for k, v in firmar(cuerpo).items()})
                webhook(req, "simulada")
        transaction.on_commit(entregar)
    return HttpResponseRedirect(cobro.url_retorno or reverse("simulador", args=[cobro.pk]))
