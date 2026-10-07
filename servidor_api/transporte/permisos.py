from rest_framework.permissions import BasePermission

GRUPO_OPERADORES = "operadores"
# Cuentas creadas con "Solicitar acceso" en el panel, hasta que un superusuario las aprueba
GRUPO_SOLICITUDES = "solicitudes"


class EsValidador(BasePermission):
    """Solo los dispositivos de bus registrados y activos."""
    message = "solo para validadores registrados y activos"

    def has_permission(self, request, view):
        v = getattr(request.user, "validador", None)
        return v is not None and v.activo


class EsOperador(BasePermission):
    """Personal de emisión, recarga y atención (grupo 'operadores') o administradores."""
    message = "solo para operadores"

    def has_permission(self, request, view):
        u = request.user
        return u.is_authenticated and (
            u.is_staff or u.groups.filter(name=GRUPO_OPERADORES).exists())
