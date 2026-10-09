# Backlog Scrum: sistema de pago de transporte NFC

Documento de trabajo para un equipo de **5 o 6 personas**. Cada ID `Tn` es un
elemento del *product backlog* que puede convertirse en un issue; sus casillas
son tareas técnicas para completar dentro de un sprint. El equipo estima el
esfuerzo y elige cuánto cabe en cada sprint. Una prioridad no equivale a una
fecha prometida.

**Objetivo del producto:** emitir tarjetas de prueba, cobrar en un validador
sin conexión, sincronizar los viajes de forma confiable y permitir que un
operador investigue incidencias. Antes de plantear decenas de miles de usuarios
o dinero real, hay que medir y cerrar los riesgos de integridad.

## Punto de partida verificado en el repositorio

| Pieza | Estado actual | Implicación para el backlog |
|---|---|---|
| API Django, PostgreSQL y panel | Implementados; API v1 y panel operativos en el prototipo | Medir y mejorar, sin reescribir todo el servidor |
| API incremental `/api/sync/v2/` | **T1 implementada en `tarea/T1-sync-api`** (`9e14dde`); no consta integrada en `main` | Revisar PR, CI con PostgreSQL y migración antes de declararla cerrada |
| Validador Python | Usa la sincronización v1 | T7 migra el cliente |
| Validador ESP32 | LCD, microSD, WiFi y cobro implementados; todavía usa v1 | T2 migra el firmware |
| NFC del bus | ACR122U conectado a un ordenador con `puente_nfc.py` | El lector directo en ESP32 es T11, pendiente |
| Tarjetas | NTAG215 para pruebas | T6 y T13 tratan fallos y evolución de seguridad |
| Montaje | Protoboard, cables y alimentación USB en el prototipo | T20–T24 tratan la parte física y su verificación |

La rama contiene también documentación posterior a T1. **T1 no es una función
ya desplegada en todos los validadores**: la ESP32 y Python deben migrar por
separado. La capacidad para 50 000 usuarios es una hipótesis de ensayo, no una
medición obtenida.

## Cómo usar este backlog en Scrum

1. **Refinamiento:** el responsable del producto ordena los elementos; el
   equipo aclara criterios, dependencias y divide cualquier historia que no
   quepa en un sprint. En tareas de hardware, confirmar disponibilidad de
   componentes antes de comprometer una demostración física.
2. **Planificación:** escoger un objetivo de sprint y solo las historias que
   aporten a ese objetivo. Asignar una persona responsable por issue; otra
   revisa el PR, diseño o ensayo. Si una historia exige varias disciplinas,
   colaborar en el mismo resultado, no crear entregas inconexas.
3. **Tablero:** `Backlog → Lista → En curso → En revisión → En prueba física →
   Hecha`. Omitir «En prueba física» cuando no aplique. Un bloqueo por piezas,
   datos o acceso se registra en el issue con el siguiente paso concreto.
4. **Revisión de sprint:** demostrar un flujo ejecutable o una medición con
   datos de prueba; registrar el resultado observado y ajustar el backlog.
   La retrospectiva decide una mejora del modo de trabajo para el sprint
   siguiente.

**Definición de listo para planificar:** objetivo comprensible, criterios de
aceptación comprobables, dependencias identificadas, responsable y recurso de
prueba disponible o una alternativa acotada. El equipo asigna puntos de
historia o tamaño durante el refinamiento; esta tabla no inventa estimaciones.

**Definición de hecho:** criterios demostrados, revisión de otra persona,
pruebas pertinentes ejecutadas, documentación de uso y de recuperación
actualizada. Para hardware: foto o esquema de conexión, lista de componentes
con versión, mediciones y ensayo en placa real. Para software: tests relevantes,
compatibilidad/migración cuando corresponda y evidencia en el PR. No usar
datos, DNI, tokens ni claves reales en capturas o commits. «Hecha» significa
integrada en la rama objetivo acordada; «implementada en rama» no equivale a
«hecha».

