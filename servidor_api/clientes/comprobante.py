"""Comprobante sin persistir ni volver a leer el código en claro."""
from io import BytesIO

import segno
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.safestring import mark_safe

from .models import CodigoActivacion, numero_impreso

URL_ACTIVACION = "https://mi.subepe.app/activar"


def _dni_enmascarado(documento):
    return "•" * max(0, len(documento) - 2) + documento[-2:]


def comprobante(request, tarjeta, codigo):
    registro = CodigoActivacion.objects.filter(
        tarjeta=tarjeta, usado_en__isnull=True, anulado_en__isnull=True).first()
    if not registro or not registro.vigente(codigo):
        return HttpResponse("Código de activación inválido o vencido.", status=409)
    qr = BytesIO()
    segno.make_qr(URL_ACTIVACION, error="m").save(qr, kind="svg", scale=4)
    respuesta = render(request, "panel/comprobante.html", {
        "tarjeta": tarjeta, "numero": numero_impreso(tarjeta), "codigo": codigo,
        "expira": registro.expira, "dni": _dni_enmascarado(tarjeta.cuenta.documento),
        "url_activacion": URL_ACTIVACION, "qr": mark_safe(qr.getvalue().decode("utf-8")),
    })
    respuesta["Cache-Control"] = "no-store"
    respuesta["Referrer-Policy"] = "no-referrer"
    respuesta["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:")
    return respuesta
