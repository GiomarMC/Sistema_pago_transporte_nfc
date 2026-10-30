from django.contrib import admin

from .models import ClienteCuenta, CobroSimulado, EventoPasarela, HistorialOrden, OrdenRecarga


class SoloLectura(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OrdenRecarga)
class OrdenAdmin(SoloLectura):
    list_display = ("id", "cuenta", "monto", "estado", "pasarela", "creada")
    readonly_fields = [f.name for f in OrdenRecarga._meta.fields]


@admin.register(EventoPasarela)
class EventoAdmin(SoloLectura):
    list_display = ("pasarela", "id_evento", "tipo", "firma_valida", "recibido", "procesado_en")
    readonly_fields = [f.name for f in EventoPasarela._meta.fields]


@admin.register(HistorialOrden)
class HistorialAdmin(SoloLectura):
    list_display = ("orden", "de", "a", "fecha", "motivo")
    readonly_fields = [f.name for f in HistorialOrden._meta.fields]


admin.site.register(ClienteCuenta)
admin.site.register(CobroSimulado, SoloLectura)
