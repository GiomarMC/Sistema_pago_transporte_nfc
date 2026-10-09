# Sistema de pago de transporte urbano con tarjetas NFC

Proyecto del curso de IoT. Simula el sistema de pago de un transporte urbano: cada
pasajero tiene una tarjeta NFC personal con su saldo; al subir al bus acerca la
tarjeta a un validador, que descuenta el pasaje en unos 150 ms aunque el bus no
tenga conexión a internet. Un servidor central guarda las cuentas, recibe los
viajes cuando los buses se conectan, gestiona las recargas (en ventanilla o
remotas, tipo Yape) y detecta tarjetas robadas, clonadas o manipuladas.

Para repartir el trabajo de escalabilidad entre cinco o seis personas, ver el
[plan de tareas y criterios de entrega](docs/plan-escalabilidad.md).

Contenido:

1. [Qué hace el sistema](#1-qué-hace-el-sistema)
2. [Arquitectura](#2-arquitectura)
3. [Flujo completo](#3-flujo-completo)
4. [Datos en la tarjeta](#4-datos-en-la-tarjeta)
5. [Seguridad](#5-seguridad)
6. [Estructura del repositorio](#6-estructura-del-repositorio)
7. [Instalación](#7-instalación)
8. [Uso](#8-uso)
9. [API del servidor](#9-api-del-servidor)
10. [Panel de administración](#10-panel-de-administración)
11. [Tests](#11-tests)
12. [Solución de problemas](#12-solución-de-problemas)
13. [Estado del proyecto](#13-estado-del-proyecto)

---

## 1. Qué hace el sistema

| Función | Descripción |
|---|---|
| Emisión de tarjetas | Se crea la cuenta del cliente (nombre, DNI, tarifa) y se graba su tarjeta NFC con los datos firmados y protegida con contraseña. |
| Cobro del pasaje | El validador del bus lee la tarjeta, comprueba que es auténtica y no está bloqueada, descuenta la tarifa (general S/ 1.30, estudiante S/ 0.80) y graba el nuevo saldo. |
| Funcionamiento sin conexión | El validador decide solo; guarda los viajes en una cola local y los sube al servidor cuando hay red. |
| Recargas | Remotas (Yape, web): el saldo llega a la tarjeta en el siguiente viaje. En punto de recarga: se graba en el momento. |
| Robo o pérdida | Se bloquea la tarjeta; los buses la rechazan tras sincronizar y graban el bloqueo en la propia tarjeta. El saldo se recupera en una tarjeta nueva. |
| Detección de fraude | Detecta saldos alterados, copias restauradas de una tarjeta y clones, y bloquea la tarjeta automáticamente. |

La fuente de verdad del saldo es el **servidor**. La tarjeta lleva una copia
firmada del saldo para poder cobrar sin conexión.

## 2. Arquitectura

```
                    +-------------------------------------------+
                    |  SERVIDOR (Docker)                        |
                    |  Django + Django REST Framework           |
                    |  PostgreSQL                               |
                    |  - cuentas, tarjetas, recargas            |
                    |  - movimientos (viajes, recargas)         |
                    |  - lista negra, alertas de fraude         |
                    |  - panel de administración web            |
                    +-------------------------------------------+
                        ^                ^                 ^
              HTTP/JSON |                | HTTP/JSON       | HTTP/JSON
        (cuando hay red)|                |                 |
    +-------------------+--+   +---------+----------+   +--+-------------------+
    | VALIDADOR (bus)      |   | EMISIÓN / RECARGA  |   | RECARGA REMOTA       |
    | ACR122U + Python     |   | ACR122U + Python   |   | (Yape, web, app)     |
    | SQLite local:        |   | PC de atención     |   | solo llamada a la API|
    |  cola de viajes      |   +--------------------+   +----------------------+
    |  lista negra         |
    |  recargas pendientes |
    |  tarifas             |
    +----------------------+
              ^
              | NFC (13,56 MHz)
    +----------------------+
    | TARJETA NTAG215      |
    | datos firmados       |
    | contraseña, contador |
    +----------------------+
```

| Componente | Carpeta | Tecnología |
|---|---|---|
| Servidor central | `servidor_api/` | Python 3.13, Django 6.1, Django REST Framework, PostgreSQL 17, Docker |
| Programas del lector (validador, emisión, recarga) | `transporte/` | Python 3.9+, pyscard (PC/SC), SQLite |
| Validador de bus (dispositivo, opcional) | `pantalla_esp32/` | ESP32 en C++ (PlatformIO), pantalla LCD SSD1283A, microSD |
| Lector NFC | - | ACS ACR122U (USB, chip NXP PN532) |
| Tarjetas | - | NXP NTAG215 (pruebas). Previsto: NTAG 424 DNA |

## 3. Flujo completo

### 3.1 Emisión de una tarjeta

```
Operador                     Programa de emisión                 Servidor
   |  datos del cliente  -->  |                                    |
   |                          |-- ¿es NTAG215? ¿ya emitida? ------>| (en la tarjeta)
   |                          |-- POST /api/emisiones/ ----------->| crea cuenta, reserva n.º de tarjeta
   |                          |<-- n.º tarjeta, saldo, tarifa -----|
   |                          |-- graba cabecera y saldo firmados  |
   |                          |-- activa contraseña y contador NFC |
   |                          |-- relee y verifica                 |
   |                          |-- POST .../confirmar/ ------------>| activa la tarjeta; bloquea las anteriores
   |                          |   (si algo falla: .../cancelar/)   |
```

Una tarjeta de reemplazo (robo o pérdida) se emite para la misma cuenta y nace
con el saldo real de la cuenta, incluidas las recargas que no habían llegado a
la tarjeta anterior.

### 3.2 Viaje: cobro en el bus

Todo ocurre en el validador, sin consultar al servidor (unos 150 ms en total):

1. Autentica la tarjeta con su contraseña. La tarjeta responde con un código
   (PACK) que demuestra que fue emitida por el sistema.
2. Lee los datos y verifica sus firmas. Si no cuadran, la tarjeta fue alterada:
   se rechaza y se reporta como fraude.
3. Comprueba que no está bloqueada (en la propia tarjeta y en la lista negra
   local). Si está en la lista negra, graba el bloqueo en la tarjeta.
4. Comprueba que el número de operación no retrocede (detecta copias antiguas
   restauradas).
5. Si el mismo validador la cobró hace menos de 60 s, responde "ya pagado" y no
   cobra otra vez.
6. Suma las recargas remotas pendientes de esa cuenta, si las hay.
7. Si el saldo alcanza, descuenta la tarifa y graba el nuevo saldo en la copia
   libre de la tarjeta (ver 4). Relee para verificar la escritura.
8. Guarda el viaje en la cola local para subirlo al servidor.

### 3.3 Recargas

| Tipo | Qué pasa |
|---|---|
| Remota (Yape, web) | El servidor abona el monto a la cuenta al instante. Los buses reciben la recarga pendiente al sincronizar y la graban en la tarjeta en el siguiente viaje. |
| En punto de recarga | Con la tarjeta en el lector: el servidor registra la recarga y el programa la graba al momento (incluye también las remotas pendientes). Si se retira la tarjeta antes de terminar, la recarga no se pierde: queda pendiente. |

Cada recarga de una cuenta tiene un número correlativo (1, 2, 3...) y la
tarjeta guarda el número de la última que recibió. Así ningún bus aplica dos
veces la misma recarga, aunque varios la tengan pendiente.

### 3.4 Sincronización (buses sin conexión permanente)

El validador nunca depende de la red para cobrar. Cuando hay conexión (al
arrancar, cada 30 s y al cerrar) hace una sola petición:

```
Validador -> Servidor:  viajes e incidencias pendientes de subir
Servidor -> Validador:  qué eventos se aceptaron
                        lista negra actual
                        recargas pendientes de entregar
                        tarifas vigentes
                        hora del servidor (para detectar relojes desajustados)
```

- Si la red se corta a mitad, el validador reenvía todo y el servidor ignora
  los eventos que ya tenía (cada evento tiene un identificador único).
- El servidor registra todos los viajes, incluso los de tarjetas bloqueadas que
  un bus desactualizado dejó pasar: el pasajero ya viajó, y el saldo real de la
  cuenta queda correcto.

### 3.5 Robo o pérdida

1. El cliente lo reporta: se bloquea la tarjeta (programa, API o panel web).
2. Los buses reciben la lista negra en su siguiente sincronización.
3. El primer bus actualizado que ve la tarjeta la rechaza **y graba "bloqueada"
   en la tarjeta**. A partir de ahí la rechazan todos los buses, incluso los que
   aún no se han sincronizado.
4. Se emite una tarjeta nueva para la misma cuenta: conserva el saldo.

### 3.6 Detección de fraude

| Ataque | Cómo se detecta | Respuesta |
|---|---|---|
| Modificar el saldo en la tarjeta | La firma HMAC del registro no coincide | Rechazo en el bus, alerta |
| Copiar los datos a otra tarjeta | La firma incluye el UID de la tarjeta original | Rechazo en el bus |
| Restaurar una copia antigua (recuperar saldo gastado) | El bus que ya vio la tarjeta detecta que el n.º de operación retrocede; el servidor detecta el mismo n.º de operación o contador NFC usado dos veces | Bloqueo automático de la tarjeta |
| Clonar la tarjeta | Dos usos con el mismo n.º de operación o contador NFC | Bloqueo automático |
| Usar una tarjeta robada en un bus sin red | El servidor lo detecta al sincronizar y cobra el viaje a la cuenta | Alerta "uso de tarjeta no activa" |
| Validador falso que sube viajes | Cada validador se autentica con su propio token | Petición rechazada (401/403) |

## 4. Datos en la tarjeta

Formato versión 2, en tarjetas NTAG215 (páginas de 4 bytes; enteros big-endian):

| Páginas | Contenido |
|---|---|
| 0-3 | UID y configuración de fábrica |
| 4-7 | Registro NDEF vacío (lo que ve un móvil) |
| 8 | `T` `R`, versión del formato, estado (1 activa, 2 bloqueada) |
| 9 | N.º de tarjeta (uint32) |
| 10 | N.º de cuenta (uint32) |
| 11 | Tarifa, reservado, fecha de emisión (días desde 2020-01-01) |
| 12-13 | Firma de la cabecera (HMAC-SHA256 truncado a 8 bytes) |
| 16-20 | Registro A: saldo en céntimos, n.º de operación, fecha y hora, validador, última recarga, firma |
| 21-25 | Registro B: igual que A |
| 131-134 | Configuración: contraseña, protección desde la página 8, contador NFC |

**Dos copias del saldo.** Cada cambio se graba en la copia que no está vigente;
la vigente es la de mayor n.º de operación con firma válida. Si se retira la
tarjeta a mitad de la escritura, la copia anterior sigue intacta: no se pierde
ni se inventa saldo.

Los datos personales (nombre, DNI) **no** se guardan en la tarjeta: solo el
n.º de cuenta. El resto está en el servidor.

## 5. Seguridad

- **Clave maestra** (`transporte/claves/clave_maestra.bin`, 32 bytes aleatorios).
  De ella se derivan, para cada tarjeta y a partir de su UID, una clave de firma
  y una contraseña propias. Descubrir la contraseña de una tarjeta no compromete
  a las demás.
- **El servidor no conoce la clave maestra.** Solo la tienen los equipos con
  lector (validadores, emisión, recarga).
- **Tokens de API**: uno por validador y uno por operador. El n.º de validador
  se toma del token, no de los datos enviados.
- **Limitaciones de NTAG215**: su contraseña de 32 bits viaja sin cifrar por
  radio, y las tarjetas de prueba no son NXP originales (su firma de
  originalidad es todo ceros). Por eso la seguridad se apoya en el diseño
  (firmas, contadores, detección en servidor) y no en el chip. La versión final
  usará **NTAG 424 DNA** (AES-128); el formato de datos está separado del acceso
  al chip (`transporte/tarjeta/`) para que el cambio afecte solo a ese módulo.

## 6. Estructura del repositorio

```
.
|-- README.md
|-- servidor_api/                  Servidor central
|   |-- docker-compose.yml         Servidor + PostgreSQL
|   |-- Dockerfile
|   |-- .env.example               Plantilla de configuración
|   |-- requirements.txt
|   |-- config/                    Configuración de Django
|   `-- transporte/                Aplicación
|       |-- models.py              Cuentas, tarjetas, recargas, movimientos, alertas, validadores
|       |-- servicios.py           Lógica de negocio (sincronización, fraude, recargas, emisión)
|       |-- views.py, urls.py      API REST
|       |-- admin.py               Panel de administración
|       |-- management/commands/  crear_validador, crear_operador, config_cliente, importar_sqlite
|       `-- tests/                 Tests de la API
|-- pantalla_esp32/                Validador de bus con ESP32, pantalla y microSD (ver su README)
|-- docs/                          Informe de conexiones y pruebas del dispositivo (PDF)
`-- transporte/                    Programas que usan el lector NFC
    |-- validador.py               Validador del bus
    |-- emitir_tarjeta.py          Emisión de tarjetas
    |-- recargar.py                Recargas (remota y en punto)
    |-- leer_tarjeta.py            Consulta de una tarjeta
    |-- bloquear_tarjeta.py        Bloqueo por robo o pérdida
    |-- restablecer_tarjeta.py     Deja una tarjeta de pruebas como de fábrica
    |-- detectar_ultralight.py     Identifica el tipo de tarjeta y muestra su memoria
    |-- validador_local.py         Base de datos local del validador y sincronización
    |-- pantalla.py                Envío de resultados a la pantalla ESP32 (modo A)
    |-- puente_nfc.py              Presta el ACR122U al validador de la ESP32 (modo B)
    |-- api_cliente.py             Cliente HTTP de la API
    |-- config.py                  Tarifas por defecto, formato de montos
    |-- api.json.example           Plantilla de URL y tokens
    `-- tarjeta/
        |-- formato.py             Formato de datos y firmas (independiente del chip)
        |-- claves.py              Clave maestra y claves derivadas
        `-- ntag215.py             Comunicación con NTAG215 a través del ACR122U
```

## 7. Instalación

El sistema tiene dos partes que se instalan por separado:

- **Servidor**: se ejecuta con Docker. Funciona igual en Windows, macOS y Linux.
- **Programas del lector**: se ejecutan directamente en el equipo al que se
  conecta el ACR122U (Docker no puede acceder de forma fiable a lectores USB en
  Windows y macOS).

Para probar todo en un solo equipo, instala ambas partes en él.

### 7.1 Requisitos

| Para | Necesitas |
|---|---|
| Servidor | Docker Desktop (Windows, macOS) o Docker Engine con el plugin compose (Linux) |
| Programas del lector | Python 3.9 o superior, un lector ACR122U y el servicio PC/SC del sistema |
| Descargar el proyecto | Git |

### 7.2 Descargar el proyecto

```bash
git clone <URL_DEL_REPOSITORIO> transporte-nfc
cd transporte-nfc
```

### 7.3 Servidor (Windows, macOS y Linux)

1. Instala Docker:
   - Windows y macOS: [Docker Desktop](https://www.docker.com/products/docker-desktop/). Ábrelo y espera a que indique que está en marcha.
   - Linux: Docker Engine y el plugin compose ([guía oficial](https://docs.docker.com/engine/install/)). Para usarlo sin `sudo`: `sudo usermod -aG docker $USER` y vuelve a iniciar sesión.

2. Crea la configuración a partir de la plantilla:

   ```bash
   cd servidor_api
   cp .env.example .env          # Windows (PowerShell): Copy-Item .env.example .env
   ```

   Edita `.env` y cambia los dos valores `CAMBIAR`:
   - `DJANGO_SECRET_KEY`: genera uno con `python -c "import secrets; print(secrets.token_urlsafe(50))"` (en Linux o macOS, `python3`)
   - `POSTGRES_PASSWORD`: cualquier contraseña larga.

   Si el puerto 8000 está ocupado, cambia `WEB_PORT`.

3. Construye y arranca:

   ```bash
   docker compose up -d --build
   ```

   La primera vez tarda unos minutos. Comprueba que está en marcha:

   ```bash
   docker compose ps          # "db" debe aparecer como healthy y "web" como Up
   docker compose logs web    # debe terminar en "Listening at: http://0.0.0.0:8000"
   ```

4. Crea un administrador para el panel web:

   ```bash
   docker compose exec web python manage.py createsuperuser
   ```

5. Crea el operador y los validadores, y obtén la configuración para los programas del lector:

   ```bash
   docker compose exec web python manage.py config_cliente --url http://127.0.0.1:8000 --validadores 101 102
   ```

   Muestra un JSON con la URL y los tokens. Cópialo en `transporte/api.json`
   (junto a `api.json.example`). Si los programas del lector van a correr en otro
   equipo, usa en `--url` la IP del servidor en la red local (ver 7.6). El
   comando se puede repetir: no crea duplicados y muestra los mismos tokens.

Comandos útiles:

```bash
docker compose stop                  # detener (los datos se conservan)
docker compose start                 # volver a arrancar
docker compose down                  # eliminar contenedores (los datos se conservan)
docker compose down -v               # eliminar contenedores Y datos
docker compose up -d --build         # aplicar cambios del código
```

### 7.4 Programas del lector

#### Linux

1. Instala el servicio PC/SC, el driver CCID y pyscard:

   ```bash
   # Fedora
   sudo dnf install pcsc-lite pcsc-lite-ccid pcsc-tools python3-pyscard
   # Ubuntu / Debian
   sudo apt install pcscd libccid pcsc-tools python3-pyscard
   # Arch
   sudo pacman -S pcsclite ccid pcsc-tools python-pyscard

   sudo systemctl enable --now pcscd.socket
   ```

2. Desactiva el driver NFC del kernel. Linux carga por defecto `pn533_usb`, que
   se queda con el ACR122U e impide que PC/SC lo vea:

   ```bash
   printf 'blacklist pn533_usb\nblacklist pn533\nblacklist nfc\n' | sudo tee /etc/modprobe.d/blacklist-acr122u.conf
   sudo modprobe -r pn533_usb pn533 nfc
   sudo systemctl restart pcscd
   ```

   Desconecta y vuelve a conectar el lector. Si `modprobe -r` dice que el módulo
   está en uso, reinicia el equipo.

3. Comprueba el lector con `pcsc_scan`: debe aparecer
   `ACS ACR122U PICC Interface`. Al acercar una tarjeta se muestra su ATR. Sal
   con `Ctrl+C`.

#### Windows 10 / 11

1. Instala Python 3.9 o superior desde [python.org](https://www.python.org/downloads/)
   (marca "Add python.exe to PATH").
2. Instala el driver PC/SC del ACR122U desde la web de ACS
   ([acs.com.hk](https://www.acs.com.hk/), producto ACR122U, sección Downloads).
   El driver genérico de Windows detecta el lector, pero puede no aceptar los
   comandos directos que usa este proyecto.
3. Comprueba que el servicio "Tarjeta inteligente" (Smart Card) está iniciado
   (`services.msc`).
4. Instala pyscard en un entorno virtual:

   ```powershell
   cd transporte
   py -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

   En cada consola nueva, activa el entorno con `.venv\Scripts\activate` y usa
   `python` en lugar de `python3` en los comandos de este README.

#### macOS

1. Instala Python 3.9 o superior (desde [python.org](https://www.python.org/downloads/) o con Homebrew: `brew install python`).
2. macOS incluye el servicio PC/SC y un driver CCID que reconoce el ACR122U. Si
   el lector no aparece, instala el driver de macOS de la web de ACS.
3. Instala pyscard:

   ```bash
   cd transporte
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

   Si pip intenta compilar pyscard y falla, instala swig (`brew install swig`) y
   repite.

#### Comprobación (todos los sistemas)

Con una tarjeta en el lector:

```bash
cd transporte
python3 detectar_ultralight.py --una
```

Debe indicar el tipo de tarjeta (por ejemplo `NTAG215`) y mostrar su memoria.

### 7.5 Clave maestra: compartirla en el equipo

La primera vez que un programa necesita la clave maestra la crea en
`transporte/claves/clave_maestra.bin`. **Todos los equipos que leen las mismas
tarjetas deben usar el mismo archivo**: si cada uno genera la suya, las tarjetas
emitidas por un compañero aparecerán como "contraseña que no es de este
sistema".

- Elegid un equipo que genere la clave (basta con emitir la primera tarjeta) y
  pasad el archivo a los demás por un medio privado.
- **No la subáis al repositorio** (ya está excluida en `.gitignore`).
- Si se pierde, las tarjetas emitidas con ella no se pueden volver a leer ni
  reescribir con este sistema.

**Dónde colocarla al recibirla.** La carpeta `claves/` no viene en el
repositorio: créala dentro de `transporte/` y copia ahí el archivo, con este
nombre exacto:

```
transporte/
|-- claves/
|   `-- clave_maestra.bin      <- aquí (32 bytes)
|-- api.json
|-- validador.py
`-- ...
```

```bash
# Linux / macOS (desde la raíz del repositorio)
mkdir -p transporte/claves
cp /ruta/donde/la/recibiste/clave_maestra.bin transporte/claves/
chmod 600 transporte/claves/clave_maestra.bin

# Windows (PowerShell, desde la raíz del repositorio)
New-Item -ItemType Directory -Force transporte\claves
Copy-Item C:\ruta\donde\la\recibiste\clave_maestra.bin transporte\claves\
```

**Cópiala antes de usar el lector por primera vez.** Si ejecutas un programa
sin ella, se crea una clave nueva y se muestra el aviso
`[!] Clave maestra nueva creada en ...`. Si te pasa, borra ese archivo, copia
el correcto y vuelve a emitir las tarjetas que hayas grabado con la clave
equivocada. Si están protegidas con esa clave, restablécelas primero con
`restablecer_tarjeta.py` mientras la clave equivocada siga en su sitio.

### 7.6 Validadores en otros equipos de la red

1. Averigua la IP del equipo que ejecuta el servidor: `ipconfig` (Windows),
   `ip addr` (Linux) o `ipconfig getifaddr en0` (macOS).
2. Permite conexiones entrantes al puerto 8000 en su firewall.
3. En el otro equipo, pon esa IP en `transporte/api.json`:
   `"url": "http://192.168.1.50:8000"`.
4. Copia también la clave maestra (ver 7.5).

### 7.7 Validador de bus con ESP32 (opcional)

El dispositivo que iría en el bus (ESP32 con pantalla LCD y microSD) tiene su
propia guía: [`pantalla_esp32/README.md`](pantalla_esp32/README.md). Funciona de
dos formas:

- **Modo A:** `validador.py` sigue cobrando en el ordenador y la ESP32 solo
  muestra el resultado en la pantalla (`validador.py --pantalla auto`).
- **Modo B:** el validador corre en la ESP32 y el ordenador solo le presta el
  ACR122U (`puente_nfc.py`). Sirve cualquier ordenador con Linux, Windows o
  macOS como puente. La ESP32 sincroniza sola con el servidor por WiFi (también
  por HTTPS con el servidor en internet, sección 7.8).

En los dos casos el servidor no cambia: el dispositivo sustituye al validador
del ordenador, no al servidor. Las conexiones, la carga de los programas y las
pruebas de cada componente están en esa guía y en
`docs/informe-conexiones-validador-esp32.pdf`.

### 7.8 Servidor en internet

Para las pruebas del ecosistema, el servidor está en una VM de Oracle Cloud
(capa gratuita) en **https://subepe.app** (también `https://subepe.duckdns.org`).
Corre el mismo `docker-compose.yml` más `docker-compose.prod.yml`, que añade
**Caddy** con certificado HTTPS automático de Let's Encrypt.

**Despliegue automático.** Cada push a `main` que toque `servidor_api/` pasa los
tests en GitHub Actions y, si pasan, se despliega solo
(`.github/workflows/servidor.yml`). GitHub entra a la VM con una clave que solo
puede ejecutar `servidor_api/desplegar.sh`. Los cambios de los compañeros llegan
a la VM sin acceso a ella: basta con juntarlos en `main`.

**Acceso al panel.** En https://subepe.app/panel/ → «Solicitar acceso». Un
superusuario aprueba la cuenta en la sección Usuarios del panel.

**Configuración de la VM** (`servidor_api/.env`, no está en git): como
`.env.example`, más `DOMINIO=subepe.app, subepe.duckdns.org`,
`DJANGO_ALLOWED_HOSTS=subepe.app,subepe.duckdns.org` y `DJANGO_HTTPS=1`.

**Copias de seguridad.** Cada noche (03:00 de Perú), `.github/workflows/respaldo.yml`
saca una copia de la base de datos, comprueba que se puede leer, la **cifra** y
la guarda 30 días en la pestaña Actions del repositorio (ejecución «Respaldo» →
*Artifacts*). Para sacar una copia en el momento: Actions → Respaldo → *Run
workflow*. Para restaurar una:

```bash
# 1. Descifrar (pide la contraseña RESPALDO_CLAVE; la tiene quien administra el servidor)
gpg --decrypt respaldo-AAAAMMDD-HHMM.dump.gpg > respaldo.dump
# 2. Llevarla a la VM y cargarla (reemplaza los datos actuales)
scp respaldo.dump ubuntu@<IP de la VM>:
ssh ubuntu@<IP de la VM> 'cd Sistema_pago_transporte_nfc/servidor_api && bash restaurar.sh ~/respaldo.dump'
```

Secretos del repositorio (Settings → Secrets and variables → Actions):
`VM_HOST`, `VM_SSH_KEY`, `VM_KNOWN_HOSTS` (despliegue y copias) y
`RESPALDO_CLAVE` (cifrado de las copias; sin ella no se pueden recuperar, así
que también hay que guardarla fuera de GitHub). Al cambiar de VM se actualizan
`VM_HOST` y `VM_KNOWN_HOSTS`, y el registro DNS de los dominios.

## 8. Uso

Todos los comandos se ejecutan desde la carpeta `transporte/` (en Windows,
`python` en lugar de `python3`).

### Emitir tarjetas

```bash
# Cliente nuevo, con recarga inicial
python3 emitir_tarjeta.py --nombre "Ana Quispe" --dni 71234567 --tarifa estudiante --saldo 10.00

# Tarjeta de reemplazo para la cuenta 1 (conserva el saldo, bloquea la anterior)
python3 emitir_tarjeta.py --cuenta 1

# Reemitir una tarjeta que ya estaba emitida
python3 emitir_tarjeta.py --cuenta 1 --forzar
```

### Validador del bus

```bash
python3 validador.py --id 101               # con red: sincroniza al inicio y cada 30 s
python3 validador.py --id 101 --sin-red     # simula un bus sin conexión
python3 validador.py --id 101 --sincronizar # solo sincroniza y sale
```

Cada toque muestra el resultado y el tiempo:

```
10:15:02  [PASA]      pasaje estudiante S/ 0.80 (+recarga S/ 5.00)  saldo   S/ 10.20  (155 ms)
10:15:40  [RECHAZADA] tarjeta bloqueada                                             (152 ms)
```

### Recargas

```bash
python3 recargar.py --dni 71234567 --monto 5.00 --origen yape   # remota, sin tarjeta
python3 recargar.py --monto 2.50                                # en punto, con la tarjeta
python3 recargar.py --aplicar                                   # grabar recargas pendientes
```

Límites: entre S/ 1.00 y S/ 200.00 por recarga.

### Consultar, bloquear y restablecer

```bash
python3 leer_tarjeta.py                        # datos de la tarjeta y de su cuenta
python3 bloquear_tarjeta.py --tarjeta 2        # bloquear una tarjeta
python3 bloquear_tarjeta.py --dni 71234567     # bloquear todas las de un cliente
python3 restablecer_tarjeta.py                 # dejar una tarjeta de pruebas como de fábrica
python3 detectar_ultralight.py --resumen       # identificar muchas tarjetas seguidas
```

### Ejemplo de prueba completa

```bash
python3 emitir_tarjeta.py --nombre "Prueba" --dni 12345678 --tarifa general --saldo 5.00
python3 validador.py --id 101 --sin-red        # acercar la tarjeta: cobra S/ 1.30. Ctrl+C
python3 recargar.py --dni 12345678 --monto 3.00 --origen yape
python3 validador.py --id 102                  # sincroniza, cobra y aplica la recarga. Ctrl+C
python3 bloquear_tarjeta.py --dni 12345678
python3 validador.py --id 101                  # sincroniza: la rechaza y graba el bloqueo. Ctrl+C
python3 leer_tarjeta.py                        # la tarjeta indica "bloqueada"
python3 emitir_tarjeta.py --cuenta <N> --forzar  # recuperarla con el saldo de la cuenta
```

## 9. API del servidor

Todas las rutas empiezan por `/api/` y requieren la cabecera
`Authorization: Token <token>`. Montos en céntimos de sol.

| Método y ruta | Quién | Para qué |
|---|---|---|
| `POST /api/sync/` | Validador | Sube eventos; recibe lista negra, recargas pendientes y tarifas |
| `POST /api/sync/v2/` | Validador | Sube hasta 40 eventos y descarga cambios por cursor, en páginas de hasta 100 |
| `POST /api/emisiones/` | Operador | Reserva un n.º de tarjeta (y crea la cuenta si es nueva) |
| `POST /api/emisiones/{id}/confirmar/` | Operador | Activa la tarjeta tras grabarla |
| `POST /api/emisiones/{id}/cancelar/` | Operador | Libera la reserva si la grabación falló |
| `GET /api/tarjetas/{id}/` | Operador | Estado de la tarjeta, cuenta y recargas pendientes |
| `POST /api/tarjetas/{id}/bloquear/` | Operador | Bloqueo por robo o pérdida |
| `POST /api/tarjetas/uid/{uid}/anular/` | Operador | Anula las emisiones de una tarjeta restablecida |
| `GET /api/cuentas/?documento=...` | Operador | Busca una cuenta por DNI |
| `GET /api/cuentas/{id}/` | Operador | Cuenta, tarjetas y últimos movimientos |
| `POST /api/cuentas/{id}/bloquear/` | Operador | Bloquea todas las tarjetas activas de la cuenta |
| `POST /api/recargas/` | Operador | Recarga remota (`documento`) o en punto (`tarjeta`, `uid`, `recarga_en_tarjeta`) |
| `POST /api/recargas/entregas/` | Operador | Confirma que el punto de recarga grabó la tarjeta |

Ejemplo de sincronización:

```json
POST /api/sync/
{
  "eventos": [
    {"id": 57, "tipo": "viaje", "fecha": "2026-09-30T17:38:05+00:00",
     "tarjeta_id": 4, "uid": "04B3E121CA2A81", "monto": 80, "saldo_final": 1020,
     "operacion": 2, "contador": 19, "recarga_hasta": 1}
  ]
}

Respuesta:
{
  "validador": 101,
  "aceptados": [57],
  "lista_negra": [1, 2, 3],
  "recargas": [{"cuenta_id": 7, "seq": 3, "monto": 500}],
  "tarifas": {"1": ["general", 130], "2": ["estudiante", 80]},
  "hora_servidor": "2026-09-30T17:38:50+00:00"
}
```

Tipos de evento: `viaje`, `incidencia` (se registra como alerta) y `fraude` (se
registra y bloquea la tarjeta).

El [contrato de sincronización v2](docs/contrato-sync-v2.md) explica los
cursores, las páginas, el arranque inicial y la transición desde v1. La ESP32
y el validador Python aún usan v1 hasta que se adapten en T2.

Comandos de gestión (con Docker, anteponer `docker compose exec web`):

```bash
python manage.py crear_validador 103 --descripcion "Bus ruta 5"   # muestra su token
python manage.py crear_operador ventanilla2                       # muestra su token
python manage.py config_cliente --url http://IP:8000 --validadores 101 102
python manage.py importar_sqlite ruta/transporte.db               # datos de la versión antigua
```

## 10. Panel de administración

En `http://localhost:8000/admin/`, con el usuario creado en el paso 4 de la sección 7.3:

- **Cuentas**: saldo (solo lectura: cambia con movimientos), tarjetas y movimientos de cada cliente.
- **Tarjetas**: estado; acción "Bloquear (robo o pérdida)".
- **Recargas**: filtro por pendientes o entregadas.
- **Movimientos**: historial completo de viajes y recargas.
- **Alertas**: fraudes detectados, bloqueos, usos de tarjetas bloqueadas; se marcan como revisadas.
- **Tarifas**: al cambiar un precio, los buses lo reciben en su siguiente sincronización.
- **Validadores**: última sincronización de cada bus; se pueden desactivar.

## 11. Tests

Los tests de la API cubren permisos, sincronización repetida, detección de
clones y copias, bloqueos, recargas (sin duplicados, con tarjetas bloqueadas) y
emisión (reemplazo, cancelación).

```bash
cd servidor_api
docker compose exec web python manage.py test transporte
```

## 12. Solución de problemas

| Problema | Solución |
|---|---|
| `no se encontró ningún lector` | Linux: comprueba `pcscd` y que el driver `pn533` está desactivado (7.4). Windows: instala el driver de ACS y revisa el servicio "Tarjeta inteligente". |
| `Failed to establish context: Access denied` (Linux) | PC/SC solo da acceso a la sesión activa (polkit): pasa si la pantalla está bloqueada. Para un validador que corre como servicio sin sesión, añade una regla de polkit (ver abajo). |
| `Falta .../api.json` | Crea `transporte/api.json` con el paso 5 de la sección 7.3. |
| `Token inválido` (401) | El token no existe en ese servidor (por ejemplo, tras `docker compose down -v`). Genera la configuración otra vez con `config_cliente`. |
| `la tarjeta tiene una contraseña que no es de este sistema` | La tarjeta se emitió con otra clave maestra (7.5). |
| `versión de formato no soportada: 1` | Tarjeta emitida con una versión anterior: reemítela con `emitir_tarjeta.py --cuenta N --forzar`. |
| `Sin conexión con el servidor` | Comprueba que el servidor está en marcha (`docker compose ps`), la URL de `api.json` y el firewall. El validador sigue cobrando sin red. |
| El puerto 8000 está ocupado | Cambia `WEB_PORT` en `servidor_api/.env` y la URL de `api.json`. |
| Error `set: illegal option` al arrancar el contenedor | El script se guardó con finales de línea de Windows. Vuelve a clonar el repositorio (`.gitattributes` lo evita). |

Regla de polkit para usar el lector desde un servicio (Linux), en
`/etc/polkit-1/rules.d/50-pcscd-transporte.rules` (cambia `USUARIO`):

```js
polkit.addRule(function(action, subject) {
    if ((action.id == "org.debian.pcsc-lite.access_pcsc" ||
         action.id == "org.debian.pcsc-lite.access_card") &&
        subject.user == "USUARIO") {
        return polkit.Result.YES;
    }
});
```

## 13. Estado del proyecto

Probado:

- Programas del lector con un ACR122U en Fedora 42 y tarjetas NTAG215: emisión,
  cobro (unos 150 ms por toque), recargas remotas y en punto, bloqueo y
  recuperación de tarjetas.
- Servidor en Docker (Linux): 25 tests de la API y prueba de extremo a extremo
  de los programas del lector contra el servidor.

Pendiente:

- Probar los programas del lector con el ACR122U en Windows y macOS.
- Migrar a tarjetas NTAG 424 DNA (AES-128).
- Probar la pantalla ESP32 + LCD con el hardware real (el programa compila y el
  protocolo se probó con una placa simulada).
- Validador autónomo en la ESP32 con un módulo PN532 (sin ordenador en el bus).
- HTTPS para usar el servidor fuera de la red local.
- Guardar la clave maestra en un módulo seguro (SAM) en lugar de un archivo.
