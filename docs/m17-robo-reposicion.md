# M17: bloqueo, reposición y propagación

## Flujo disponible en el servidor

`POST /api/v1/tarjetas/{id}/bloquear/` exige sesión del titular, contraseña y motivo `robo` o `perdida`. Usa `transporte.servicios.bloquear`: en una transacción cambia la tarjeta, registra un `CambioLista` **urgente** (que `puente_mqtt` difunde a todos los validadores por `sp/todos/urgente`) y deja la notificación `tarjeta_bloqueada` en la bandeja de salida; el correo sale después del commit. Una repetición del bloqueo responde 409 y no crea otro cambio ni otro aviso. Un viaje offline posterior al bloqueo se contabiliza cuando llega al servidor, genera la alerta `uso de tarjeta no activa` y el aviso `uso_tarjeta_bloqueada` una sola vez por referencia. El bloqueo no llega a un validador desconectado hasta que se conecte y sincronice.

`python3 transporte/emitir_tarjeta.py --cuenta N` usa la emisión existente. Tras la verificación física y la confirmación en el servidor, las solicitudes abiertas de esa cuenta pasan a `entregada`, guardan la tarjeta nueva, la fecha y el operador. Una emisión fallida deja las solicitudes abiertas. Si la cuenta no tiene `clientes.Cliente`, se entrega un código de activación de un solo uso; con `Cliente` existente, no se genera código. `/api/v1/resumen/`, la API de reposiciones y el panel muestran la tarjeta nueva y su estado. El aviso `reposicion_lista` se crea solo si la solicitud todavía estaba `solicitada`; si ya estaba `lista`, no se duplica.

## Propagación física pendiente (M17-02)

El servidor ya publica el cambio urgente por MQTT (M9), pero faltan las implementaciones de difusión por WiFi, BLE y ESP-NOW del firmware (M15 y M8). Por eso no hay latencia medida y no se declara cumplido el objetivo de menos de un minuto por WiFi.

Cuando estén disponibles los canales, bloquear una tarjeta de prueba y medir desde el commit del bloqueo hasta que cada validador confirme la versión aplicada. Repetir cinco veces por canal con reloj sincronizado. Registrar valores en segundos y calcular mediana y máximo:

| Canal | 1 | 2 | 3 | 4 | 5 | Mediana | Máximo |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| WiFi | pendiente | pendiente | pendiente | pendiente | pendiente | pendiente | pendiente |
| BLE | pendiente | pendiente | pendiente | pendiente | pendiente | pendiente | pendiente |
| ESP-NOW | pendiente | pendiente | pendiente | pendiente | pendiente | pendiente | pendiente |

No se debe inferir recepción a partir de que el servidor creó el cambio; la medición termina con la confirmación de aplicación por el validador. También falta la prueba manual con NTAG215 de la reposición en mostrador.
