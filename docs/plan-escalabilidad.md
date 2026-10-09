# Plan de trabajo: validadores y servidor a mayor escala

Este documento detalla T1–T6 para un equipo de **cinco o seis personas**. Cada
tarea puede convertirse en un issue y un pull request (PR). Como T1 ya está
implementada en una rama, cinco personas pueden tomar T2–T6 en paralelo.
**No se ha demostrado todavía que el sistema soporte decenas de miles de pasajeros**:
primero hay que corregir el intercambio de datos y medirlo.

El [backlog completo](tareas-completas.md) incluye las tareas posteriores.
T1 ya está implementada en la rama `tarea/T1-sync-api`; faltan revisión,
pruebas de CI con PostgreSQL e integración en `main`.

## Escenario común para las pruebas

- **Hipótesis, no tráfico medido:** 50 000 cuentas, 60 000 tarjetas, 500
  validadores y 100 000 viajes al día. Si el 25 % de los viajes ocurre en una
  hora, son unos 7 viajes por segundo en esa hora. Con una sincronización cada
  30 segundos, 500 validadores generan unas 17 solicitudes por segundo de media,
  más ráfagas y reintentos tras caídas de red.
- El cobro debe seguir funcionando sin red. El servidor confirma eventos sin
  duplicarlos cuando llegan otra vez. El panel muestra los datos **recibidos**,
  no inventa los viajes que un bus aún guarda en su microSD.
- Registrar los resultados con fecha, commit, CPU/RAM, versión de PostgreSQL,
  número de validadores, tamaño de la base y distribución de viajes. No presentar
  una cifra de capacidad sin estas condiciones.
- Metas iniciales para discutir con el equipo: tiempo de toque p95 menor de 1 s
  en el hardware de prueba; cero eventos perdidos o duplicados tras reintentos;
  respuestas de sincronización de tamaño acotado; y atraso de sincronización
  visible por validador. Ajustar las metas después de la primera medición.

## Cómo colaborar sin pisarse

1. Una persona toma cada tarea y anota su nombre en la tabla. Abrir una rama
   `tarea/T1-sync-api`, `tarea/T2-esp32`, etc., desde `main` actualizado.
2. **T1 y T2 acuerdan por escrito el contrato de `/api/sync/` antes de cambiar
   el formato.** T3 prepara la carga desde el primer día y repite las mediciones
   cuando se integren T1 y T2.
3. Cada PR indica: problema, cambio, migración si existe, pruebas realizadas,
   resultado medido y cómo volver a la versión anterior. Evitar archivos
   `.env`, tokens, claves, datos personales y volcados de producción en Git.
4. No modificar a la vez el mismo archivo en dos ramas sin acordarlo. Si T3
   descubre que necesita índices o migraciones, coordina esos cambios con T1.
5. Integrar primero el contrato y las pruebas de T1, después T2. T4 y T5
   pueden avanzar en paralelo. Revisar T6 antes de declarar listo un piloto.

