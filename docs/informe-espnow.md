# M8-04 — Informe del intercambio ESP-NOW entre buses

Pruebas del 22 y 23 nov 2026 con dos validadores reales y el servidor de producción (MQTT/TLS), para #77.

**Resumen de los criterios de #77** (23 nov, bus B **sin WiFi**):

| Criterio | Resultado |
|---|---|
| Del encuentro entre buses a los viajes en el servidor | **30 viajes en 25 s** desde que el bus A se enciende (unos 15 s son su arranque y su conexión al WiFi); 6 s desde que los dos están en el mismo canal |
| Del bloqueo en el servidor a su aplicación en el bus B | **entre 50 s y 1 min 50 s** hasta que el bus A le entrega las LISTAS al bus B; el bus B rechazó la tarjeta en la primera pasada después del bloqueo (2 min 45 s) |

El detalle del 23 nov está en [Pruebas del 23 nov](#pruebas-del-23-nov-bus-b-sin-wifi-30-viajes-y-bloqueo). Lo del 22 nov (bus B con WiFi pero sin servidor, viajes sueltos y una recarga) sigue debajo.

## Escenario del 22 nov

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

## 22 nov: viajes del bus B al servidor por el bus A

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

## 22 nov: del servidor al bus B por el bus A (LISTAS)

Una recarga es un cambio de LISTAS, igual que un bloqueo, y recorre el mismo camino. Se creó una recarga de S/ 5.00 en el servidor y se siguió hasta el bus B.

| Paso | Hora |
|---|---|
| Recarga creada en el servidor (R3) | 10:06:57 |
| El bus A recibe las LISTAS en la respuesta a su HOLA (uno cada 5 min) | entre 10:07:30 y 10:08:30 |
| El bus A se las entrega al bus B por ESP-NOW (`LISTAS 1` en su diagnóstico de las 10:08:30) | entre 10:07:30 y 10:08:30 |
| El bus B cobra con la recarga aplicada (saldo 0.90 + 5.00 − 1.30 = 4.60) | 10:16 |

Del servidor al bus B: **entre 33 s y 1 min 33 s**. La mayor parte es la espera al siguiente HOLA del bus A. En el peor caso serían unos 5 min, porque las LISTAS solo llegan con el HOLA (no hay aviso del servidor).

## Pruebas del 23 nov: bus B sin WiFi, 30 viajes y bloqueo

### Escenario

| | Bus A | Bus B |
|---|---|---|
| Placa | validador 105 (LCD) | validador 106 (OLED) |
| Servidor | WiFi (red MUNOZ, **canal 8**) + MQTT/TLS al servidor de producción | **sin WiFi** (`wifi=off`), `espnow_canal=6` |
| Distancia | ~1 m, en la misma mesa | |

Firmware: `develop` con #236. El bus B está en otro canal que el bus A, y lo encuentra solo: recorre los canales y, al oír una baliza que dice venir del canal 8, salta a él.

**Método de banco para los 30 viajes:** el validador no vuelve a cobrar la misma tarjeta hasta 60 s después (doble toque), y hay una sola tarjeta con saldo. Para la prueba, el bus B se compiló con `-DMARGEN_DOBLE_TOQUE_PRUEBA=3` (margen de 3 s; el arranque lo avisa con `PRUEBA DE BANCO`) y después se le volvió a cargar el firmware normal. Los viajes son cobros reales con la tarjeta 04653E22CA2A81, recargada con S/ 40.00 en punto antes de empezar.

El bus A se "apagó" manteniéndolo en reinicio desde el PC (EN a masa por RTS) y se "encendió" soltándolo: así el encuentro empieza en un instante conocido.

### 30 viajes del bus B al servidor por el bus A

1. Con el bus A apagado, se cobraron los viajes en el bus B entre las 19:57:21 y las 19:59:56. Antes se había desenchufado el bus B (su PN532 no respondía, #200), así que arrancó sin hora real: los viajes quedaron con la hora estimada.
2. El encuentro:

| Hora | Qué pasa |
|---|---|
| 20:00:45 | se suelta el bus A (empieza el encuentro) |
| 20:00:51 | el bus B, que buscaba por los canales, oye al bus A en el canal 1 (A aún sin WiFi) y salta allí; ve que B tiene `pendientes 30`, pero sin servidor no los acepta |
| 20:00:55 | el bus A se conecta a MUNOZ y ESP-NOW pasa al canal 8; a las 20:01:00 abre MQTT |
| 20:01:02 | el bus B oye desde el canal 1 una baliza del canal 8 y salta; a las 20:01:04 los dos se ven en el canal 8 |
| 20:01:05 | el bus B recibe la hora del bus A (PEDIDO(HORA)) y corrige los 30 viajes (+506 s) |
| 20:01:10-11 | los 30 viajes están en el servidor (`EventoRecibido` del 106, ids 9-38) |
| antes de 20:01:18 | el ACUSE vuelve al bus B: `pendientes 0` |

- **Del encuentro al servidor: 25 s.** Del momento en que los dos están en el mismo canal: 6 s. Los 30 viajes fueron en un solo paquete EVENTOS (`EVENTOS entregados 1`, sin abortos).
- En el servidor: 30 `Movimiento` (V106-9 a V106-38), sin duplicados, con las fechas de cobro bien corregidas (19:57:21 a 19:59:56, iguales a las del registro del bus B).
- **Un cobro se perdió por el margen de banco.** La tarjeta registra 31 cobros (S/ 40.70 → S/ 0.40) y el servidor 30 (cuenta en S/ 1.70). Falta la operación 33: a las 19:59:03 la tarjeta se retiró a mitad de la verificación (`Lectura interrumpida`) con el cobro ya grabado. Con el margen normal, la siguiente pasada (19:59:06) habría salido "Ya pagado" y registrado ese viaje. Con 3 s se tomó como un viaje nuevo. Es un efecto del método de banco, pero deja ver un hueco real: si el pasajero no vuelve a acercar la tarjeta en 60 s, ese cobro no llega nunca al servidor.

### Bloqueo de una tarjeta

Se emitió una tarjeta solo para esto (n.º 4, UID 041B5712CE2A81, cuenta 4 "Prueba bloqueo M8-04", sin saldo), porque el bloqueo no se puede deshacer: el servidor no tiene desbloqueo y el validador lo graba en la tarjeta.

| Hora | Qué pasa |
|---|---|
| 20:14:35-45 | referencia: el bus B la rechaza por **saldo insuficiente** (no la tiene bloqueada) |
| 20:14:59 | se bloquea en el servidor (`bloquear_tarjeta.py --tarjeta 4`) |
| entre 20:15:49 y 20:16:49 | el bus A le entrega las LISTAS al bus B (`LISTAS 0` → `LISTAS 1` en su diagnóstico de cada minuto; el bus B, `pedidos de listas 1`) |
| 20:17:44 | primera pasada después del bloqueo: el bus B la rechaza, **"Tarjeta bloqueada"**, y graba el bloqueo en la tarjeta |

- **Del bloqueo al bus B: entre 50 s y 1 min 50 s.** No se puede afinar más: el bus A anota sus contadores una vez por minuto y no se pasó la tarjeta entre las 20:14:45 y las 20:17:44. Como en la recarga del 22 nov, casi todo es la espera al siguiente HOLA del bus A, que trae las LISTAS (cada 5 min como mucho).

## Otras mediciones

- **Detección:** con `setSleep(false)`, los buses se detectan en 1-3 s tras encenderse. El bus B, sin redes a la vista (#232), mantuvo al bus A como vecino más de 100 s seguidos.
- **Hora por vecino:** al arrancar, el bus B pidió la hora con PEDIDO(HORA) y recibió el ACUSE fresco del bus A (`pedidos de hora 1`, `recibidos 1`).
- **Conexión al WiFi con ESP-NOW** (105, router en el canal 6, `espnow_canal=1`): antes de #231/#232 no conectaba nunca; ahora 12 de 12 arranques conectan en 3-14 s.

## Lo que falta

Los dos criterios de #77 están medidos. Queda fuera de este informe:

- [ ] Tres buses, y distancias reales (eso es #75).
- [ ] El reintento de 5 s tras un aborto (#228), en hardware.
- [ ] El cobro que se pierde si la tarjeta se retira a mitad de la verificación y no se vuelve a acercar (ver los 30 viajes): no es de ESP-NOW, pero conviene abrir un issue.
