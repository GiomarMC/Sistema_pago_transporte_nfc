from django.http import HttpResponse, JsonResponse


def inicio(request):
    return HttpResponse("SubePe — portal de clientes", content_type="text/plain; charset=utf-8")


def verificar_sun(request):
    # Verificación del mensaje SUN (URL que genera la tarjeta al acercarla a un celular)
    return JsonResponse({"error": {"codigo": "no_implementado",
                                   "mensaje": "Verificación SUN aún no disponible"}}, status=501)