**Ejemplo de issue:** `T2 — ESP32 consume sync v2`. Copiar su historia, tareas,
criterios, dependencias y evidencia; añadir responsable, tamaño acordado,
sprint y enlace al PR. Crear ramas desde `main` actualizado cuando la
dependencia esté integrada (`tarea/T2-esp32`, etc.). Si T1 aún no está en
`main`, basar temporalmente T2/T7 en su rama y declarar esa dependencia en el
PR. No fusionar ramas dependientes fuera de orden.

## Orden del backlog

`P0` prepara un piloto **con tarjetas y dinero de prueba**; `P1` mejora un
dispositivo y una operación repetible; `P2` depende de mediciones o decisiones
del producto. Los temas «hardware» incluyen firmware solo cuando está ligado
a una prueba física.

| ID | Prioridad | Área | Resultado esperado | Dependencia | Estado |
|---|---|---|---|---|---|
| T1 | P0 | API | Sync v2 incremental | — | Implementada en rama; falta cierre |
| T2 | P0 | Firmware | ESP32 usa v2 sin perder viajes | T1 | Pendiente |
| T3 | P0 | Backend/datos | Línea base de carga reproducible | — | Pendiente |
| T4 | P0 | Frontend | Panel operativo con muchos registros | T3 para medir | Pendiente |
| T5 | P0 | Operación | Alertas y restauración verificadas | — | Pendiente |
| T6 | P0 | Seguridad | Riesgos y fallos probados | T1/T2 para integración | Pendiente |
| T7 | P0 | Cliente Python | Validador Python usa v2 | T1 | Pendiente |
| T8 | P0 | Integración | Flujo completo y convivencia v1/v2 | T1, T2, T7 | Pendiente |
| T9 | P0 | Operación | Conciliación e incidentes trazables | T3, T4, T6 | Pendiente |
| T10 | P0 | Campo | Piloto controlado medido | T2, T5, T6, T8, T9, T24 | Pendiente |
| T20 | P0 | Hardware | Inventario, conexiones y banco de prueba | — | Pendiente |
| T24 | P0 | Hardware/datos | Ensayo de desgaste y extracción de microSD | T20, T6 | Pendiente |
| T11 | P1 | Hardware/NFC | Lector directo en ESP32 | T2, T8 | Pendiente |
| T12 | P1 | Compatibilidad | ACR122U y puente en otros SO | T8 | Pendiente |
| T13 | P1 | Hardware/seguridad | Decisión de tarjeta y claves | T6, T11 | Pendiente |
| T14 | P1 | Firmware/operación | Alta y actualización de equipos | T5, T6, T10 | Pendiente |
| T15 | P1 | Datos | Roles, auditoría y conservación | T4, T5, T6 | Pendiente |
| T21 | P1 | Hardware/energía | Alimentación y reinicios seguros | T20, T6 | Pendiente |
| T22 | P1 | Hardware/interacción | Pantalla, avisos y montaje usable | T20, T10 | Pendiente |
| T23 | P1 | Hardware/campo | Resistencia del montaje y mantenimiento | T11, T21, T22 | Pendiente |
| T16 | P2 | Producto | Portal para pasajeros | T15, T3 | Condicionada |
| T17 | P2 | Pagos | Integración con proveedor real | T9, T15 | Condicionada |
| T18 | P2 | Infraestructura | Escalar servidores según métricas | T3, T5, T10 | Condicionada |
| T19 | P2 | Arquitectura | Decidir Kafka/Flink/Kubernetes | T3, T5, T10, T18 | Condicionada |

## P0 — Historias para un piloto confiable

### T1 — Sincronización incremental de la API

**Historia.** Como validador, quiero recibir solo los cambios desde mi último
cursor para actualizar bloqueos, recargas y tarifas con respuestas acotadas.

**Tareas:**

- [x] Implementar `/api/sync/v2/`, paginación, cursor y migración
  `0006_sync_incremental` en la rama T1.
- [x] Documentar petición, respuesta, límites y reintentos en
  [contrato-sync-v2.md](contrato-sync-v2.md); conservar v1.
- [ ] Abrir/revisar el PR y pasar CI con PostgreSQL.
- [ ] Probar migración sobre copia de una base existente y comprobar que v1
  sigue respondiendo.

