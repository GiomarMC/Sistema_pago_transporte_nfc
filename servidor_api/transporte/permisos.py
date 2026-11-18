from rest_framework.permissions import BasePermission

GRUPO_OPERADORES = "operadores"
GRUPO_CONDUCTORES = "conductores"
# Cuentas creadas con "Solicitar acceso" en el panel, hasta que un superusuario las aprueba
GRUPO_SOLICITUDES = "solicitudes"
# Conductores que retransmiten por BLE los paquetes de su bus (el grupo lo crea M16-01)
GRUPO_CONDUCTORES = "conductores"


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


def es_conductor(usuario) -> bool:
    return usuario.is_authenticated and usuario.groups.filter(name=GRUPO_CONDUCTORES).exists()


class EsConductorOValidador(BasePermission):
    """Sincronización v2: el validador (WiFi) o el celular de un conductor (relé BLE)."""
    message = "solo para validadores activos o conductores"

    def has_permission(self, request, view):
        return EsValidador().has_permission(request, view) or es_conductor(request.user)
