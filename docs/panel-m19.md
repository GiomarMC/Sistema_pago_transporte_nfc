# Panel de operadores M19

El panel muestra órdenes y la última conciliación en `/panel/pagos/ordenes/`, el último resultado de `verificar_libro` en el inicio, el estado de validadores en `/panel/validadores/` y las reposiciones en `/panel/reposiciones/`.

El registro de HOLA disponible hoy es `POST /api/validadores/hola/` con el token del validador y JSON `{canal, version_listas, pendientes, consumo_dia, firmware}`. `consumo_dia` se expresa en bytes. El panel considera en línea un HOLA recibido en los últimos seis minutos o una conexión reciente registrada por `registrar_conexion()`. El puente MQTT y el paquete C1 de M9 todavía no existen; deberán llamar a esos servicios cuando se implementen. No se muestran datos de canal o consumo inventados para equipos que nunca enviaron HOLA.

La ruta mínima del cliente es `GET/POST /api/v1/reposiciones/` y el estado también se ve en `/mi/reposiciones/`. Solo se solicitan reposiciones para una tarjeta bloqueada propia. «Marcar lista» envía un correo después de confirmar la transición y muestra el aviso en el portal; «Entregada» actualiza ambos lugares. En desarrollo, el correo sale por consola. Para enviar correo real configure `DJANGO_EMAIL_BACKEND`, `DJANGO_EMAIL_HOST`, `DJANGO_EMAIL_PORT`, `DJANGO_EMAIL_USER`, `DJANGO_EMAIL_PASSWORD`, `DJANGO_EMAIL_USE_TLS` y `DJANGO_DEFAULT_FROM_EMAIL`.

El botón de reembolso exige operador y motivo, pero el servicio M11 rechaza todo reembolso automático mientras una recarga pueda estar almacenada en validadores desconectados. El panel muestra el motivo y no cambia saldo ni pasarela. La verificación y la conciliación conservan un resultado por ejecución; el panel presenta la última.