**Aceptación:** un lote reenviado no duplica viajes; páginas de hasta 100
cambios y subida de hasta 40 eventos; los dos clientes pueden convivir durante
la transición. **Evidencia:** PR, CI, prueba de migración y contrato. **Estado:**
implementada en `tarea/T1-sync-api`, pendiente de integración en `main`.

### T2 — ESP32 consume sync v2

**Historia.** Como pasajero, quiero que el bus siga aceptando mi tarjeta sin
demora aunque sincronice, pierda WiFi o se reinicie.

**Tareas:**

- [ ] Descargar páginas v2 y confirmar solo eventos aceptados; medir el tamaño
  máximo de respuesta y la memoria libre/mínima.
- [ ] Guardar cambios y cursor de forma recuperable en microSD; reconstruir
  una caché inicial desde cursor 0 sin activarla a mitad de la descarga.
- [ ] Acotar el trabajo de red dentro del ciclo de lectura; probar cola mayor
  de 40 viajes, WiFi lento, reinicio y tarjeta presentada durante sync.

**Aceptación:** compila con `pio run -e validador`; en placa real cobra sin red,
recupera el estado tras reinicio y no pierde ni duplica viajes en el ensayo.
Registrar p95 del toque, heap mínimo, placa y red. **Depende de:** T1.
**Archivos:** `pantalla_esp32/src/validador/`.

### T3 — Línea base de carga y PostgreSQL

**Historia.** Como equipo, queremos conocer el primer cuello de botella antes
de prometer capacidad para decenas de miles de cuentas.

**Tareas:**

- [ ] Crear generador reproducible de 50 000 cuentas, 60 000 tarjetas y datos
  ficticios. Es un escenario de ensayo, no una demanda observada.
- [ ] Simular 100, 500 y 800 validadores, sync con 0–40 eventos, ráfagas tras
  desconexión y consultas del panel; medir rutas por separado.
- [ ] Analizar consultas lentas con `EXPLAIN (ANALYZE, BUFFERS)`; comparar
  índices o agregados solo si el resultado lo justifica.

**Aceptación:** `docs/resultados-carga.md` permite repetir comandos y semilla;
incluye máquina, commit, tamaño de base, p50/p95/p99, errores, CPU/RAM y tamaño
de respuestas. Probar en entorno aislado. **Depende de:** puede iniciar ahora;
repetir tras integrar T1/T2.

### T4 — Panel para operadores con muchos registros

**Historia.** Como operador, quiero hallar una tarjeta, un bus atrasado o una
alerta en pocos pasos aunque existan miles de registros.

**Tareas:**

- [ ] Paginar y filtrar usuarios y validadores en el servidor; preservar
  búsqueda y paginación de cuentas, viajes y alertas.
- [ ] Medir agregados del inicio con T3 y mostrar fecha de actualización si
  se introduce caché; distinguir viajes recibidos de los aún no enviados.
- [ ] Probar con al menos dos compañeros las tres búsquedas; revisar teclado,
  foco, contraste, móvil y estados vacíos/error.

**Aceptación:** las tres tareas se completan sin recorrer listas completas;
consultas y tiempos quedan medidos, y pasan pruebas de filtros/paginación y
permisos. **Depende de:** T3 para la medición.

### T5 — Observabilidad, despliegue y recuperación

**Historia.** Como responsable de operación, quiero detectar buses atrasados
y restaurar el servidor para investigar incidentes sin perder el historial.

**Tareas:**

- [ ] Exponer o registrar 5xx/latencia, última sync por bus, cola pendiente
  cuando se reporte, espacio de PostgreSQL y resultado/antigüedad del backup.
- [ ] Definir umbrales, destinatario y pasos de respuesta para cada alerta.
- [ ] Restaurar un backup **de prueba** en entorno aislado; comprobar cuentas,
  viajes y panel. Documentar despliegue, reversión y rotación de secretos.

**Aceptación:** `docs/operacion.md` contiene evidencias, tiempo de recuperación
medido y un procedimiento que otra persona reproduce. **Depende de:** ninguna.

### T6 — Integridad, seguridad y fallos

**Historia.** Como equipo, queremos saber qué ocurre si se corta la energía,
se reenvía un evento o aparece una tarjeta restaurada antes de usar el sistema
en un piloto.

