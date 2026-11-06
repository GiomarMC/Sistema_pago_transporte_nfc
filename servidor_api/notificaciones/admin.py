from django.contrib import admin

from .models import Notificacion


@admin.register(Notificacion)
class NotificacionAdmin(admin.ModelAdmin):
    list_display = ("destinatario", "tipo", "asunto", "estado", "intentos", "creada", "enviada_en")
    list_filter = ("estado", "tipo")
    search_fields = ("destinatario", "asunto")
    readonly_fields = ("creada", "enviada_en", "intentos", "error")
