"""Contextos de ejemplo para cada plantilla: los usan las pruebas y el comando de previsualización."""

MUESTRAS = {
    "otp": {"codigo": "482913", "minutos": 5},
    "aviso": {"titulo": "Mantenimiento programado", "mensaje": "El servicio estará lento el sábado."},
    "activacion_completa": {"nombre": "Ana Quispe"},
    "nuevo_inicio_sesion": {
        "nombre": "Ana Quispe", "fecha": "06/11/2026 10:32", "dispositivo": "Android · Chrome",
    },
    "contrasena_cambiada": {"nombre": "Ana Quispe", "fecha": "06/11/2026 10:32"},
    "recarga_acreditada": {"nombre": "Ana Quispe", "monto": "S/ 20.00", "fecha": "06/11/2026 10:32"},
    "tarjeta_bloqueada": {"nombre": "Ana Quispe", "tarjeta": 12, "fecha": "06/11/2026 10:32"},
    "uso_tarjeta_bloqueada": {"nombre": "Ana Quispe", "tarjeta": 12, "fecha": "06/11/2026 10:35"},
    "saldo_bajo": {"nombre": "Ana Quispe", "saldo": "S/ 1.50", "tarifa": "S/ 1.00"},
    "reposicion_lista": {"nombre": "Ana Quispe", "tarjeta": 13},
    "correo_cambiado": {
        "nombre": "Ana Quispe", "correo_nuevo": "ana.nueva@example.com", "fecha": "06/11/2026 10:32",
    },
}