**Tareas:**

- [ ] Construir matriz de fallos de escritura de tarjeta, microSD, envío y
  confirmación; incluir recarga repetida, bus atrasado y dos buses sin red.
- [ ] Ejecutar pruebas automáticas donde sea posible y ensayos físicos con
  saldo, contador, cola y alerta esperados/observados.
- [ ] Documentar modelo de amenazas, límites de NTAG215, clave maestra en
  microSD, bloqueo de equipos y rotación de tokens.

**Aceptación:** `docs/modelo-amenazas.md` y registro de pruebas muestran fallos
que el sistema detecta y riesgos residuales; no se afirma detección en tiempo
real de clones usados en buses desconectados. **Depende de:** T1/T2 para la
prueba integrada.

### T7 — Validador Python consume sync v2

**Historia.** Como operador de un validador Python, quiero mantener el cobro
offline y sincronizar sin descargar las listas completas cada 30 segundos.

**Tareas:**

- [ ] Enviar lotes de hasta 40 eventos; persistir cursor y cambios en SQLite
  de forma recuperable e idempotente.
- [ ] Reconstruir caché inicial desde v2 sin mezclar estado parcial de v1.
- [ ] Probar varias páginas, respuesta recibida seguida de corte de red,
  duplicados y reinicio; actualizar guía de migración.

**Aceptación:** el CLI cobra sin red y cada viaje llega al servidor una sola
vez en las pruebas de reintento. **Depende de:** T1. **Archivos:**
`transporte/validador_local.py`, `api_cliente.py`, `validador.py`.

### T8 — Prueba integral y convivencia de versiones

**Historia.** Como equipo, queremos demostrar que emisión, cobro, recarga y
bloqueo funcionan juntos con tarjetas reales antes de un piloto.

**Tareas:**

- [ ] Ejecutar emisión → cobro offline → envío v2 → recarga remota → entrega →
  bloqueo → rechazo con el cliente Python y con ESP32 + ACR122U.
- [ ] Repetir la sincronización y verificar saldo y movimientos sin duplicados;
  probar también un cliente v1 durante el despliegue gradual.
- [ ] Registrar versiones, comandos y resultados esperados/observados.

**Aceptación:** `docs/prueba-extremo-a-extremo.md`, CI del servidor y ejecución
física revisada por dos compañeros. **Depende de:** T1, T2 y T7.

### T9 — Conciliación e investigación de incidentes

**Historia.** Como operador, quiero reconstruir un saldo discrepante a partir
de movimientos y eventos para resolver reclamaciones con trazabilidad.

**Tareas:**

- [ ] Definir reglas para retraso, duplicado y diferencia entre tarjeta,
  cuenta y viaje; crear casos de prueba.
- [ ] Mostrar cronología de cuenta/tarjeta/bus y acciones auditadas en el
  panel; definir corrección mediante movimiento registrado.

**Aceptación:** se explica cada caso de prueba y ninguna corrección edita el
saldo directamente sin dejar movimiento. **Depende de:** T3, T4 y T6.

### T10 — Piloto controlado

**Historia.** Como equipo, queremos observar el sistema en condiciones
parecidas a un bus y decidir con evidencia qué corregir antes de ampliarlo.

**Tareas:**

- [ ] Definir lugar, responsables, pocas tarjetas/validadores de prueba,
  horario, criterios de parada y versión instalada.
- [ ] Medir toques aceptados/rechazados, p95, atraso de sync y cola; ensayar
  pérdida de WiFi, reinicio y recuperación.
- [ ] Revisar con el equipo incidentes y saldos y decidir continuar, corregir
  o volver a la versión anterior.

**Aceptación:** informe de piloto con datos y decisión escrita. Detener si se
pierden eventos o no se puede reconstruir un saldo. **Depende de:** T2, T5,
T6, T8, T9 y T24.

### T20 — Banco de pruebas e inventario de hardware

**Historia.** Como integrante nuevo, quiero reproducir el montaje y saber qué
piezas se probaron para no atribuir una falla de cableado al software.

**Tareas:**

