from django.contrib import admin

from .models import CodigoActivacion


@admin.register(CodigoActivacion)
class CodigoActivacionAdmin(admin.ModelAdmin):
    list_display = ("tarjeta", "emitido_por", "expira", "usado_en", "anulado_en", "intentos")
    list_filter = ("anulado_en", "usado_en")
    search_fields = ("tarjeta__id", "emitido_por__username")
    readonly_fields = ("tarjeta", "emitido_por", "expira", "usado_en", "anulado_en", "intentos")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
