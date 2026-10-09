# Contrato de sincronización v2

**Ruta:** `POST /api/sync/v2/` con `Authorization: Token <token del validador>`.
El número de validador sale del token, nunca del cuerpo. Los montos son céntimos
de sol. Esta ruta se agregó para T1; los clientes actuales siguen usando
`POST /api/sync/` (v1), que todavía devuelve listas completas. **La ESP32 y el
validador Python deben adaptarse en T2 antes de que v2 reduzca el tráfico real.**

## Petición

```json
{
  "cursor": 0,
  "limite": 50,
  "eventos": [
    {"id": 57, "tipo": "viaje", "fecha": "2026-10-09T17:38:05+00:00",
     "tarjeta_id": 4, "uid": "04B3E121CA2A81", "monto": 80,
     "operacion": 2, "contador": 19, "recarga_hasta": 1}
  ]
}
```

`cursor` es la última versión **aplicada y guardada** en el validador (0 para
una copia local nueva). `limite` vale 50 por defecto y admite 1–100 cambios.
`eventos` admite 0–40 elementos por petición. El cuerpo completo no puede
superar 64 KiB. En la primera página se omite `hasta_version`; en las siguientes
se envía el valor devuelto por el servidor para mantener un corte estable.

## Respuesta

```json
{
  "version": 2,
  "validador": 101,
  "aceptados": [57],
  "cambios": [
    {"version": 12, "tipo": "tarjeta", "accion": "poner", "tarjeta_id": 4},
    {"version": 13, "tipo": "recarga", "accion": "poner",
     "cuenta_id": 7, "seq": 3, "monto": 500}
  ],
  "siguiente_cursor": 13,
  "hasta_version": 20,
  "hay_mas": true,
  "hora_servidor": "2026-10-09T17:38:50+00:00"
}
```

El ejemplo muestra una página intermedia: el servidor puede devolver menos
elementos que `limite` en la última página. Los cambios están ordenados por
`version`. El servidor fija `hasta_version` al comenzar una ronda y entrega
solo versiones hasta ese valor, aunque entren cambios nuevos durante la
paginación. Si `hay_mas` es falso, `siguiente_cursor` avanza hasta
`hasta_version`, incluso si la página está vacía. Las versiones nunca se
reutilizan en la base actual.

| `tipo` | `accion=poner` | `accion=quitar` |
|---|---|---|
| `tarjeta` | Añadir `tarjeta_id` a la lista negra | Quitarla de la lista negra; actualmente solo se emite al borrar una tarjeta bloqueada/anulada |
| `recarga` | Guardar `cuenta_id`, `seq`, `monto` | Quitar la recarga identificada por `cuenta_id` y `seq` al entregarla o borrarla |
| `tarifa` | Guardar `codigo`, `nombre`, `precio` | Quitar la tarifa identificada por `codigo` |

`poner` reemplaza el valor previo de la misma clave y `quitar` de una clave
ausente no causa error. Así una página repetida se puede aplicar de nuevo.
La versión es global para todos los validadores; cada uno guarda su propio
cursor local.

## Algoritmo para T2: cliente ESP32 o Python

1. Si es la primera vez en v2 o se perdió el cursor, preparar **una copia local
   vacía** de lista negra, recargas y tarifas. Descargar todas las páginas desde
   `cursor=0` antes de usar esa copia para cobrar. Al migrar desde v1 no mezclar
   la caché v1 con el registro v2: la primera página no es una lista completa.
2. Enviar hasta 40 eventos locales pendientes y el último cursor guardado.
   Confirmar localmente **solo** los IDs de `aceptados`. Si la respuesta se
   pierde, reenviar los mismos IDs: el servidor ignora referencias repetidas.
3. Aplicar `cambios` a la microSD de forma idempotente y guardar
   `siguiente_cursor` junto con ellos en una operación recuperable tras corte de
   energía. Nunca adelantar el cursor antes de persistir los cambios.
4. Si `hay_mas=true`, solicitar la siguiente página con `cursor` igual a
   `siguiente_cursor`, el mismo `hasta_version` y, si no hay eventos nuevos,
   `eventos=[]`. Si `hay_mas=false`, terminar la ronda; la siguiente petición
   omite `hasta_version` para obtener un corte nuevo.
5. Si una descarga inicial se interrumpe, continuar desde el cursor ya
   persistido en la copia de preparación. Activarla para cobrar solo cuando
   termine la ronda inicial. Después, si no hay red, seguir con la última
   copia completa y guardar viajes para reenvío.

**Errores:** `400` para cursor, corte o límite inválido; `401` sin token;
`403` con token de operador o validador desactivado; `413` para cuerpo mayor de
64 KiB. Un cursor futuro puede aparecer después de restaurar la base desde una
copia antigua: detener la sincronización y reconstruir la caché desde cero,
sin borrar la cola local de eventos no confirmados.

## Cómo se mantiene el registro en el servidor

La migración `0006_sync_incremental` carga el estado existente de tarjetas
bloqueadas/anuladas, recargas pendientes y tarifas. Desde entonces, los
servicios de emisión, bloqueo, anulación y recarga escriben cada cambio en la
misma transacción que el estado de negocio. Una fila de `EstadoSync` bloqueada
serializa las versiones para que el cursor no salte una transacción que aún no
termina. Cambios de tarifa por `.save()` y borrados excepcionales tienen
señales; **no usar `QuerySet.update()` ni SQL directo para cambiar estados** sin
registrar también el cambio. El comando `importar_sqlite` registra los datos
que importa.

El registro no se purga en esta versión: un validador nuevo puede empezar en
cursor 0. Una política futura de retención deberá ofrecer una nueva copia
inicial verificable antes de borrar versiones antiguas. Para bases con muchos
datos, planificar tiempo y copia de seguridad antes de aplicar la migración de
carga inicial.

## Compatibilidad y despliegue

1. Desplegar T1 y aplicar la migración. Los clientes v1 siguen funcionando sin
   cambiar el firmware; comprobar que `/api/sync/` devuelve lo mismo.
2. Adaptar y probar un validador a la vez con este contrato (T2). Comparar
   bloqueos, recargas, tarifas y viajes confirmados con el servidor.
3. Medir tamaño de respuesta, tiempo de sincronización y memoria de ESP32 con
   T3. Tras migrar todos los validadores se podrá retirar v1 en otra versión.

Para ejecutar las pruebas del servidor desde `servidor_api/`:

```bash
docker compose exec web python manage.py test transporte panel
docker compose exec web python manage.py makemigrations --check --dry-run
```
