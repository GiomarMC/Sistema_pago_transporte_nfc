# M8-04 — Informe del intercambio ESP-NOW entre buses

Pruebas del 22 nov 2026 con dos validadores reales y el servidor de producción (MQTT/TLS). Este informe cubre una parte de #77: viajes sueltos de un bus sin servidor que llegan al servidor a través de otro, y una actualización de LISTAS (una recarga) que llega del servidor al bus sin servidor. **Faltan** la prueba de 30 viajes en un encuentro de 60 s y la del bloqueo de una tarjeta (ver al final).

## Escenario

| | Bus A | Bus B |
|---|---|---|
| Placa | validador 105 (LCD), MAC 8C:94:DF:97:D5:58 | validador 106 (OLED), MAC 04:B2:47:82:76:18 |
| Servidor | WiFi + MQTT/TLS al servidor de producción | sin servidor: `subida=vecinos` (se conecta al WiFi pero no abre MQTT, así que sus eventos solo salen por ESP-NOW) |
| Radio | hotspot del celular en el canal 1, ESP-NOW en el canal 1 | mismo hotspot y canal |
| Distancia | ~1 m, en la misma mesa | |

`subida=vecinos` (#228) sustituye a quitarle el WiFi al bus B: así los dos buses quedan en el mismo canal sin configurar nada más. Para el servidor, el bus B solo existe a través del bus A.

Firmware: la cadena de PR #227 → #232. Lo que cambió durante las pruebas y por qué:

- **Modem sleep** (#228): con el ahorro de energía del WiFi, cada placa recibía una baliza de cada ~8 y el vecino aparecía y se perdía cada 10 s. Con `WiFi.setSleep(false)`, el vecino queda estable.
- **Reintento tras aborto** (#228): al arrancar, el bus B entregaba sus eventos mientras su WiFi buscaba red fuera del canal; la transferencia se abortaba y el código de entonces no la reintentaba hasta 10 min después. Ahora la reintenta a los 5 s.
- **Hora real antes de subir** (#230): los viajes cobrados con la hora estimada (tras estar apagada) esperan a la hora real. El bus B se la pide al bus A con PEDIDO(HORA).
- **Canal del WiFi** (#231, #232): ESP-NOW impedía autenticar con algunos routers y con el AP en otro canal. Ahora ESP-NOW se pasa al canal del AP.

## Resultados: viajes del bus B al servidor por el bus A

Horas locales (UTC−5). "Recibido" es `EventoRecibido.recibido` en el servidor. En los cinco casos el ACUSE volvió al bus B por ESP-NOW y su contador de pendientes bajó a 0.

| Viaje | Situación | Desde | Hasta el servidor | Tiempo |
|---|---|---|---|---:|
| V106-3 | B arranca con el viaje pendiente; la 1.ª entrega se aborta (09:26:15) porque B aún buscaba WiFi | contacto estable 09:27:07 | 09:36:15 | 9 min 08 s |
| V106-4 | cobro con los dos buses conectados | cobro ~09:54 (hora del usuario) | 09:54:56 | < 1 min |
| V106-5 | B arranca con el viaje pendiente y A arranca a la vez (A tiene que conectar al WiFi y a MQTT) | B enchufado 10:16:04 | 10:16:50 | 46 s |
| V106-6 | cobro con los dos buses conectados | cobro 10:17:06 | 10:17:20 | 14 s |
| V106-7 | cobro tras apagar B (hora estimada) | cobro 10:46:47 | 10:46:52 | 5 s |

- El tiempo de V106-3 es el del reintento de 10 min del código de entonces. Con #228 ese caso se reintenta a los 5 s, pero ese reintento **no se volvió a medir en hardware**.
- Fechas de los viajes: V106-4 y V106-5 se subieron con la hora estimada atrasada (09:37:19 y 09:55:41 para cobros de ~09:54 y ~10:14). Con #230, V106-7 llegó con 10:46:47 para un cobro a las ~10:46:50.
- No hubo duplicados: cada viaje dejó un solo `EventoRecibido` y un solo `Movimiento`. Los reenvíos del ACUSE no repiten el cobro.

## Resultados: del servidor al bus B por el bus A (LISTAS)

Una recarga es un cambio de LISTAS, igual que un bloqueo, y recorre el mismo camino. Se creó una recarga de S/ 5.00 en el servidor y se siguió hasta el bus B.

| Paso | Hora |
|---|---|
| Recarga creada en el servidor (R3) | 10:06:57 |
| El bus A recibe las LISTAS en la respuesta a su HOLA (uno cada 5 min) | entre 10:07:30 y 10:08:30 |
| El bus A se las entrega al bus B por ESP-NOW (`LISTAS 1` en su diagnóstico de las 10:08:30) | entre 10:07:30 y 10:08:30 |
| El bus B cobra con la recarga aplicada (saldo 0.90 + 5.00 − 1.30 = 4.60) | 10:16 |

Del servidor al bus B: **entre 33 s y 1 min 33 s**. La mayor parte es la espera al siguiente HOLA del bus A. En el peor caso serían unos 5 min, porque las LISTAS solo llegan con el HOLA (no hay aviso del servidor).

## Otras mediciones

- **Detección:** con `setSleep(false)`, los buses se detectan en 1-3 s tras encenderse. El bus B, sin redes a la vista (#232), mantuvo al bus A como vecino más de 100 s seguidos.
- **Hora por vecino:** al arrancar, el bus B pidió la hora con PEDIDO(HORA) y recibió el ACUSE fresco del bus A (`pedidos de hora 1`, `recibidos 1`).
- **Conexión al WiFi con ESP-NOW** (105, router en el canal 6, `espnow_canal=1`): antes de #231/#232 no conectaba nunca; ahora 12 de 12 arranques conectan en 3-14 s.

## Lo que falta para cerrar #77

- [ ] **30 viajes en el bus B y un encuentro de ~60 s.** Hace falta saldo para 30 pasajes (S/ 39) o un método de banco para registrar viajes sin tarjeta. Cada paquete EVENTOS lleva hasta 30 eventos, así que 30 viajes caben en uno.
- [ ] **Bloqueo:** bloquear una tarjeta en el servidor y medir hasta que el bus B la rechaza. Hay que hacerlo en el servidor de producción y necesita autorización. Se espera el mismo tiempo que la recarga.
- [ ] Repetir con el bus B **sin WiFi** (`wifi=off`) y `espnow_canal` igual al canal del AP del bus A. Hoy el bus B tenía WiFi, solo que sin servidor.
- [ ] Tres buses, y distancias reales (eso es #75).
- [ ] El reintento de 5 s tras un aborto (#228), en hardware.
