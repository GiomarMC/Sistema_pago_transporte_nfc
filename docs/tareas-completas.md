# Backlog completo del sistema de pago NFC

Plan de trabajo compartido para un equipo de **cinco o seis personas**. Se basa
en el código del repositorio al terminar T1 (`9e14dde`). Es un backlog: una
tarea se marca como terminada solo cuando tiene código o documento revisado,
pruebas y evidencia. **No es una afirmación de que el prototipo ya soporte
50 000 usuarios ni de que esté listo para cobrar dinero real.**

## Estado y prioridad

| Estado | Significado |
|---|---|
| Implementada en rama | Código y documentación preparados; faltan revisión, CI con PostgreSQL e integración en `main` |
| Pendiente | No existe todavía la entrega descrita |
| Condicionada | Se decide solo después de medir o probar el requisito previo |

| Prioridad | Cuándo se atiende |
|---|---|
| P0 | Antes de un piloto con varios validadores y datos de prueba |
| P1 | Antes de una operación estable o de ampliar buses |
| P2 | Producto futuro o inversión condicionada por evidencia |

**T1 está implementada en `tarea/T1-sync-api` y subida a GitHub.** Su ruta v2
no está siendo usada aún por la ESP32 ni por el validador Python. Antes de dar
por cerrado T1 en `main`, abrir/revisar el PR, comprobar el CI con PostgreSQL,
probar la migración `0006_sync_incremental` sobre una copia de una base
existente y fusionarlo. La API v1 sigue activa para compatibilidad.

## Reparto inmediato

Una persona toma una tarea principal. Las letras son puestos, no nombres;
escriban el nombre del responsable al crear cada issue.

