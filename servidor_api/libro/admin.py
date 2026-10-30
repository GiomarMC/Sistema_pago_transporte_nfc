from django.contrib import admin

from .models import Asiento, CuentaContable, Partida


class PartidaInline(admin.TabularInline):
    model = Partida
    extra = 0
    can_delete = False
    readonly_fields = ("cuenta_contable", "monto")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Asiento)
class AsientoAdmin(admin.ModelAdmin):
    list_display = ("fecha", "tipo", "ref", "descripcion")
    search_fields = ("ref",)
    readonly_fields = ("fecha", "tipo", "ref", "descripcion", "creado_por")
    inlines = (PartidaInline,)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CuentaContable)
class CuentaContableAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "naturaleza", "cuenta_cliente")
    search_fields = ("codigo",)
    readonly_fields = ("codigo", "nombre", "naturaleza", "cuenta_cliente")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
