# Panel de operadores M19

El panel muestra órdenes y la última conciliación en `/panel/pagos/ordenes/`, el último resultado de `verificar_libro` en el inicio, el estado de validadores en `/panel/validadores/` y las reposiciones en `/panel/reposiciones/`.

El estado de cada validador sale de lo que ya guarda el servidor: el último HOLA de C1 (`Validador.ultimo_hola`, escrito por `procesar_paquete`, tanto por HTTP como por MQTT) y el estado de conexión del tópico `sp/v/{n}/conexion` (`registrar_conexion()`). De ahí se leen el canal (el enlace del HOLA; el código 2, 4G, se muestra como desconocido), los eventos pendientes, el consumo del día (bytes enviados y recibidos), la versión de firmware y la versión de listas frente a la actual. El panel considera en línea un HOLA recibido en los últimos seis minutos o una conexión reciente. No se muestran datos inventados para equipos que nunca enviaron HOLA.

La API del cliente es `GET/POST /api/v1/reposiciones/`; el estado de la solicitud se ve en «Mi tarjeta» del portal (`/tarjeta/` en el host del cliente). La ruta antigua `/mi/reposiciones/` ya no es una página: redirige (301) a ese lugar. Solo se solicitan reposiciones para una tarjeta bloqueada propia. «Marcar lista» crea el aviso `reposicion_lista` en la bandeja de salida de M13, que se envía por correo si la cuenta tiene un correo verificado; «Entregada» actualiza el estado visible para el cliente. El correo usa `CORREO_USUARIO` y `CORREO_CLAVE_APP` (sin ellas sale por consola); ver «Correo electrónico (Gmail)» en el README.

Las recargas no son reembolsables (#102, ver `docs/pagos.md`): el panel no tiene botón de reembolso. La verificación y la conciliación conservan un resultado por ejecución; el panel presenta la última.
