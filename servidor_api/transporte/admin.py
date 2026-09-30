from django.contrib import admin, messages

from . import servicios
from .models import Alerta, Cuenta, Movimiento, Recarga, Tarifa, Tarjeta, Validador, soles


@admin.register(Tarifa)
class TarifaAdmin(admin.ModelAdmin):
    list_display = ["codigo", "nombre", "precio_soles"]

    @admin.display(description="precio")
    def precio_soles(self, obj):
        return soles(obj.precio)


class TarjetaInline(admin.TabularInline):
    model = Tarjeta
    extra = 0
    fields = ["id", "uid", "estado", "emitida", "bloqueada_en"]
    readonly_fields = fields
    can_delete = False
    show_change_link = True


class MovimientoInline(admin.TabularInline):
    model = Movimiento
    extra = 0
    fields = ["fecha", "tipo", "monto", "saldo_final", "tarjeta", "validador", "ref"]
    readonly_fields = fields
    can_delete = False
    ordering = ["-fecha", "-id"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Cuenta)
class CuentaAdmin(admin.ModelAdmin):
    list_display = ["id", "nombre", "documento", "tarifa", "saldo_soles", "creada"]
    search_fields = ["nombre", "documento"]
    list_filter = ["tarifa"]
    # El saldo solo cambia con movimientos (recargas, viajes), nunca a mano
    readonly_fields = ["saldo"]
    inlines = [TarjetaInline, MovimientoInline]

    @admin.display(description="saldo", ordering="saldo")
    def saldo_soles(self, obj):
        return soles(obj.saldo)


@admin.register(Tarjeta)
class TarjetaAdmin(admin.ModelAdmin):
    list_display = ["id", "uid", "cuenta", "estado", "emitida", "bloqueada_en"]
    list_filter = ["estado"]
    search_fields = ["uid", "cuenta__nombre", "cuenta__documento"]
    readonly_fields = ["uid", "cuenta", "estado", "emitida", "bloqueada_en", "recarga_inicial",
                       "creo_cuenta"]
    actions = ["bloquear"]

    @admin.action(description="Bloquear (robo o pérdida)")
    def bloquear(self, request, queryset):
        n = 0
        for t in queryset.filter(estado=Tarjeta.Estado.ACTIVA):
            servicios.bloquear(t, f"bloqueada desde el admin por {request.user}",
                               tipo="bloqueo manual")
            n += 1
        self.message_user(request, f"{n} tarjeta(s) bloqueada(s). Los validadores lo recibirán "
                                   "en su próxima sincronización.", messages.SUCCESS)

    def has_add_permission(self, request):
        return False  # las tarjetas se emiten con el programa de emisión


@admin.register(Recarga)
class RecargaAdmin(admin.ModelAdmin):
    list_display = ["id", "cuenta", "seq", "monto_soles", "origen", "creada", "aplicada_en",
                    "aplicada_por"]
    list_filter = ["origen", ("aplicada_en", admin.EmptyFieldListFilter)]
    readonly_fields = [f.name for f in Recarga._meta.fields]

    @admin.display(description="monto")
    def monto_soles(self, obj):
        return soles(obj.monto)

    def has_add_permission(self, request):
        return False


@admin.register(Movimiento)
class MovimientoAdmin(admin.ModelAdmin):
    list_display = ["fecha", "tipo", "cuenta", "tarjeta", "monto_soles", "saldo_final_soles",
                    "operacion", "validador", "contador", "ref"]
    list_filter = ["tipo", "validador"]
    search_fields = ["ref", "cuenta__documento"]
    readonly_fields = [f.name for f in Movimiento._meta.fields]

    @admin.display(description="monto")
    def monto_soles(self, obj):
        return soles(obj.monto)

    @admin.display(description="saldo final")
    def saldo_final_soles(self, obj):
        return soles(obj.saldo_final)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Alerta)
class AlertaAdmin(admin.ModelAdmin):
    list_display = ["fecha", "tipo", "tarjeta", "detalle", "revisada"]
    list_filter = ["revisada", "tipo"]
    list_editable = ["revisada"]
    readonly_fields = ["fecha", "tipo", "tarjeta", "detalle"]


@admin.register(Validador)
class ValidadorAdmin(admin.ModelAdmin):
    list_display = ["numero", "descripcion", "activo", "ultima_sync"]
    list_editable = ["activo"]
    readonly_fields = ["ultima_sync"]
