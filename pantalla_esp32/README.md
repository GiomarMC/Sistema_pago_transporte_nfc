# Validador de bus con ESP32

Dispositivo que va en el bus: una ESP32 con un lector NFC PN532, una pantalla LCD
de 1.6" y un lector de tarjetas microSD. En el modo validador hace todo el cobro
por sí misma, sin ordenador: lee la tarjeta, la verifica, descuenta la tarifa,
graba el nuevo saldo, guarda el viaje en la microSD, muestra el resultado y sube
los viajes al servidor por WiFi.

Este dispositivo **no sustituye al servidor**: sustituye al validador que corría
en el ordenador (`transporte/validador.py`). El servidor sigue siendo la fuente
de verdad del saldo, las recargas y la detección de fraude; el validador, sea el
del ordenador o el de la ESP32, solo cobra y luego le sube los viajes.

Contenido:

1. [Lugar en el sistema](#1-lugar-en-el-sistema)
2. [Modos de funcionamiento](#2-modos-de-funcionamiento)
3. [Hardware y conexiones](#3-hardware-y-conexiones)
4. [Instalación de las herramientas](#4-instalación-de-las-herramientas)
5. [Programas de la ESP32](#5-programas-de-la-esp32)
6. [Modo A: validador en el ordenador + pantalla](#6-modo-a-validador-en-el-ordenador--pantalla)
7. [Modo B: validador autónomo en la ESP32](#7-modo-b-validador-autónomo-en-la-esp32)
8. [Datos en la microSD](#8-datos-en-la-microsd)
9. [Protocolo con el ordenador](#9-protocolo-con-el-ordenador)
10. [Estructura de la carpeta](#10-estructura-de-la-carpeta)
11. [Solución de problemas](#11-solución-de-problemas)
12. [Estado y pendientes](#12-estado-y-pendientes)

---

## 1. Lugar en el sistema

```
                         SERVIDOR (servidor_api/, no cambia)
                    cuentas, saldos reales, recargas, fraude
                        ^                           ^
            sincroniza  |                           |  API
                        |                           |
   VALIDADOR DEL BUS (este dispositivo)       PC DE ATENCIÓN (transporte/)
   cobra sin conexión, guarda los viajes      ACR122U: emitir, recargar,
   y los sube cuando hay red                  bloquear, consultar tarjetas
```

- Las tarjetas se emiten y se recargan en el PC de atención, con el ACR122U y
  los programas de `transporte/`. Las emitidas allí funcionan en la ESP32: el
  formato de datos y las firmas son idénticos, y un test lo comprueba byte a
  byte (sección 5).
- Lo que el validador necesita para cobrar sin red (lista negra, tarifas,
  recargas pendientes) lo recibe del servidor al sincronizar.

## 2. Modos de funcionamiento

| | Modo A | Modo B |
|---|---|---|
| Quién cobra | El ordenador (`validador.py`) | **La ESP32** |
| Lector NFC | ACR122U en el ordenador | **PN532 conectado a la ESP32** |
| Papel del ordenador | Hace todo y envía el resultado a la pantalla | Ninguno en el bus. Solo para configurarla la primera vez (`puente_nfc.py --configurar`) |
| Dónde se guardan los viajes | Base de datos local del ordenador | microSD de la ESP32 |
| Sincronización con el servidor | Sí (`validador.py`) | **Por WiFi**, desde la ESP32 (sección 7.5) |
| Firmware de la ESP32 | `esp32dev` | `validador` |

Hasta el 10 de octubre de 2026, el modo B usaba el ACR122U del ordenador a
través de `puente_nfc.py` ("cable largo" hasta el lector). Con el PN532 solo
cambió la pieza que habla con la tarjeta (`lib/LectorNFC/LectorPN532`); la
lógica de cobro, la microSD y la pantalla son las mismas.

## 3. Hardware y conexiones

| Componente | Detalle |
|---|---|
| ESP32 | DevKit de 38 pines con módulo ESP32-WROOM-32, chip USB CP210x |
| Pantalla | LCD 1.6" SPI, controlador SSD1283A, 130x130 |
| Lector microSD | Módulo de 6 pines rotulados 3V3, CS, MOSI, CLK, MISO, GND (va a 3,3 V) |
| Tarjeta microSD | 32 GB o menos, en FAT32 (probado con 8 GB) |
| Protoboard y cables dupont | |
| Lector NFC | Módulo PN532 (rojo, con conector I2C de 4 pines y SPI de 8), en modo I2C |

La placa rotula cada pin como P + su número de GPIO (P5 = GPIO5). Posiciones
contadas desde arriba, con la antena arriba y el conector USB abajo. La pantalla
y la microSD van cada una en su propio bus SPI; el PN532, en el bus I2C.

| Pantalla | ESP32 | Lado y posición |
|---|---|---|
| VCC | 3V3 | Izquierdo, 1.º |
| LED | 3V3 (por la línea de 3,3 V del protoboard) | Izquierdo, 1.º |
| GND | GND | Derecho, 1.º |
| SDA | P23 | Derecho, 2.º |
| SCK | P18 | Derecho, 9.º |
| CS | P5 | Derecho, 10.º |
| RST | P17 | Derecho, 11.º |
| A0 | P16 | Derecho, 12.º |

| microSD | ESP32 | Lado y posición |
|---|---|---|
| 3V3 | 3V3 (por la línea de 3,3 V del protoboard) | Izquierdo, 1.º |
| CS | P26 | Izquierdo, 10.º |
| MISO | P27 | Izquierdo, 11.º |
| CLK | P14 | Izquierdo, 12.º |
| GND | GND | Izquierdo, 14.º |
| MOSI | P13 | Izquierdo, 15.º |

| PN532 | ESP32 | Lado y posición |
|---|---|---|
| VCC (conector de arriba) | 3V3 (por la línea de 3,3 V del protoboard) | Izquierdo, 1.º |
| GND (conector de arriba) | GND | Derecho, 7.º |
| SDA | P21 | Derecho, 6.º |
| SCL | P22 | Derecho, 3.º |
| IRQ (conector de la derecha) | P32 | Izquierdo, 7.º |
| RSTO | Sin conectar | |

- **Interruptor del PN532 en I2C: 1 en ON, 2 en OFF**, cambiado con el módulo
  sin alimentación (solo lo lee al encenderse). De fábrica viene en HSU (los
  dos en OFF): en ese modo el validador muestra "Sin lector".
- **Nada va al pin 5V.** La pantalla, la microSD y el PN532 trabajan a 3,3 V
  (con 5 V, el PN532 pondría SDA y SCL a 5 V).
- No uses los pines P0, P2, P12 y P15 ni SD0-SD3, CMD y CLK: un módulo
  conectado ahí impide que la ESP32 arranque o lea su memoria.
- Desconecta el USB de la ESP32 antes de poner o quitar cables.

El informe `docs/informe-conexiones-validador-esp32.pdf` tiene los diagramas de
cableado, el montaje paso a paso y las pruebas de cada componente.

## 4. Instalación de las herramientas

### 4.1 PlatformIO (para compilar y cargar los programas en la ESP32)

```bash
pip install platformio          # o la extensión "PlatformIO IDE" de VS Code
```

La primera compilación descarga el compilador de la ESP32 (unos 1,5 GB en
`~/.platformio`).

### 4.2 Driver USB y permisos

| Sistema | Qué hacer |
|---|---|
| Linux | Suele reconocer la placa sola (`/dev/ttyUSB0`). Para tener permiso sobre el puerto: `sudo usermod -aG dialout $USER` y vuelve a iniciar sesión. |
| Windows | Si en el Administrador de dispositivos aparece un dispositivo desconocido, instala el driver CP210x (Silicon Labs) o CH340, según el chip junto al conector USB. El puerto será `COM3`, `COM4`... |
| macOS | El puerto aparece como `/dev/cu.usbserial-XXXX` o `/dev/cu.SLAB_USBtoUART`. |

### 4.3 Programas del ordenador

Los del lector (`transporte/`) ya instalados según el README principal, más
`pyserial` (incluido en `transporte/requirements.txt`; en Fedora,
`sudo dnf install python3-pyserial`).

## 5. Programas de la ESP32

Todos se compilan y cargan desde esta carpeta. El puerto puede indicarse con
`--upload-port` (por ejemplo `--upload-port COM3`).

| Entorno | Para qué | Cargar |
|---|---|---|
| `esp32dev` | Pantalla del modo A: muestra lo que le envía `validador.py` | `pio run -e esp32dev -t upload` |
| `prueba` | Prueba de componentes: colores de la pantalla, escritura y lectura de la microSD, eco por USB | `pio run -e prueba -t upload` y después `pio device monitor` |
| `validador` | Validador autónomo del modo B, con el PN532 | `pio run -e validador -t upload` |

Test del formato de la tarjeta, que se ejecuta en la propia ESP32 y compara el
C++ con ejemplos generados por el código Python:

```bash
pio test -e validador
```

Debe terminar en `PASSED`. Si alguien cambia el formato en Python
(`transporte/tarjeta/formato.py`), hay que regenerar
`test/test_formato/vectores.h` y repetir este test.

Notas de carga:

- La velocidad de carga está fijada en 115200 (`upload_speed`): a 460800 la
  carga fallaba con esta placa.
- Si la carga se queda en `Connecting....____`, mantén pulsado el botón BOOT de
  la placa hasta que empiece a escribir.
- Ningún otro programa puede tener abierto el puerto durante la carga (cierra el
  validador, el puente o `pio device monitor`).

## 6. Modo A: validador en el ordenador + pantalla

1. Carga el firmware `esp32dev`.
2. Con el servidor en marcha y el ACR122U conectado al ordenador:

   ```bash
   cd transporte
   python3 validador.py --id 101 --pantalla auto   # o el puerto: /dev/ttyUSB0, COM3...
   ```

El ordenador cobra y sincroniza como siempre; la ESP32 solo muestra el
resultado. Si la pantalla se desconecta, el validador sigue cobrando y la
reconecta sola en cuanto vuelve (lo revisa cada 5 segundos mientras no hay
tarjetas).

## 7. Modo B: validador autónomo en la ESP32

### 7.1 Primera vez: configurar la ESP32

1. Carga el firmware `validador` con la microSD insertada.
2. La ESP32 necesita su número de validador y **la misma clave maestra con la
   que se emitieron las tarjetas** (`transporte/claves/clave_maestra.bin`; ver
   la sección 7.5 del README principal). Con la ESP32 conectada por USB al
   ordenador que tiene la clave (no hace falta el ACR122U):

   ```bash
   cd transporte
   python3 puente_nfc.py --configurar --validador 105 --servidor https://subepe.app
   ```

   La consola debe mostrar `[ESP32] Configuración guardada en la microSD`. Cierra
   con `Ctrl+C`.
3. Añade una red WiFi de 2,4 GHz con el portal (sección 7.5).

Usa un número de validador que exista en el servidor (por ejemplo, uno creado
con `crear_validador`). Solo hay que configurarla una vez: la configuración
queda en la microSD.

### 7.2 Uso diario

Basta con alimentar la ESP32 por USB (cargador, batería externa o el
ordenador). Al arrancar:

```
1,6 s   Lector PN532 v1.6 listo
1,6 s   Conectando al WiFi MUNOZ...
9,2 s   WiFi conectado a MUNOZ: IP 192.168.100.10, señal -37 dBm
14,3 s  Sincronizado: 0 eventos subidos, 0 pendientes, 0 en lista negra, 0 recargas pendientes
```

- **La hora la da el servidor** en la primera sincronización: la ESP32 no tiene
  reloj con pila. Hasta entonces la pantalla dice "Sin hora" y no cobra. Si
  después se pierde el WiFi, sigue cobrando con normalidad.
- Sin WiFi al arrancar, también toma la hora del ordenador si está conectada
  por USB con `puente_nfc.py` abierto.
- Para ver qué hace, con la ESP32 conectada al ordenador: `pio device monitor`
  o `python3 puente_nfc.py` (muestra cada toque):

  ```
  14:24:40  [ESP32] PASA 04653E22CA2A81 | General S/ 1.30 | Saldo S/ 3.70 (329 ms)
  13:32:46  [ESP32] RECHAZADA 04B3E121CA2A81 | Saldo insuficiente | Saldo S/ 0.70 (92 ms)
  ```

### 7.3 El ordenador ya no hace falta en el bus

`puente_nfc.py` solo se usa para configurar la ESP32 (y como monitor). No guarda
nada ni toma decisiones, así que sirve cualquier ordenador con Linux, Windows o
macOS y Python con `pyserial`. Solo `--configurar` necesita la clave maestra.

### 7.4 Qué hace la ESP32 en cada toque

Lo mismo que `validador.py`, en el mismo orden:

1. Autentica la tarjeta con su contraseña y comprueba el código de respuesta.
2. Lee los datos y verifica las firmas (HMAC-SHA256).
3. Rechaza si la tarjeta tiene grabado "bloqueada" o está en la lista negra; en
   ese caso graba el bloqueo en la propia tarjeta.
4. Detecta copias antiguas restauradas (el n.º de operación retrocede).
5. No cobra dos veces si el mismo validador la cobró hace menos de 60 segundos.
6. Suma las recargas remotas pendientes, descuenta la tarifa, graba el nuevo
   saldo en la copia libre de la tarjeta y lo verifica releyendo.
7. Guarda el viaje en la microSD **antes** de dar por terminado el cobro. Sin
   microSD no cobra.

| Pantalla | Cuándo |
|---|---|
| Verde, PASE | Cobro aceptado (tarifa, saldo y recarga aplicada si la hubo) o "Ya pagado" |
| Rojo, RECHAZADA | Saldo insuficiente, tarjeta bloqueada, tarjeta no válida o no emitida |
| Ámbar, ATENCION | Lectura cortada: el pasajero debe volver a acercar la tarjeta (no se cobró) |
| Azul, "Sin lector" | El PN532 no responde: cables o interruptor (sección 3) |
| Azul, "Sin hora" | Aún no sincronizó con el servidor; espera al WiFi |
| Azul, "Sin configurar" | Falta la configuración (sección 7.1) |
| Azul, "Error microSD" | No hay tarjeta microSD o no se puede leer |

### 7.5 Sincronización con el servidor por WiFi

La ESP32 sube sola los viajes de la microSD a `/api/sync/` cada 30 segundos y
recibe la lista negra, las recargas pendientes, las tarifas y la hora. Sin red
sigue cobrando y acumula los viajes hasta que vuelva la conexión. La primera
línea de la pantalla indica el estado: `WiFi OK`, `Sin WiFi` o `Sin servidor`.

Necesita tres datos en `config.txt`:

| Dato | Cómo se configura |
|---|---|
| Red WiFi (2,4 GHz) | Desde el celular, con el portal (abajo), o con `--wifi` en el puente |
| `servidor` | Con el puente: `--servidor` |
| `token` (del validador en ese servidor) | Lo pone el puente, sacándolo de `transporte/api.json` |

**Servidor en internet (HTTPS).** Con `api.json` apuntando al servidor
desplegado (`https://subepe.app`), desde el ordenador con la clave
maestra:

```bash
cd transporte
python3 puente_nfc.py --configurar --validador 105 --servidor https://subepe.app
```

Así cambian solo el servidor y el token; las redes WiFi guardadas se conservan.
La ESP32 verifica el certificado del servidor con las raíces de Let's Encrypt
incluidas en el firmware (`src/validador/raices_tls.h`), así que el token nunca
viaja sin cifrar ni se entrega a un servidor falso. Por eso el servidor debe
usar Let's Encrypt (ya fijado en `servidor_api/Caddyfile`). La conexión se
mantiene abierta entre sincronizaciones: la negociación TLS (1-3 s, durante los
que no se cobra) solo se repite si se corta.

**Servidor en la red local (pruebas).** `--servidor http://192.168.1.50:8000`
o `http://nombre.local:8000` (el ordenador se busca por mDNS; no funciona entre
las bandas de 2,4 y 5 GHz de algunos routers). Con `--wifi "Mi red"` se
configuran a la vez la red, el servidor y el token.

**Portal WiFi desde el celular.** Para cambiar de red sin ordenador, mantén
pulsado el botón BOOT 3 segundos (o arranca sin redes guardadas). La pantalla
muestra una red `Validador-105` y su contraseña; al conectarse, el celular abre
solo la página para elegir la red (aunque tenga datos móviles). Guarda hasta 5
redes y las prueba en orden. El portal no muestra ni cambia la clave maestra ni
el token.

## 8. Datos en la microSD

Archivos de texto, para poder revisarlos en un ordenador si hace falta:

| Archivo | Contenido |
|---|---|
| `config.txt` | `validador=103`, `clave_maestra=<64 caracteres hex>`, `servidor`, `token` y la red WiFi configurada con el puente |
| `redes.txt` | Redes WiFi guardadas desde el portal (`nombre<TAB>contraseña`, una por línea) |
| `eventos.txt` | Un JSON por línea: cada viaje, incidencia o fraude, en el formato que espera `/api/sync/` |
| `estado.txt` | Siguiente n.º de evento y último subido al servidor |
| `ultima_op.txt` | Última operación vista de cada tarjeta (detecta copias restauradas) |
| `lista_negra.txt`, `tarifas.txt`, `recargas.txt` | Los escribe la sincronización. Si no existen, se usan las tarifas por defecto (general S/ 1.30, estudiante S/ 0.80) y listas vacías |

Ejemplo de una línea de `eventos.txt` (valores de ejemplo):

```json
{"id":1,"tipo":"viaje","fecha":"2026-10-01T21:11:49+00:00","tarjeta_id":4,"uid":"04B3E121CA2A81","monto":80,"saldo_final":710,"operacion":9,"contador":31,"recarga_hasta":null,"detalle":null}
```

**Cuidado:** `config.txt` contiene la clave maestra y el token en texto plano. Quien tenga
la microSD puede leerla. Para el proyecto es aceptable; en un sistema real la
clave iría en un chip seguro (SAM) o en la memoria cifrada de la ESP32.

## 9. Protocolo con el ordenador

Una línea de texto ASCII por mensaje, a 115200 baudios por el USB de la ESP32.
El validador solo saluda al ordenador mientras le falta la configuración o la
hora, o mientras este conteste:

| ESP32 envía | Puente responde | Significado |
|---|---|---|
| `>n HOLA` | `<n OK PUENTE 1` o `<n CFG clave=valor;...` | Saludo (con `--configurar`, entrega la configuración) |
| `>n HORA` | `<n OK <unix>` | Hora del ordenador (la del servidor tiene preferencia) |

- `n` es el número de petición: la respuesta lo repite y la ESP32 descarta
  cualquier respuesta con otro número.
- Las líneas que empiezan por `#` son mensajes de la ESP32 para la consola.
- El puente también atiende `ESPERA`, `CMD`, `ESCRIBE`, `FIN` y `RETIRADA`, con
  los que `LectorPuente` usaba el ACR122U como lector de la ESP32. El firmware
  ya no los usa, pero `LectorPuente` sigue en `lib/LectorNFC/` por si hace
  falta volver al ACR122U.

## 10. Estructura de la carpeta

```
pantalla_esp32/
|-- platformio.ini              Entornos: esp32dev, prueba, validador
|-- src/
|   |-- main.cpp                Firmware de pantalla (modo A)
|   |-- prueba_componentes.cpp  Prueba de pantalla, microSD y USB
|   `-- validador/
|       |-- main.cpp            Validador (modo B): lógica de cobro
|       `-- almacen.cpp/.h      Datos en la microSD
|-- lib/
|   |-- PantallaUI/             Pantallas de espera, PASE, RECHAZADA y ATENCION
|   |-- TarjetaTransporte/      Formato de la tarjeta y firmas (copia exacta del Python)
|   `-- LectorNFC/              Interfaz del lector, LectorPN532 (el del validador) y LectorPuente
`-- test/test_formato/          Test del formato contra ejemplos del Python
```

En `transporte/`:

| Archivo | Para qué |
|---|---|
| `puente_nfc.py` | Configura la ESP32 del modo B y muestra sus mensajes |
| `pantalla.py` | Envío de resultados a la pantalla en el modo A |

## 11. Solución de problemas

| Síntoma | Solución |
|---|---|
| La pantalla queda en blanco tras reconectarla | Reinicia la ESP32 (botón EN o desconectar y conectar el USB): la pantalla solo se configura al arrancar. Desconecta el USB antes de tocar cables. |
| Todas las tarjetas salen "No emitida por el sistema" | La clave maestra de la ESP32 no es la de las tarjetas. Configura de nuevo (7.1) desde el PC que tiene la clave correcta. |
| La pantalla dice "Sin lector" | El PN532 no responde. Revisa el interruptor (I2C: 1 en ON, 2 en OFF; se lee al encender, así que desconecta y conecta el USB tras cambiarlo) y los cables SDA→P21, SCL→P22, IRQ→P32. |
| La ESP32 se reinicia en bucle o no se puede cargar el firmware (`flash read err`, `Failed to communicate with the flash chip`) | Un cable de algún módulo está en P12 o en SD0-SD3/CMD/CLK, o hay un falso contacto en el protoboard. Quita los módulos y conéctalos de uno en uno. |
| La pantalla dice "Sin hora" | No ha sincronizado con el servidor desde que arrancó. Revisa el WiFi y el servidor. |
| El puente dice "No se encontró la ESP32", aunque la placa está encendida y la pantalla funciona | El ordenador no reconoce la placa por USB. En Linux, `journalctl -k` muestra `device descriptor read error -71`. Casi siempre es **un cable USB que solo carga** (alimenta, pero no transmite datos): usa otro cable de datos. Si no, prueba a girar el conector USB-C y otro puerto. Ningún comando con `sudo` lo arregla. |
| El puente dice que no encuentra el lector | Tras reiniciar el ordenador, el ACR122U a veces no se inicializa: desconéctalo y vuelve a conectarlo, y ejecuta `sudo systemctl restart pcscd`. |
| `could not open port` o `Permission denied` | El puerto lo usa otro programa (solo uno a la vez: validador, puente, monitor serie o carga de firmware), o falta el grupo `dialout` en Linux. |
| La carga del firmware falla en `Unable to verify flash chip connection` | Usa `upload_speed = 115200` (ya fijado) y otro cable USB de datos. |
| "Error microSD" en la pantalla | Tarjeta no insertada, de más de 32 GB o no FAT32, o cableado de la microSD (prueba con el firmware `prueba`). |
| Cobra pero el saldo de la cuenta no baja en el servidor | Los viajes suben al sincronizar (cada 30 s con WiFi). Mira el estado en la pantalla y los mensajes (`pio device monitor`). |
| La pantalla dice "Sin WiFi" | La red no es de 2,4 GHz, la contraseña es incorrecta o no hay señal. Corrígela con el portal (botón BOOT 3 s). |
| La pantalla dice "Sin servidor" | Los mensajes de la ESP32 muestran el motivo: `HTTP 401` es un token que no es de ese servidor (vuelve a configurar con `--servidor`); un error `TLS` es un certificado que no es de Let's Encrypt; `connection refused` o un tiempo agotado, el servidor caído o una URL mal escrita. |

## 12. Estado y pendientes

Probado (1 de octubre de 2026):

- Pantalla, microSD (8 GB) y comunicación USB con el firmware de prueba.
- Modo A con tarjetas reales.
- Test del formato en la ESP32: el C++ coincide byte a byte con el Python.
- Portal WiFi desde un celular con datos móviles activos.

Probado (10 de octubre de 2026), con el PN532 y sin ordenador:

- Arranque, WiFi y sincronización por HTTPS con `https://subepe.app` en unos
  15 s; la hora se toma del servidor.
- Cobro completo con escritura y relectura en la tarjeta: 329 ms. El viaje
  subió al servidor en la siguiente sincronización y el saldo de la cuenta
  coincide con el de la tarjeta.
- Rechazos: tarjeta no emitida en 88 ms, saldo insuficiente en 92 ms.

Pendiente:

- **Probar el puente y la pantalla en Windows y macOS.** El código evita lo
  específico de Linux (el puerto se detecta solo y los errores del lector no
  dependen del idioma del sistema), pero solo se ha probado en Fedora 42.
- **Recarga remota entregada por el validador** con el PN532 (recarga por DNI
  en el servidor y toque en el bus): el código es el mismo que con el puente,
  pero falta probarlo.
- **Reloj con pila (DS3231)** en el mismo bus I2C, para cobrar aunque la ESP32
  arranque sin WiFi.
- Medir p50/p95 del toque y tasa de fallos frente al ACR122U (tarea T11).
- Buzzer y LEDs para indicar el resultado sin mirar la pantalla.