- [ ] Inventariar placa ESP32, LCD SSD1283A, microSD, ACR122U, tarjetas,
  cables, alimentación, versiones y responsables de custodia.
- [ ] Actualizar esquema de pines, tensión y buses SPI; identificar cables y
  fijar una secuencia de encendido/prueba de cada componente.
- [ ] Ejecutar firmware `prueba`, lectura NFC por puente y prueba de cobro;
  anotar fallos, fotos y números de serie no sensibles.

**Aceptación:** otro compañero arma el banco desde la guía y reproduce las
pruebas sin ayuda oral. **Depende de:** ninguna. **Referencia:**
[`pantalla_esp32/README.md`](../pantalla_esp32/README.md).

### T24 — MicroSD: desgaste, extracción y recuperación

**Historia.** Como operador de bus, quiero que una microSD dañada o retirada
se detecte y no convierta un cobro en un viaje perdido.

**Tareas:**

- [ ] Medir escrituras por viaje y sync; ensayar tarjeta llena, ausente,
  corrupta y retirada durante una operación, con tarjetas de prueba.
- [ ] Definir mensajes y bloqueo seguro del cobro cuando no pueda persistirse
  el viaje; probar reinicio y reparación/cambio de microSD.
- [ ] Documentar vida útil estimada bajo la carga medida, mantenimiento y
  tratamiento de secretos al sustituir la tarjeta.

**Aceptación:** matriz de ensayos físicos con saldo de tarjeta, cola y servidor
observados; procedimiento de recuperación probado. **Depende de:** T20 y T6.

## P1 — Hardware autónomo y operación repetible

### T11 — Lector NFC directo en la ESP32

**Historia.** Como operador del bus, quiero usar un validador sin ordenador ni
puente USB para reducir piezas y puntos de fallo.

**Tareas:**

- [ ] Seleccionar y conseguir un módulo lector compatible; comprobar tensión,
  interfaz, pines disponibles y acceso a las funciones NTAG215 necesarias.
- [ ] Implementar `LectorNFC` para el módulo; probar lectura, autenticación,
  escritura y relectura con tarjetas de prueba.
- [ ] Comparar p50/p95 y tasa de fallos contra ACR122U; ejecutar T8 sin PC.

**Aceptación:** demostración física autónoma, esquema y coste documentados;
paridad funcional medida antes de retirar el puente. **Depende de:** T2/T8.

### T12 — Compatibilidad del ACR122U en Windows y macOS

**Historia.** Como operador de atención, quiero instalar el lector y usar los
programas en los sistemas del equipo siguiendo instrucciones reproducibles.

**Tareas:**

- [ ] Probar PC/SC, emisión, lectura, recarga, bloqueo y puente en equipos
  disponibles; registrar SO, versión, driver y tarjeta.
- [ ] Corregir scripts o guías según los fallos observados; distinguir «no
  probado» de «funciona».

**Aceptación:** matriz de resultados con comandos y capturas sin datos reales;
otro compañero repite el flujo. **Depende de:** T8.

### T13 — Tarjeta y custodia de claves para una versión siguiente

**Historia.** Como responsable de seguridad, quiero decidir si NTAG215 y la
custodia actual de claves son suficientes para el siguiente alcance.

**Tareas:**

- [ ] Comparar tarjeta candidata (por ejemplo NTAG 424 DNA), lector, módulo
  seguro, coste y tiempo de toque; realizar prueba física de lectura/escritura.
- [ ] Diseñar alta, reemplazo, migración de tarjetas y rotación de claves con
  recuperación de saldo.

**Aceptación:** decisión documentada con prueba, límites y plan de convivencia;
ningún formato nuevo se adopta solo por una ficha comercial. **Depende de:**
T6 y T11.

### T14 — Gestión de flota y actualización de firmware

**Historia.** Como responsable de flota, quiero saber qué equipo y versión
están en cada bus y reemplazar una placa o token sin perder viajes.

**Tareas:**

- [ ] Crear inventario de bus, validador, versión, configuración y última sync.
- [ ] Ensayar alta, baja, revocación de token y reemplazo de una placa;
  comprobar conservación/transferencia de eventos pendientes.
