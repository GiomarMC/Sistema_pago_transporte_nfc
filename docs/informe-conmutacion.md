# M15 — Informe de conmutación

El plan vigente (`docs/plan-modulos-y-tareas.pdf` de `origin/main`, posterior al PR #162) define WiFi, BLE y ESP-NOW. El validador no usa 4G.

## Prueba de componentes

`sh pantalla_esp32/test_host/run.sh` compila y prueba `GestorEnlaces` con `EnlaceFalso`, `CanalFalso` y vecinos falsos. Comprueba la prioridad WiFi > BLE, la histéresis de 10 s al recuperar WiFi, el cambio entre intercambios, la solicitud explícita de la cola BLE, la pausa de ESP-NOW si el AP usa otro canal y que el gestor no consume los eventos pendientes sin enlace. Prueba también `CanalMqtt` con `EnlaceFalso`: puerto TLS 8883, usuario y clave, will, suscripciones, publicación, cola recibida y reintento.

Estas pruebas no demuestran el comportamiento del equipo físico ni la entrega de eventos al servidor.

| Escenario físico de M15-04 | Tiempo de recuperación | Eventos perdidos o duplicados | Estado |
|---|---:|---:|---|
| Apagar el router sin canal alternativo; conservar los eventos en cola | Pendiente | Pendiente | No ejecutado |
| Apagar el router con BLE del conductor disponible | Pendiente | Pendiente | No ejecutado |
| Recuperar WiFi y volver a priorizarlo | Pendiente | Pendiente | No ejecutado |

Para completar la tabla, registrar la caída y el primer HOLA válido, comparar los identificadores de eventos antes y después en el servidor y comprobar cero pérdidas y cero duplicados. Se necesitan fotos de la LCD mostrando W, BT y ausencia de enlace. No hay mediciones ni fotos de hardware en esta rama.

## Integración pendiente

La rama conserva `Sincronizador` v1 (HTTP/JSON). No están disponibles `SincronizadorV2` con acuses C1, `EnlaceWifi`, `CanalBle` ni `VecinosEspNow` de M4, M6 y M7. `GestorEnlaces` y `CanalMqtt` quedan compilados en las pruebas nativas, pero no se activan en `main.cpp`: la cola de viajes solo puede cambiar de transporte cuando el sincronizador v2 garantice sus acuses e idempotencia. `sync=v1` es el modo operativo; si se configura `sync=v2`, el equipo avisa por serie y sigue en v1. La barra LCD refleja el WiFi y los pendientes del v1. Los iconos BLE y el HOLA C1 requieren integrar los canales faltantes. La clave MQTT deberá leerse de NVS; `CanalMqtt` la recibe del llamador en memoria.

El requisito `loop() < 60 ms` se debe medir en la placa con los canales activos. Para instrumentarlo, compilar `validador` con `build_flags = -DM15_MEDIR_LOOP` y observar `M15 loop_max_us=` en el monitor serie; el valor debe ser menor que `60000`. El v1 actual tiene esperas de tarjeta de 300 ms y operaciones HTTP bloqueantes, por lo que esa condición aún no está demostrada. El test nativo no la sustituye.
