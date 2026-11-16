"""Libro mayor en el admin: todo de solo lectura (se registra desde los servicios)."""

from django.contrib import admin
from django.db.models import Sum

from transporte.models import soles

from .models import Asiento, CuentaContable, Partida


class SoloLectura:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PartidaInline(SoloLectura, admin.TabularInline):
    model = Partida
    fields = ["cuenta_contable", "monto", "monto_soles"]
    readonly_fields = fields
    extra = 0

    @admin.display(description="en soles")
    def monto_soles(self, obj):
        return soles(obj.monto)


@admin.register(Asiento)
class AsientoAdmin(SoloLectura, admin.ModelAdmin):
    list_display = ["ref", "tipo", "fecha", "descripcion", "creado_por"]
    list_filter = ["tipo"]
    search_fields = ["ref", "descripcion"]
    date_hierarchy = "fecha"
    readonly_fields = ["fecha", "registrado", "tipo", "ref", "descripcion", "creado_por"]
    inlines = [PartidaInline]


@admin.register(CuentaContable)
class CuentaContableAdmin(SoloLectura, admin.ModelAdmin):
    list_display = ["codigo", "nombre", "naturaleza", "saldo_soles"]
    list_filter = ["naturaleza"]
    search_fields = ["codigo", "nombre"]
    readonly_fields = ["codigo", "nombre", "naturaleza", "cuenta_cliente"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(suma=Sum("partidas__monto"))

    @admin.display(description="suma de partidas", ordering="suma")
    def saldo_soles(self, obj):
        return soles(obj.suma or 0)