| Tarea | Responsable | Trabajo principal | Dependencia |
|---|---|---|---|
| [T1](#t1--sincronización-incremental-en-la-api) | Implementada en rama | API y contrato de sincronización | Revisar PR e integrar |
| [T2](#t2--firmware-esp32-y-almacenamiento-local) | Por asignar | ESP32 y microSD | Contrato de T1 para integración |
| [T3](#t3--pruebas-de-carga-y-base-de-datos) | Por asignar | Generador, mediciones y consultas | Repetir tras integrar T1 |
| [T4](#t4--panel-de-operaciones-con-muchos-registros) | Por asignar | Búsqueda y vistas operativas | Datos de T3 para medir |
| [T5](#t5--despliegue-observabilidad-y-recuperación) | Por asignar | Operación y copias | Puede empezar ya |
| [T6](#t6--integridad-seguridad-y-pruebas-de-fallo) | Por asignar | Ensayos de fallo y fraude | Contrato de T1 y firmware de T2 |

## T1 — Sincronización incremental en la API

**Problema actual.** `transporte/servicios.py::sincronizar` devuelve a *cada*
validador toda la lista de tarjetas bloqueadas o anuladas y todas las recargas
pendientes. El coste y el cuerpo JSON crecen con el padrón; la ESP32 lo recibe
entero. Los eventos que sube el bus ya tienen referencias idempotentes.

**Hacer:**

1. Escribir en `docs/contrato-sync-v2.md` la petición y respuesta propuestas,
   con ejemplo de primera sincronización, página siguiente, cambios normales,
   eliminación de una recarga entregada, bloqueo/anulación y reintento. Acordar
   ese contrato con T2. Mantener la versión anterior mientras se actualizan
   los clientes o definir una migración explícita.
2. Diseñar un cursor o versión **persistente y monotónica** para los cambios.
   Incluir eliminaciones o cambios de estado (tombstones); un simple filtro por
   fecha no basta si algo deja de estar pendiente. Separar la confirmación de
   eventos subidos del avance del cursor de datos descargados.
3. Limitar eventos de entrada y elementos de salida por petición. Validar
   parámetros y devolver `hay_mas`/`siguiente_cursor` sin omitir cambios cuando
   se escriben datos mientras el validador pagina. Mantener token por validador.
4. Añadir pruebas con múltiples páginas, reconexión, duplicados, cursores
   inválidos, dos validadores y cambios concurrentes. Documentar despliegue y
   compatibilidad de clientes en el README de la API.

**Archivos de partida:** `servidor_api/transporte/servicios.py`, `views.py`,
`serializers.py`, `models.py`, `tests/test_api.py` y migraciones.

**Terminado cuando:** el mismo lote reenviado no duplica viajes; un validador
puede descargar todos los cambios con respuestas acotadas; recibe altas y bajas
de bloqueos y recargas; pasan las pruebas de la API y la migración se aplica en
una base vacía y en una base existente de prueba.

## T2 — Firmware ESP32 y almacenamiento local

**Problema actual.** `sincronizador.cpp` lee la respuesta HTTP completa en un
`String` y la convierte en JSON. `Almacen` mantiene lista negra y recargas en
RAM (`std::map`/`std::vector`). Además, la sincronización se llama desde el
`loop()` antes de esperar la tarjeta, por lo que una conexión lenta puede
demorar un toque.

**Hacer:**

1. Medir RAM libre, tamaño de respuesta y tiempo de toque con listas pequeñas
   y grandes; guardar los resultados y el modelo exacto de placa en el PR.
2. Implementar el contrato de T1: páginas limitadas, cursor persistido en
   microSD **solo después** de guardar los cambios, y recuperación tras corte
   de energía a mitad de una página. Evitar duplicar toda la respuesta en RAM.
3. Elegir una representación en microSD o compacta para buscar bloqueos y
   recargas sin cargar un padrón creciente en memoria. Medir su tiempo de
   consulta durante un cobro. Conservar la operación sin red.
4. Evitar que WiFi/TLS bloquee el ciclo de cobro más allá de la meta acordada.
   Probar conexión lenta, pérdida de WiFi, reconexión, tarjeta durante el envío
   y cola de más de 40 eventos (límite actual por petición).

**Archivos de partida:** `pantalla_esp32/src/validador/sincronizador.cpp`,
`almacen.cpp`, `main.cpp`, `pantalla_esp32/README.md` y `transporte/puente_nfc.py`.

**Terminado cuando:** compila con `pio run -e validador`; en placa real, los
viajes se cobran sin red y se suben una sola vez al volver; una respuesta de
varias páginas no agota la memoria; y el tiempo de toque está medido mientras
hay sincronización y durante un fallo de red. Anotar que el modo B todavía usa
ordenador + ACR122U como lector: PN532 es otro proyecto.

## T3 — Pruebas de carga y base de datos

**Hacer:**

1. Crear un generador **repetible** de cuentas, tarjetas, recargas, bloqueos y
   movimientos ficticios bajo `servidor_api/tools/` o `servidor_api/tests/`.
   No usar DNI reales ni datos de producción. Dejar una semilla y comando para
   reproducir exactamente el escenario común.
2. Crear una prueba de carga para `/api/sync/` con 100, 500 y 800 validadores,
   ráfagas al recuperar conexión, 0–40 eventos por petición y varios tamaños
   de lista negra/recargas. Medir solicitudes por segundo, p50/p95/p99, errores,
   CPU/RAM y tamaño de respuesta. Medir también emisión, recarga y las páginas
   principales del panel; las cifras deben ir separadas.
3. Identificar las consultas lentas con `EXPLAIN (ANALYZE, BUFFERS)` sobre una
   base de ensayo. Proponer índices, agregados o partición por fecha **solo si
   las mediciones lo justifican**. Medir antes y después, incluidos los costes
   de escritura e índices. Coordinar migraciones con T1.
4. Publicar `docs/resultados-carga.md`: hardware, comandos, datos, tablas de
   resultados, cuello de botella, límite observado y próxima prueba. No llamar
   «capacidad del sistema» al rendimiento de una sola ruta.

**Archivos de partida:** `servidor_api/transporte/models.py`, `panel/views.py`,
`docker-compose.yml`, `tests/test_api.py`.

**Terminado cuando:** otro compañero puede repetir la carga sin secretos ni
datos reales; hay una línea base y una medición después de T1; el informe dice
qué falla primero y con cuántos validadores/eventos.

## T4 — Panel de operaciones con muchos registros

**Problema actual.** Cuentas, viajes y alertas ya tienen búsqueda o paginación,
pero la pantalla inicial calcula totales sobre movimientos y las vistas de
validadores/usuarios cargan listas completas. Falta probarlas con el escenario
de T3 y decidir qué información ayuda a resolver incidentes.

**Hacer:**

1. Entrevistar al menos a dos compañeros que actuarán como operadores. Escribir
   tres tareas concretas: localizar una tarjeta, entender por qué un validador
   no sincroniza y atender una alerta. Probar que encuentran cada cosa.
2. Paginar o filtrar validadores y usuarios en servidor; conservar búsqueda
   exacta de cuentas y paginación de viajes/alertas. No descargar decenas de
   miles de filas al navegador. Distinguir claramente «viajes recibidos» de
   «viajes pendientes en el bus» cuando ese dato exista.
3. Medir las consultas del resumen de siete días con los datos de T3. Si son
   costosas, usar un agregado actualizado o caché con fecha de última
   actualización visible. No mostrar cifras viejas como si fueran instantáneas.
4. Revisar teclado, foco, contraste, estados vacíos/error y diseño móvil.
   Añadir pruebas para permisos, filtros y paginación.

**Archivos de partida:** `servidor_api/panel/views.py`, `templates/panel/`,
`static/panel/`, `tests.py`.

**Terminado cuando:** las tres tareas de operador se pueden completar sin
recorrer una lista enorme; las páginas mantienen tiempos medidos en el
escenario común y siguen protegidas por los permisos actuales.

## T5 — Despliegue, observabilidad y recuperación

**Hacer:**

1. Documentar los componentes que ya existen: Caddy, Django/Gunicorn,
   PostgreSQL, GitHub Actions y copia cifrada. Dibujar qué pasa si falla cada
   uno, quién recibe el aviso y cómo comprobar la recuperación.
2. Medir y mostrar al equipo al menos: respuestas 5xx, tiempo de `/api/sync/`,
   validadores sin sincronizar, eventos pendientes por validador (si se reporta
   de forma segura), disco de PostgreSQL y resultado/antigüedad del respaldo.
   Empezar con lo más sencillo operable; una plataforma nueva requiere motivo.
3. Hacer un **ensayo de restauración en entorno aislado** con una copia de
   prueba. Verificar que se pueden consultar cuentas y viajes. Documentar
   duración, procedimiento y quién tiene acceso a la clave. No restaurar sobre
   la base de producción para esta tarea.
4. Documentar despliegue y reversión de migraciones incompatibles: versión de
   firmware/API, copia previa, pasos de verificación y responsable. Revisar que
   `.env`, tokens y claves nunca aparezcan en logs o PR.

**Archivos de partida:** `servidor_api/docker-compose*.yml`, `Caddyfile`,
`desplegar.sh`, `respaldar.sh`, `restaurar.sh`, `.github/workflows/`.

**Terminado cuando:** hay un tablero o reporte periódico con las métricas
anteriores, un ensayo de restauración documentado y un procedimiento que otra
persona puede seguir sin contactar al autor del despliegue.

## T6 — Integridad, seguridad y pruebas de fallo

**Hacer:**

1. Escribir `docs/modelo-amenazas.md` para tarjeta, microSD, puente USB, WiFi,
   token de validador, API y panel. Separar lo que el prototipo detecta hoy de
   lo que sigue siendo un riesgo (por ejemplo, dos clones usados sin conexión
   en buses distintos antes de sincronizar).
2. Probar corte de energía antes/después de escribir un viaje y antes/después
   de recibir la confirmación del servidor; reenvío, eventos fuera de orden,
   tarjeta bloqueada en un bus sin red y recarga repetida. Registrar saldo de
   tarjeta, saldo de servidor, eventos y alertas esperados para cada caso.
3. Definir procedimiento de pérdida/rotación del token de un validador y de la
   clave maestra. No guardar la clave maestra en el servidor para «resolver»
   sincronización. Evaluar dónde quedaría en hardware final (la microSD del
   prototipo contiene secretos en texto plano).
4. Añadir pruebas automatizadas donde sea posible y una lista corta de ensayos
   físicos reproducibles. Documentar los límites de NTAG215 y el camino hacia
   una tarjeta más segura sin prometer resistencia a clones que no se ha probado.

**Archivos de partida:** `servidor_api/transporte/servicios.py`,
`tests/test_api.py`, `pantalla_esp32/src/validador/`, `transporte/tarjeta/`.

**Terminado cuando:** los casos anteriores tienen resultado observado, riesgo
residual y responsable de la siguiente mejora; las pruebas automáticas de
reenvío y duplicados pasan.

## Comandos mínimos para validar un PR

```bash
# Servidor (desde servidor_api/, con Docker iniciado)
docker compose up -d --build
docker compose exec web python manage.py test
docker compose exec web python manage.py makemigrations --check --dry-run

# Firmware (desde pantalla_esp32/, con PlatformIO instalado)
pio run -e validador
```

La prueba de carga debe ejecutarse contra un entorno de ensayo, nunca contra el
servidor público sin un acuerdo del equipo. Los resultados de hardware deben
indicar placa, lector, microSD y red usados.

## Decisión sobre Kafka, Flink y Kubernetes

Estas herramientas **no son tareas de esta ronda**. T3 debe dejar una decisión
escrita basada en mediciones: qué problema no resuelve Django + PostgreSQL,
volumen observado, alternativa más simple y coste de operar una plataforma
nueva. Kafka tendría sentido para distribuir eventos durables entre varios
consumidores; Flink para análisis continuo con estado; Kubernetes para operar
varias instancias y servicios. Ninguno sustituye el cobro local ni corrige una
sincronización que envía listas completas.
