# M21-02 — Prueba de carga del servidor por MQTT (#156)

**Resultado:** con 50 validadores que mandan un evento cada 10 s durante 30 min, los 9000 eventos recibieron su acuse. La latencia del acuse fue de **252 ms en el percentil 95** (máximo 484 ms), sin swap y con unos 324 MiB de RAM en total. **Pero la prueba se hizo en este PC, con límites de memoria parecidos a los de la VM, y no en la VM.** La CPU medida (37 % de un núcleo, en promedio) es bastante más que el mínimo garantizado de la VM, así que no queda demostrado que la VM aguante esta carga de forma sostenida (ver «Qué falta para la VM»).

## Cómo se hizo

- **Simulador:** `herramientas/carga_mqtt.py`. Cada validador simulado hace lo mismo que el firmware: entra a Mosquitto con su usuario y su contraseña, manda un HOLA al conectar y cada 5 min, y un EVENTOS con un viaje cada 10 s, firmados con su clave HMAC. Igual que el firmware, cada EVENTOS lleva también los eventos que todavía no tienen acuse. Los validadores empiezan desfasados dentro de los primeros 10 s.
- **Latencia del acuse:** va desde que se publica el evento hasta que llega un ACUSE con `hasta_evento` mayor o igual que ese evento. El simulador comprueba la firma ECDSA del servidor en cada ACUSE.
- **Servidor:** `herramientas/carga/correr.sh` levanta una copia aparte del servidor (proyecto Compose `carga`) con su propia base de datos, sus volúmenes y sus secretos. Usa la misma imagen, el mismo `docker-compose.yml`, el puente MQTT y Mosquitto 2. Crea 50 validadores (801-850), cada uno con su cuenta, su tarjeta activa y S/ 200 de saldo. Al terminar borra la copia. El servidor local no se toca.
- **Límites** (`herramientas/carga/docker-compose.carga.yml`), pensados para parecerse a la VM.Standard.E2.1.Micro de 1 GB:

| Contenedor | RAM | Swap disponible | CPU |
|---|---:|---:|---|
| db (PostgreSQL 17) | 300 MiB | 512 MiB | núcleo 2 |
| web (gunicorn, 3 workers) | 250 MiB | 512 MiB | núcleo 2 |
| puente (puente_mqtt) | 150 MiB | 512 MiB | núcleo 2 |
| mosquitto | 50 MiB | 512 MiB | núcleo 2 |

Suman unos 750 MiB; los ~250 MiB restantes de la VM son para el sistema, dockerd y Caddy. Cada contenedor puede usar swap: si la usara, sería porque llegó a su límite de RAM. Los cuatro comparten **un solo núcleo**.

- **Mediciones:** cada 5 s se leen los cgroups de cada contenedor (`cpu.stat`, `memory.current`, `memory.swap.current`). La CPU está en % de un núcleo.
- **Equipo:** AMD Ryzen 7 5800H, Fedora 44 (kernel 7.3), Docker 29.9 y Compose 5.7. Commit `038b4f4` de `develop`. Fecha: 25 nov 2026, de 15:53 a 16:24.

Para repetirla: `PYTHON=<python con paho-mqtt y cryptography> herramientas/carga/correr.sh [carpeta]` (variables `VALIDADORES`, `CADA`, `DURACION`, `CARGA_CPU`). Los datos de esta prueba están en `herramientas/carga/resultados/2026-11-25/`.

## Resultados

**Eventos:** 9000 enviados, **9000 con acuse**, 0 sin acuse, 0 desconexiones y 0 paquetes inválidos. En la base de la copia quedaron 9000 viajes registrados y 9000 `EventoRecibido` por MQTT, sin ninguna alerta. `verificar_libro` dio: «Libro mayor: cuadra». El puente no tuvo fallos al procesar; solo los rechazos de conexión de antes de cargar las credenciales, que son esperados.

**Latencia del acuse (ms):**

| p50 | media | **p95** | p99 | máximo |
|---:|---:|---:|---:|---:|
| 109 | 134 | **252** | 330 | 484 |