- [ ] Diseñar actualización de firmware con firma y reversión antes de evaluar
  OTA; probar interrupción durante la actualización.

**Aceptación:** procedimiento reproducible de reemplazo y retorno a versión
anterior, con estado visible al operador. **Depende de:** T5, T6 y T10.

### T15 — Datos personales, roles y conservación

**Historia.** Como administrador, quiero limitar quién ve DNI e historial de
viajes y dejar rastro de acciones sensibles.

**Tareas:**

- [ ] Inventariar datos y accesos; definir roles de soporte, recarga y admin.
- [ ] Probar permisos en API/panel y auditoría de búsquedas y cambios sensibles.
- [ ] Proponer conservación, archivo/borrado y tratamiento de backups; pedir
  revisión legal antes de operar con personas reales.

**Aceptación:** matriz de permisos y pruebas por rol, política aprobada por el
responsable del proyecto. **Depende de:** T4, T5 y T6.

### T21 — Alimentación y reinicios seguros del validador

**Historia.** Como operador del bus, quiero que el equipo arranque y se
recupere tras una caída de energía sin corromper viajes ni tarjeta.

**Tareas:**

- [ ] Medir consumo y caídas de tensión al encender, leer NFC, escribir en
  microSD y usar WiFi; elegir fuente, protección y conectores según resultados.
- [ ] Ensayar apagado/reinicio repetido y cortes en puntos críticos de T6;
  observar arranque, mensaje al usuario y recuperación de cola.
- [ ] Documentar instalación eléctrica de prueba y límites del montaje.

**Aceptación:** tabla de medidas y ensayos físicos; ninguna prueba aceptada
termina con un cobro sin viaje recuperable. **Depende de:** T20 y T6.

### T22 — Pantalla, avisos y montaje para uso humano

**Historia.** Como pasajero y conductor, quiero distinguir pago aprobado,
rechazo y avería rápidamente, incluso con ruido o luz cambiante.

**Tareas:**

- [ ] Definir mensajes cortos y estados coherentes de LCD; prototipar buzzer
  o LEDs solo si aporta en pruebas con usuarios.
- [ ] Probar visibilidad, tiempo de respuesta, errores y reintento con
  compañeros; comprobar que el montaje no suelta cables al tocarlo.
- [ ] Registrar versión del diseño de carcasa/soporte y acceso a microSD,
  puerto de servicio y botón de recuperación.

**Aceptación:** usuarios identifican el resultado de un toque y el siguiente
paso en las pruebas; esquema y registro de hallazgos. **Depende de:** T20 y
observaciones de T10.

### T23 — Resistencia del montaje y mantenimiento en campo

**Historia.** Como técnico, quiero instalar y mantener el validador sin que
vibración, cables o cambio de piezas produzcan fallos silenciosos.

**Tareas:**

- [ ] Preparar montaje protegido, sujeción y conectores; definir acceso para
  mantenimiento y etiqueta del equipo.
- [ ] Ensayar sacudidas suaves, desconexión/reconexión de periféricos y ciclos
  de encendido en un entorno de prueba; registrar fallos observados.
- [ ] Escribir lista de inspección, limpieza, sustitución y baja de equipo.

**Aceptación:** prototipo montado, pruebas y plan de mantenimiento documentados.
No afirmar certificación vehicular sin los ensayos correspondientes.
**Depende de:** T11, T21 y T22.

## P2 — Producto y escala condicionados por evidencia

### T16 — Portal de pasajeros

**Historia.** Como pasajero, quiero consultar mis viajes y saldo sin exponer
mi cuenta a quien solo conoce mi DNI o el UID.

**Tareas:** definir autenticación y recuperación; diseñar consulta paginada y
probar interfaz; separar su carga del flujo de cobro.

**Aceptación:** prototipo probado y permisos/privacidad verificados antes de
publicación. **Depende de:** T15 y T3.

### T17 — Recargas con proveedor de pago real

**Historia.** Como responsable de recargas, quiero abonar saldo solo tras una
confirmación verificable y conciliar reversos o duplicados.

