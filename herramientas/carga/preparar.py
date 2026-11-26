# Se ejecuta dentro del contenedor web (manage.py shell -c): crea los validadores de la prueba de carga,
# cada uno con su cuenta, su tarjeta activa (saldo S/ 200) y su contraseña MQTT, y escribe en la salida
# estándar el JSON de credenciales que usa herramientas/carga_mqtt.py. Solo para la copia "carga".
import json
import os
import secrets

from django.contrib.auth import get_user_model

from transporte import servicios
from transporte.models import Cuenta, Tarifa, Tarjeta, Validador

N = int(os.environ.get("CARGA_VALIDADORES", "50"))
PRIMERO = int(os.environ.get("CARGA_PRIMERO", "801"))
tarifa = Tarifa.objects.order_by("codigo").first()
credenciales = {}
for numero in range(PRIMERO, PRIMERO + N):
    usuario, _ = get_user_model().objects.get_or_create(username=f"validador-{numero}")
    validador, _ = Validador.objects.get_or_create(numero=numero, defaults={"usuario": usuario})
    cuenta = Cuenta.objects.create(nombre=f"Carga {numero}", documento=f"C{numero}", tarifa=tarifa)
    uid = "04" + secrets.token_hex(6).upper()
    tarjeta = Tarjeta.objects.create(uid=uid, cuenta=cuenta, estado=Tarjeta.Estado.ACTIVA)
    servicios.crear_recarga(cuenta.id, 20000, "punto")
    credenciales[str(numero)] = {"hmac": validador.clave_hmac, "mqtt": validador.nueva_clave_mqtt(),
                                 "tarjeta": tarjeta.id, "uid": uid}
print(json.dumps(credenciales))