Ningún acuse tardó más de 500 ms. Por tramos de 5 minutos (1500 eventos cada uno), el p95 fue de 251, 255, 251, 253, 254 y 255 ms, y el p50 se quedó entre 107 y 110 ms: no sube con el tiempo.

**CPU y RAM (360 muestras, cada 5 s):**

| Contenedor | CPU media | CPU p95 | CPU máx. | RAM media | RAM máx. | Swap |
|---|---:|---:|---:|---:|---:|---:|
| db | 23,5 % | 27,5 % | 41,0 % | 98,7 MiB | 113,6 MiB | 0 |
| puente | 13,3 % | 15,3 % | 22,7 % | 54,4 MiB | 55,8 MiB | 0 |
| web | 0,0 % | 0,0 % | 0,0 % | 151,2 MiB | 151,5 MiB | 0 |
| mosquitto | 0,2 % | 0,3 % | 0,9 % | 3,8 MiB | 5,1 MiB | 0 |
| **Total** | **37,0 %** | **42,7 %** | **64,1 %** | **308,2 MiB** | **324,1 MiB** | **0** |

Ninguna muestra tuvo swap. La RAM del total quedó en 324 MiB, lejos de los 750 MiB asignados: la carga no presiona la memoria.

## Criterios de #156

| Criterio | Estado |
|---|---|
| 50 validadores, un evento cada 10 s, 30 min, contra una VM de 1 GB | **A medias:** la carga completa sí, pero en este PC con límites de 1 GB, no en la VM |
| Informe con CPU, RAM y latencia de los acuses | Cumplido (este documento) |
| Latencia del acuse < 2 s en el p95 | Cumplido en local: 252 ms |
| Sin swap sostenida | Cumplido en local: 0 en todas las muestras |

## Qué falta para la VM

**La memoria y la swap no preocupan.** El total quedó en 324 MiB, el mismo orden que en la VM, porque son los mismos contenedores y la misma configuración.

**La CPU sí preocupa.** Cada evento cuesta unos **74 ms de CPU** de un núcleo de este PC: 37 % de un núcleo × 1800 s ÷ 9000 eventos. Casi todo se va en PostgreSQL (unos 47 ms) y en el puente (unos 27 ms), que registran el viaje, el asiento contable, la comprobación de clones, el avance del último evento contiguo y la firma ECDSA del acuse. Con 5 eventos por segundo:

- En este PC hacen falta unos 0,37 núcleos.
- La VM.Standard.E2.1.Micro tiene garantizado **1/8 de OCPU** y puede usar más CPU solo si el host la tiene libre. Su procesador (AMD EPYC de la serie E2) es además más lento por núcleo que este Ryzen. **Estimación, no medición:** con solo el mínimo garantizado, la VM procesaría del orden de 1 evento por segundo (unos 10 validadores a este ritmo). Por encima, los eventos se encolarían y la latencia crecería sin límite. Si el host le da CPU libre, como suele pasar, la carga cabría.

Para cerrarlo hay dos caminos:

1. **Correr la misma prueba en la VM.** No se puede hacer al lado de producción, porque una segunda copia del servidor no cabe en 1 GB, ni contra producción, porque metería 9000 viajes falsos en la base real. Haría falta una ventana con producción parada o una VM de pruebas temporal. Las dos cosas las decide el dueño del proyecto.
2. **Bajar el costo por evento** antes de medir en la VM, empezando por las consultas de PostgreSQL de `procesar_evento`.

## Lo que no cubre

- **No se probó TLS:** los validadores simulados entran por el 1883 sin cifrar. En producción usan el 8883 con TLS, que añade el cifrado y el saludo inicial de cada conexión en Mosquitto, aunque las conexiones son persistentes.
- **Caddy no corre** y la web no recibe tráfico (portal ni panel) durante la prueba.
- **No hay bloqueos urgentes ni recargas en línea** durante la prueba, ni validadores que se desconecten y reconecten.
- **El simulador corre en el mismo PC**, en otros núcleos, así que la red es la local y no internet. La latencia por internet sumaría el tiempo de ida y vuelta real del bus (WiFi y operador).