| Persona | Primera tarea | Puede comenzar | Entrega coordinada |
|---|---|---|---|
| A | [T2 ESP32 y sync v2](#t2--esp32-consume-la-sincronización-v2) | Ya: mediciones; integrar tras T1 | Contrato con quien hizo T1 |
| B | [T3 carga y PostgreSQL](#t3--pruebas-de-carga-y-base-de-datos) | Ya | Base para T4, T18 y T19 |
| C | [T4 panel de operaciones](#t4--panel-usable-con-muchos-registros) | Ya | Revisar consultas con B |
| D | [T5 observabilidad y recuperación](#t5--observabilidad-despliegue-y-recuperación) | Ya | Métricas del piloto |
| E | [T6 seguridad y fallos](#t6--integridad-seguridad-y-cortes-de-energía) | Ya | Casos para A y quien hizo T1 |
| F o siguiente ronda | [T7 cliente Python v2](#t7--validador-python-consume-la-sincronización-v2) | Tras contrato T1 | Mismo comportamiento que ESP32 |

Si son **cinco**, T7 la puede tomar quien terminó T1 en la siguiente ronda.
T8 y T10 son tareas de integración del equipo, después de las entregas
individuales. No poner a todos a editar `servicios.py`, `almacen.cpp` o la
misma migración a la vez.

## Reglas para cualquier tarea

1. Crear un issue con el ID y un responsable. Crear una rama desde `main`
   actualizado (`tarea/T2-esp32`, `tarea/T3-carga`, etc.). Un PR por tarea o
   por entrega pequeña que se pueda revisar.
2. En el PR: explicar el problema, la decisión, los archivos cambiados, las
   pruebas, mediciones y riesgo de despliegue. Si hay migración o cambio de
   formato de microSD, incluir actualización/recuperación de datos anteriores.
3. Usar cuentas, DNI, tarjetas y tokens **ficticios** en pruebas y capturas.
   No subir `.env`, `api.json`, claves maestras, volcados reales ni secretos.
4. En mediciones anotar commit, hardware, red, tamaño de la base, número de
   buses, carga y p50/p95/p99. Comparar antes y después; no presentar una sola
   prueba como capacidad garantizada.
5. La base PostgreSQL es la fuente central del saldo; el cobro del bus debe
   poder continuar sin red. El panel solo muestra viajes ya recibidos.

### Escenario de ensayo compartido

**Hipótesis para construir pruebas:** 50 000 cuentas, 60 000 tarjetas, 500
validadores, 100 000 viajes/día y un 25 % de los viajes en la hora más cargada
(≈7 viajes/s en esa hora). Con sincronización cada 30 s son ≈17 solicitudes/s
de media, además de reintentos y picos. Estos números son un punto de partida,
no demanda observada. Ajustarlos con datos del piloto.

## P0 — Camino hasta un piloto confiable

### T1 — API de sincronización incremental

**Estado:** implementada en rama; pendiente revisión e integración.

**Por qué:** `/api/sync/` envía listas completas de bloqueos y recargas a cada
bus. Ese cuerpo crece y se repite en todas las sincronizaciones.

**Entrega:** `/api/sync/v2/` con registro monotónico de cambios, páginas de
hasta 100 elementos, hasta 40 eventos de subida, cursor estable y migración de
estado existente. La ruta v1 se conserva. Ver
[contrato-sync-v2.md](contrato-sync-v2.md).

**Cierre:** PR revisado; CI con PostgreSQL verde; migración probada sobre una
copia de datos existentes; documentación y respuesta v1 comprobadas. No retirar
v1 mientras queden clientes antiguos.

**Archivos:** `servidor_api/transporte/{servicios,views,sync_cambios,models}.py`,
`migrations/0006_sync_incremental.py`, `tests/test_api.py`.

### T2 — ESP32 consume la sincronización v2

**Por qué:** el firmware actual usa v1, recibe el JSON entero en un `String` y
mantiene lista negra y recargas en estructuras de RAM. La red se atiende en el
`loop()` antes de esperar la tarjeta; una negociación TLS lenta puede atrasar
un cobro.

**Hacer:** implementar las páginas de [v2](contrato-sync-v2.md), confirmar solo
eventos aceptados, guardar cambios y cursor juntos en microSD, reconstruir una
copia inicial desde cursor 0, limitar RAM y evitar que la red bloquee el toque.
Medir heap libre/mínimo, tiempo de toque, respuesta, reconexión y cola de más
de 40 eventos. Probar tarjeta presentada durante sincronización y corte de
energía a mitad de una página.

**Cierre:** compila con `pio run -e validador`; funciona en placa real con/sin
WiFi; no pierde ni duplica viajes tras reinicio; la caché inicial se activa solo
al terminar todas sus páginas; métricas y placa usadas documentadas.

**Depende de:** T1. **Archivos:** `pantalla_esp32/src/validador/{sincronizador,
almacen,main}.cpp`, `pantalla_esp32/README.md`.

### T3 — Pruebas de carga y base de datos

**Por qué:** tener 50 000 filas no demuestra que resistan hora punta, reintentos
ni consultas del panel. Sin medición se puede optimizar el componente equivocado.

**Hacer:** generar datos ficticios reproducibles y probar 100, 500 y 800
validadores (el modelo actual usa IDs 1–899), respuestas v1/v2, lotes de 0–40
eventos, caídas y reconexión. Medir API, emisión, recarga y panel por separado.
Usar `EXPLAIN (ANALYZE, BUFFERS)` para consultas lentas, aplicar solo los
índices/agregados justificados y comparar antes/después.

**Cierre:** `docs/resultados-carga.md` con comandos, semilla, hardware,
latencias, errores, RAM/CPU, tamaño de respuesta y primer cuello de botella;
otro compañero puede reproducirlo en un entorno de ensayo. No cargar el
servidor público sin coordinación.

**Depende de:** puede iniciar ya; repetir tras T1/T2. **Archivos:**
`servidor_api/transporte/`, `servidor_api/panel/views.py`, nueva herramienta de
carga bajo `servidor_api/tools/`.

### T4 — Panel usable con muchos registros

**Por qué:** el panel ya busca cuentas y pagina viajes/alertas, pero las vistas
de usuarios y validadores cargan listas completas, y el resumen agrega viajes
al abrirse. Un operador necesita encontrar incidentes sin recorrer miles de
filas ni confundir viajes pendientes con recibidos.

**Hacer:** paginar y filtrar esas vistas, medir los agregados con T3, mostrar
la fecha de actualización cuando un indicador no sea inmediato. Probar tres
tareas reales con compañeros: buscar una tarjeta, identificar un bus sin sync y
resolver una alerta. Revisar móvil, teclado, estados vacíos y permisos.

**Cierre:** las tres tareas se completan sin navegación interminable; consultas
y tiempos están medidos; tests de permisos, filtros y paginación pasan.

**Depende de:** T3 para medir. **Archivos:** `servidor_api/panel/{views,tests}.py`,
`templates/panel/`, `static/panel/`.

### T5 — Observabilidad, despliegue y recuperación

**Por qué:** un bus puede seguir cobrando sin red mientras el servidor no ve
sus viajes. Hay despliegue automático y copias cifradas, pero falta demostrar
que el equipo detecta atrasos, fallos y puede restaurar datos.

**Hacer:** registrar latencia y 5xx de la API, última sync por bus, cola local
reportada de forma segura, espacio de PostgreSQL y antigüedad/resultado del
respaldo. Definir alertas con destinatario del equipo. Restaurar una copia de
**prueba en un entorno aislado** y verificar cuentas, viajes y acceso al panel.
Documentar despliegue, reversión y rotación de secretos.

**Cierre:** `docs/operacion.md` con tablero/reporte, umbrales, responsable,
ensayo de restauración con tiempo medido y pasos que otra persona pueda seguir.

**Depende de:** puede iniciar ya. **Archivos:** `servidor_api/docker-compose*.yml`,
`desplegar.sh`, `respaldar.sh`, `restaurar.sh`, `.github/workflows/`.

### T6 — Integridad, seguridad y cortes de energía

**Por qué:** la tarjeta y la microSD guardan estado para cobrar sin red; una
interrupción entre escribir, guardar y confirmar puede dejar copias diferentes.
Los clones usados en dos buses desconectados no se detectan en tiempo real.

**Hacer:** matriz de casos con corte de energía antes/después de cada paso,
reenvío, duplicado, evento fuera de orden, recarga repetida, bus desactualizado,
token perdido y tarjeta clonada. Registrar saldo en tarjeta y servidor, cola y
alerta esperada. Definir el procedimiento de bloqueo/rotación de tokens y
documentar límites de la clave maestra en microSD.

**Cierre:** `docs/modelo-amenazas.md`, pruebas automatizadas donde sea posible,
ensayos físicos reproducibles y riesgos residuales explícitos. No declarar
«antifraude total» por tener HMAC.

**Depende de:** integrar con T1/T2. **Archivos:** `servidor_api/transporte/`,
`pantalla_esp32/src/validador/`, `transporte/tarjeta/`.

### T7 — Validador Python consume la sincronización v2

**Por qué:** `transporte/validador_local.py` usa `/api/sync/`, envía todos los
eventos pendientes y reemplaza la caché con listas completas. Si permanece así,
el servidor seguirá pagando el coste de v1 por cada validador Python.

**Hacer:** lotes de 40 eventos, cursor persistido en SQLite, aplicación
idempotente de cambios, reconstrucción inicial sin mezclar una caché v1, y
reintento tras corte de red. Conservar la opción de funcionar sin conexión.

**Cierre:** pruebas con páginas, duplicados, corte tras recibir respuesta y
recuperación; el CLI sigue cobrando sin red y sube exactamente una vez cada
viaje. Actualizar el README y el modo de migración desde v1.

**Depende de:** T1. **Archivos:** `transporte/validador_local.py`,
`transporte/api_cliente.py`, `transporte/validador.py` y sus pruebas.

### T8 — Integración completa y compatibilidad

**Por qué:** pasar tests aislados de API y firmware no garantiza que una tarjeta
real, una recarga pendiente y un bloqueo atraviesen todo el sistema.

**Hacer:** guion automatizable para emisión → cobro sin red → reenvío v2 →
recarga remota → entrega → bloqueo → rechazo. Ejecutarlo con Python y con
ESP32+ACR122U; registrar diferencias. Comprobar que un cliente v1 todavía
funciona durante el despliegue gradual.

**Cierre:** `docs/prueba-extremo-a-extremo.md` con versiones, comandos,
capturas/logs sin secretos y resultados esperados/observados. CI del servidor
verde y una ejecución física firmada por dos compañeros.

**Depende de:** T1, T2 y T7.

### T9 — Conciliación de saldos y atención de incidentes

**Por qué:** con varios buses desconectados puede haber saldo negativo,
reenvíos tardíos o reclamaciones. El operador necesita reconstruir qué pasó
sin editar el saldo a mano.

**Hacer:** definir reglas de conciliación entre movimientos, cuenta y eventos;
detectar atrasos, duplicados y diferencias; agregar al panel una vista de caso
con cronología de tarjeta/cuenta/bus y acciones auditadas. Preparar un
procedimiento de corrección que produzca un movimiento trazable.

**Cierre:** casos de prueba de discrepancia y guía de atención; ninguna
corrección cambia `Cuenta.saldo` directamente sin registro.

**Depende de:** T3, T4 y T6. **Archivos:** `servidor_api/transporte/servicios.py`,
`panel/`.

### T10 — Piloto controlado con métricas

**Por qué:** el sistema necesita evidencia de campo: cableado, red, microSD,
latencia percibida, soporte del operador y recuperación de fallos.

**Hacer:** seleccionar pocos validadores y tarjetas **de prueba**, fijar horas
y responsable, registrar toques aceptados/rechazados, p95 del toque, atraso de
sync, eventos pendientes y fallos. Ensayar caída de WiFi y reinicio. Detener el
piloto si se pierden eventos o no se puede reconstruir un saldo.

**Cierre:** informe de piloto con datos observados, incidentes y decisión
escrita de continuar, corregir o volver a la versión anterior.

**Depende de:** T2, T5, T6, T8 y T9.

## P1 — Ampliación y operación estable

### T11 — Lector NFC conectado directamente a la ESP32

**Por qué:** el modo B actual deja la lógica en la ESP32, pero todavía requiere
un ordenador que presta el ACR122U por USB. Para cada bus esto añade equipo,
cables y un proceso que puede fallar.

**Hacer:** prototipo con PN532 u otro lector compatible; implementar la misma
interfaz `LectorNFC`, comprobar lectura/escritura y autenticación de NTAG215,
comparar tiempos y fallos con ACR122U. Diseñar caja, energía y recuperación
del dispositivo antes de pensar en instalación permanente.

**Cierre:** demostración sin ordenador y medidas repetidas con tarjetas reales;
documentación de conexión, coste y limitaciones. No sustituir el puente hasta
tener paridad funcional y pruebas.

**Depende de:** T2/T8. **Archivos:** `pantalla_esp32/lib/LectorNFC/`,
`pantalla_esp32/src/validador/`.

### T12 — Compatibilidad de lector y puente en Windows/macOS

**Por qué:** el lector Python y el puente se probaron principalmente en Fedora;
el equipo usa también Mac y podría necesitar equipos Windows.

**Hacer:** matriz por SO con instalación de PC/SC, selección de puerto,
emisión, lectura, cobro, recarga, bloqueo y puente ESP32. Registrar errores de
drivers y pasos reproducibles; añadir pruebas automatizables sin hardware.

**Cierre:** tabla de «probado/no probado/falló» con versión de SO, ACR122U,
tarjeta y resultado; guías de instalación corregidas.

**Depende de:** T8. **Archivos:** `README.md`, `pantalla_esp32/README.md`,
`transporte/`.

### T13 — Tarjeta y claves para una versión más segura

**Por qué:** NTAG215 con contraseña y HMAC es útil para prototipo, pero la
clave maestra y el token del bus están hoy en archivos/microSD. Una tarjeta
futura y un elemento seguro cambian formato, lector, firmware y costos.

**Hacer:** comparar tarjetas candidatas (incluida NTAG 424 DNA), lector y
módulo seguro; prototipar emisión, validación offline, recarga y reemplazo;
planear migración de tarjetas ya emitidas y rotación de claves. Medir tiempo
de toque y coste por bus/tarjeta.

**Cierre:** prueba física y decisión documentada; no desplegar un formato nuevo
sin plan de convivencia y recuperación de saldo.

**Depende de:** T6 y T11. **Archivos:** `transporte/tarjeta/`, firmware y
`docs/modelo-amenazas.md`.

### T14 — Gestión de flota y actualización de validadores

**Por qué:** configurar a mano WiFi, URL, token y firmware funciona con pocas
placas; con muchos buses es difícil saber qué versión corre cada uno y retirar
una credencial perdida.

**Hacer:** inventario de equipo/número/versión/última sync, proceso de alta y
baja, configuración por dispositivo y actualización de firmware comprobable.
Evaluar actualizaciones remotas solo después de resolver firma, reversión y
fallo de energía; una actualización fallida no debe dejar al bus sin cobrar.

**Cierre:** procedimiento reproducible para reemplazar una placa, revocar un
token y volver a firmware anterior; estado visible para operadores.

**Depende de:** T5, T6 y T10.

### T15 — Datos personales, permisos y conservación

**Por qué:** el sistema guarda DNI, nombres y viajes. Al crecer, las búsquedas,
exportaciones, cuentas de operador y copias requieren reglas claras de acceso.

**Hacer:** inventariar datos y accesos; separar roles de soporte, recarga y
administración; auditar consultas y cambios sensibles; definir borrado o
archivo de datos y respaldo. Pedir revisión legal antes de operar con personas
reales. Evitar mostrar más datos de los necesarios en el panel.

**Cierre:** matriz de permisos, política de conservación aprobada por el
responsable del proyecto y pruebas de que cada rol solo ve/hace lo autorizado.

**Depende de:** T4, T5 y T6.

## P2 — Producto y escala condicionada

### T16 — Portal para pasajeros

**Por qué:** hoy el panel es para operadores. Si se ofrece consulta de saldo y
viajes a pasajeros, esa carga debe ir separada del flujo de cobro y proteger el
historial personal.

**Hacer:** definir casos de uso, autenticación, recuperación de cuenta,
consulta paginada y actualización de datos; diseñar y probar la interfaz con
usuarios. No exponer una cuenta solo con saber su DNI o UID.

**Cierre:** prototipo probado, permisos y pruebas de privacidad antes de
publicarlo. **Depende de:** T15 y resultados de T3.

### T17 — Recargas con proveedor de pago real

**Por qué:** el repositorio simula recargas remotas; registrar una recarga no
equivale a confirmar un pago externo. Un cobro real requiere conciliación con
el proveedor y manejo de mensajes repetidos o reversados.

**Hacer:** elegir proveedor y entorno de prueba, verificar notificaciones,
registrar referencias únicas, manejar reintentos/reversas y conciliar montos.
Separar «pago confirmado» de «saldo entregado a tarjeta». Revisar requisitos
contractuales, financieros y de protección de datos antes de usar dinero real.

**Cierre:** pruebas de pago aprobado/rechazado/duplicado/reversado y cierre
diario conciliado; autorización del equipo antes de producción.

**Depende de:** T9, T15 y T16 si habrá autogestión.

### T18 — Más capacidad de base de datos y servidores

**Por qué:** un solo PostgreSQL y una instancia web pueden llegar a su límite,
pero añadir servicios sin medir aumenta el trabajo operativo y puede ocultar
consultas lentas.

**Hacer:** usar T3 para decidir índices, agregados del panel, archivo de
movimientos, partición por fecha o más instancias web detrás del proxy. Ensayar
fallo y restauración; verificar que sesiones, migraciones y tareas programadas
funcionan con varias instancias.

**Cierre:** mejora medida frente a línea base y procedimiento de operación.
Si la línea base cumple la meta, registrar que no hace falta añadir nodos.

**Depende de:** T3, T5, T10. **Estado:** condicionada.

### T19 — Decisión sobre Kafka, Flink y Kubernetes

**Por qué:** estas herramientas resuelven problemas distintos: distribución de
eventos, análisis continuo y operación de contenedores. Ninguna hace que la
ESP32 procese más rápido ni sustituye el saldo transaccional en PostgreSQL.

**Hacer:** escribir una decisión de arquitectura con volumen observado,
requisito que no cubre la solución actual, alternativa simple, coste de
despliegue, responsables y criterio para retirarla. Kafka solo si varios
consumidores necesitan un flujo durable; Flink si hay análisis continuo con
estado que no cabe en consultas/trabajos sencillos; Kubernetes si se necesita
operar múltiples servicios/nodos y el equipo puede mantenerlo.

**Cierre:** decisión «incorporar / aplazar» con prueba pequeña y métricas. No
meter estas tecnologías en el camino crítico del toque sin necesidad probada.

**Depende de:** T3, T5, T10 y T18. **Estado:** condicionada.

## Orden de integración

1. **Ahora:** revisar/fusionar T1; avanzar T2–T7 en ramas separadas. T3 produce
   línea base de v1 y v2; T5 prepara observación y recuperación.
2. **Después:** integrar T2/T7 con T1, ejecutar T8 y resolver hallazgos de T6.
   T4/T9 deben permitir investigar los viajes que lleguen tarde.
3. **Piloto:** ejecutar T10 solo cuando los casos de integridad y restauración
   tengan resultado. T11–T15 siguen los problemas hallados en el piloto.
4. **Escala mayor o producto público:** decidir T16–T19 con demanda y medición;
   no son requisitos para mostrar el prototipo en clase.

## Comandos mínimos de comprobación

```bash
# Desde servidor_api/, con Docker funcionando
docker compose up -d --build
docker compose exec web python manage.py test
docker compose exec web python manage.py makemigrations --check --dry-run

# Desde pantalla_esp32/, con PlatformIO instalado
pio run -e validador
```

Para T3, T5 y T10 guardar también los comandos exactos, datos de entrada y
resultados en el PR. Un test que pasa con SQLite no sustituye la prueba de
PostgreSQL que usa el servidor.

## Referencias para ejecutar las tareas

- [Contrato v2](contrato-sync-v2.md) y [plan detallado T1–T6](plan-escalabilidad.md)
  dentro del repositorio.
- [Medición de memoria en ESP32](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/system/heap_debug.html).
- [Planes de consultas con PostgreSQL](https://www.postgresql.org/docs/17/using-explain.html).
- [Lista de comprobación de despliegue de Django](https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/).