**Tareas:** probar en sandbox del proveedor; registrar referencias únicas;
separar «pago confirmado» de «entregado en tarjeta»; ensayar rechazo,
duplicación, reversa y cierre diario. Revisar obligaciones aplicables antes
de usar dinero real.

**Aceptación:** conciliación reproducible y pruebas de todos esos casos.
**Depende de:** T9 y T15; T16 solo si habrá autogestión.

### T18 — Capacidad de servidores y base de datos

**Historia.** Como equipo, queremos aumentar la capacidad cuando la medición
muestre un límite concreto.

**Tareas:** usar T3 para decidir índices, agregados, archivo/partición o más
instancias web; medir antes/después y ensayar migración y restauración con la
topología propuesta.

**Aceptación:** mejora medida y procedimiento operativo; si la línea base
cumple la meta, registrar que se aplaza. **Depende de:** T3, T5 y T10.

### T19 — Decisión sobre Kafka, Flink y Kubernetes

**Historia.** Como equipo, queremos elegir infraestructura solo para una
necesidad observada que podamos operar.

**Tareas:** escribir una decisión con volumen real, problema, alternativa más
sencilla, coste y responsables. Kafka se evalúa para varios consumidores de un
flujo durable; Flink para análisis continuo con estado; Kubernetes para operar
múltiples servicios/nodos. Ninguno sustituye el saldo transaccional de
PostgreSQL ni acelera por sí mismo la ESP32.

**Aceptación:** decisión «adoptar/aplazar» con prueba pequeña y métricas.
**Depende de:** T3, T5, T10 y T18.

## Sprints sugeridos para 5 o 6 personas

Los números son **secuencia propuesta**, no fechas. Cada sprint debe cerrar
menos historias si la capacidad real del equipo no alcanza. Evitar que dos
personas modifiquen a la vez `servicios.py`, `almacen.cpp` o una migración sin
coordinar el contrato.

| Sprint | Objetivo demostrable | Trabajo candidato | Revisión |
|---|---|---|---|
| 1 | Contrato v2 revisado y banco reproducible | Cerrar T1; iniciar T2, T3, T5, T6, T20 | API v1/v2, migración y montaje documentado |
| 2 | Clientes sincronizan y panel se puede navegar | Cerrar T2 y T7; avanzar T4 y T24 | Cobro offline, reintento y búsqueda con datos de carga |
| 3 | Flujo integral y recuperación probados | T8, T9, T21 y cierre de T5/T6/T24 | Demostración física, restauración y matriz de fallos |
| 4 | Aprender del piloto | T10; refinar T11–T15 y T21–T23 según resultados | Informe de campo y decisión de la siguiente iteración |

**Reparto inicial sugerido:** A firmware (T2), B backend/carga (T1 y T3), C
panel (T4), D operación (T5), E seguridad e integración (T6), F hardware
(T20/T24). Con cinco personas, F se comparte con E durante los ensayos. T7
puede tomarlo quien cierre T1. Los responsables reales y capacidad se acuerdan
en la planificación, no se deducen de esta tabla.

## Escenario común y comprobaciones

Ensayo inicial: 50 000 cuentas, 60 000 tarjetas, 500 validadores y 100 000
viajes/día. Si un 25 % ocurre en la hora de mayor carga, son aproximadamente
7 viajes/s; sincronizar 500 validadores cada 30 s genera aproximadamente 17
peticiones/s de media, además de ráfagas y reintentos. Ajustar con datos del
piloto. Registrar en cada medición commit, hardware, red, base, carga, p50/p95/
p99 y errores. El panel muestra viajes **recibidos**, no los que aún están en
la microSD del bus.

```bash
# Desde servidor_api/, con Docker funcionando
docker compose up -d --build
docker compose exec web python manage.py test
docker compose exec web python manage.py makemigrations --check --dry-run

# Desde pantalla_esp32/, con PlatformIO instalado
pio run -e validador
```

Para firmware y hardware, compilar no sustituye el ensayo en placa. Para
backend, pasar tests con SQLite no sustituye probar PostgreSQL. Más detalle
de T1–T6 en [plan-escalabilidad.md](plan-escalabilidad.md) y el contrato de
sync en [contrato-sync-v2.md](contrato-sync-v2.md).
