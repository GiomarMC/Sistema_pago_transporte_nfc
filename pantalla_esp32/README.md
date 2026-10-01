# Validador de bus con ESP32

Dispositivo que va en el bus: una ESP32 con una pantalla LCD de 1.6" y un lector
de tarjetas microSD. Muestra al pasajero el resultado de cada toque y, en el modo
validador, hace todo el cobro por sí misma: verifica la tarjeta, descuenta la
tarifa, graba el nuevo saldo y guarda el viaje en la microSD.

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
7. [Modo B: validador en la ESP32 + puente](#7-modo-b-validador-en-la-esp32--puente)
8. [Datos en la microSD](#8-datos-en-la-microsd)
9. [Protocolo del puente](#9-protocolo-del-puente)
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

| | Modo A | Modo B | Futuro |
|---|---|---|---|
| Quién cobra | El ordenador (`validador.py`) | **La ESP32** | La ESP32 |
| Lector NFC | ACR122U en el ordenador | ACR122U en el ordenador, usado a través del puente | Módulo PN532 conectado a la ESP32 |
| Papel del ordenador | Hace todo y envía el resultado a la pantalla | Solo presta el lector (`puente_nfc.py`): reenvía comandos, sin lógica | Ninguno |
| Dónde se guardan los viajes | Base de datos local del ordenador | microSD de la ESP32 | microSD de la ESP32 |
| Sincronización con el servidor | Sí (`validador.py`) | **Pendiente** (fase 4, por WiFi) | Por WiFi |
| Firmware de la ESP32 | `esp32dev` | `validador` | `validador` (con otro lector) |

El modo B simula el validador independiente: todo el programa corre en la
ESP32 y el ordenador hace de "cable largo" hasta el lector. Cuando llegue el
PN532 solo cambia la pieza que habla con la tarjeta (`lib/LectorNFC/`); la
lógica de cobro, la microSD y la pantalla siguen igual, y el ordenador deja de
hacer falta.

## 3. Hardware y conexiones

| Componente | Detalle |
|---|---|
| ESP32 | DevKit de 38 pines con módulo ESP32-WROOM-32, chip USB CP210x |
| Pantalla | LCD 1.6" SPI, controlador SSD1283A, 130x130 |
| Lector microSD | Módulo de 6 pines rotulados 3V3, CS, MOSI, CLK, MISO, GND (va a 3,3 V) |
| Tarjeta microSD | 32 GB o menos, en FAT32 (probado con 8 GB) |
| Protoboard y cables dupont | |
| Lector NFC | ACS ACR122U, conectado al ordenador |

La placa rotula cada pin como P + su número de GPIO (P5 = GPIO5). Posiciones
contadas desde arriba, con la antena arriba y el conector USB abajo. La pantalla
va toda al lado derecho y la microSD al izquierdo; cada una en su propio bus SPI.

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

- **Nada va al pin 5V.** La pantalla y la microSD trabajan a 3,3 V.
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
| `validador` | Validador completo del modo B | `pio run -e validador -t upload` |

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

## 7. Modo B: validador en la ESP32 + puente

### 7.1 Primera vez: configurar la ESP32

1. Carga el firmware `validador` con la microSD insertada.
2. La ESP32 necesita su número de validador y **la misma clave maestra con la
   que se emitieron las tarjetas** (`transporte/claves/clave_maestra.bin`; ver
   la sección 7.5 del README principal). El puente se los envía y la ESP32 los
   guarda en la microSD:

   ```bash
   cd transporte
   python3 puente_nfc.py --configurar --validador 103
   ```

   La consola debe mostrar `[ESP32] Configuración guardada en la microSD` y
   `[ESP32] Validador 103 listo`. Cierra con `Ctrl+C`.

Usa un número de validador que exista en el servidor (por ejemplo, uno creado
con `crear_validador`), para que la sincronización de la fase 4 funcione sin
cambios. Solo hay que configurarla una vez: la configuración queda en la
microSD.

### 7.2 Uso diario

Con el ACR122U y la ESP32 conectados al ordenador:

```bash
cd transporte
python3 puente_nfc.py        # en Windows: python puente_nfc.py
```

El puente busca solo el puerto de la ESP32 por el chip USB de la placa (CP210x,
CH340...). Si hay varias placas conectadas, indica cuál con `--puerto`
(`/dev/ttyUSB0` en Linux, `COM3` en Windows, `/dev/cu.usbserial-XXXX` en macOS).

- La pantalla muestra "Acerque su tarjeta", el número de bus y cuántos viajes
  quedan sin subir al servidor.
- Cada toque aparece en la consola tal como lo informa la ESP32:

  ```
  16:11:49  [ESP32] PASA 04B3E121CA2A81 | Estudiante S/ 0.80 | Saldo S/ 7.10 (469 ms)
  16:11:55  [ESP32] RECHAZADA 0412BD11CE2A81 | Tarjeta no valida | No emitida por el sistema (129 ms)
  ```

- `--detalle` muestra además cada comando que pide la ESP32 y su respuesta.
- Al abrir el puente, la ESP32 tarda unos 2 segundos en saludar y quedar lista.

### 7.3 Cualquier ordenador puede hacer de puente

El puente no guarda nada ni toma decisiones: la lógica está en la ESP32 y la
configuración y los viajes, en su microSD. Por eso sirve **cualquier ordenador
con Linux, Windows o macOS**, no solo el que la configuró. Un iPhone o iPad no
sirve: iOS no permite usar lectores de tarjetas USB ni ejecutar el puente.

| El ordenador puente necesita | No necesita |
|---|---|
| El ACR122U funcionando (sección 7.4 del README principal: en Linux, `pcscd` y el driver `pn533` desactivado; en Windows, el driver de ACS; en macOS, nada o el driver de ACS) | La clave maestra |
| Python 3.9 o superior con `pyscard` y `pyserial` (`pip install -r transporte/requirements.txt`) | El servidor, Docker, `api.json` o la base de datos |
| La carpeta `transporte/` del repositorio | Volver a configurar la ESP32 |
| **La hora del sistema correcta** (automática): la ESP32 no tiene reloj con pila y toma la hora del puente al conectarse | |

Solo hace falta la clave maestra para **cambiar** la configuración de la ESP32
(otra microSD, otro número de validador): `--configurar` debe ejecutarse desde
un ordenador con la clave con la que se emitieron las tarjetas.

Para comprobar que el lector funciona en un ordenador nuevo, antes de usar el
puente: `python3 detectar_ultralight.py --una` con una tarjeta encima.

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
| Azul, "Sin lector" | El puente no está en marcha |
| Azul, "Sin configurar" | Falta la configuración (sección 7.1) |
| Azul, "Error microSD" | No hay tarjeta microSD o no se puede leer |

## 8. Datos en la microSD

Archivos de texto, para poder revisarlos en un ordenador si hace falta:

| Archivo | Contenido |
|---|---|
| `config.txt` | `validador=103`, `clave_maestra=<64 caracteres hex>` (y en la fase 4, WiFi, servidor y token) |
| `eventos.txt` | Un JSON por línea: cada viaje, incidencia o fraude, en el formato que espera `/api/sync/` |
| `estado.txt` | Siguiente n.º de evento y último subido al servidor |
| `ultima_op.txt` | Última operación vista de cada tarjeta (detecta copias restauradas) |
| `lista_negra.txt`, `tarifas.txt`, `recargas.txt` | Los escribirá la sincronización. Si no existen, se usan las tarifas por defecto (general S/ 1.30, estudiante S/ 0.80) y listas vacías |

Ejemplo de una línea de `eventos.txt` (valores de ejemplo):

```json
{"id":1,"tipo":"viaje","fecha":"2026-10-01T21:11:49+00:00","tarjeta_id":4,"uid":"04B3E121CA2A81","monto":80,"saldo_final":710,"operacion":9,"contador":31,"recarga_hasta":null,"detalle":null}
```

**Cuidado:** `config.txt` contiene la clave maestra en texto plano. Quien tenga
la microSD puede leerla. Para el proyecto es aceptable; en un sistema real la
clave iría en un chip seguro (SAM) o en la memoria cifrada de la ESP32.

## 9. Protocolo del puente

Una línea de texto ASCII por mensaje, a 115200 baudios por el USB de la ESP32.
La ESP32 pregunta y el puente responde:

| ESP32 envía | Puente responde | Significado |
|---|---|---|
| `>n HOLA` | `<n OK PUENTE 1` o `<n CFG clave=valor;...` | Saludo (con `--configurar`, entrega la configuración) |
| `>n HORA` | `<n OK <unix>` | Hora del ordenador (la ESP32 no tiene reloj con pila) |
| `>n ESPERA ms` | `<n OK <uid>` o `<n NADA` | Espera una tarjeta hasta `ms` milisegundos |
| `>n CMD <hex>` | `<n OK <hex>` o `<n ERR motivo>` | Comando nativo de la tarjeta (autenticar, leer, contador) |
| `>n ESCRIBE pag <hex>` | `<n OK` o `<n ERR motivo>` | Escribe páginas consecutivas de 4 bytes |
| `>n FIN` | `<n OK` | Termina la sesión dejando la tarjeta sin autenticar |
| `>n RETIRADA ms` | `<n OK` o `<n NADA` | Espera hasta `ms` a que se retire la tarjeta |

- `n` es el número de petición: la respuesta lo repite y la ESP32 descarta
  cualquier respuesta con otro número. Así, mensajes atrasados o duplicados en
  el puerto (por ejemplo, saludos enviados mientras el puente estaba cerrado)
  no pueden desincronizar el diálogo.
- Las líneas que empiezan por `#` son mensajes de la ESP32 para la consola.

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
|   `-- LectorNFC/              Interfaz del lector y LectorPuente; aquí irá LectorPN532
`-- test/test_formato/          Test del formato contra ejemplos del Python
```

En `transporte/`:

| Archivo | Para qué |
|---|---|
| `puente_nfc.py` | Puente del modo B |
| `pantalla.py` | Envío de resultados a la pantalla en el modo A |

## 11. Solución de problemas

| Síntoma | Solución |
|---|---|
| La pantalla queda en blanco tras reconectarla | Reinicia la ESP32 (botón EN o desconectar y conectar el USB): la pantalla solo se configura al arrancar. Desconecta el USB antes de tocar cables. |
| Todas las tarjetas salen "No emitida por el sistema" | La clave maestra de la ESP32 no es la de las tarjetas. Configura de nuevo (7.1) desde el PC que tiene la clave correcta. |
| La pantalla dice "Sin lector" | El puente no está en marcha o se cerró. Arranca `puente_nfc.py`. |
| El puente dice que no encuentra el lector | Tras reiniciar el ordenador, el ACR122U a veces no se inicializa: desconéctalo y vuelve a conectarlo, y ejecuta `sudo systemctl restart pcscd`. |
| `could not open port` o `Permission denied` | El puerto lo usa otro programa (solo uno a la vez: validador, puente, monitor serie o carga de firmware), o falta el grupo `dialout` en Linux. |
| La carga del firmware falla en `Unable to verify flash chip connection` | Usa `upload_speed = 115200` (ya fijado) y otro cable USB de datos. |
| "Error microSD" en la pantalla | Tarjeta no insertada, de más de 32 GB o no FAT32, o cableado de la microSD (prueba con el firmware `prueba`). |
| Cobra pero el saldo de la cuenta no baja en el servidor | Es lo esperado hasta la fase 4: los viajes se quedan en `eventos.txt` de la microSD. |

## 12. Estado y pendientes

Probado (1 de octubre de 2026):

- Pantalla, microSD (8 GB) y comunicación USB con el firmware de prueba.
- Modo A con tarjetas reales.
- Modo B con tarjetas reales: cobro aceptado en unos 470 ms y rechazos de
  tarjetas no emitidas en unos 130 ms.
- Test del formato en la ESP32: el C++ coincide byte a byte con el Python.

Pendiente:

- **Probar el puente y la pantalla en Windows y macOS.** El código evita lo
  específico de Linux (el puerto se detecta solo y los errores del lector no
  dependen del idioma del sistema), pero solo se ha probado en Fedora 42.
- **Fase 4, sincronización por WiFi:** subir `eventos.txt` a `/api/sync/` y
  guardar la lista negra, las tarifas y las recargas que devuelve el servidor.
  Requiere una red de 2,4 GHz sin aislamiento entre dispositivos (por ejemplo,
  el punto de acceso de un móvil). Mientras tanto, la ESP32 no conoce los
  bloqueos hechos solo en el servidor ni las recargas remotas.
- **Lector PN532** conectado a la ESP32 (implementar `LectorPN532` sobre la
  interfaz `LectorNFC`), con lo que el ordenador deja de hacer falta en el bus y
  el cobro bajaría de los 470 ms actuales.
- Buzzer y LEDs para indicar el resultado sin mirar la pantalla.
