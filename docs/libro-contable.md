# Libro mayor contable de SubePe

Referencia para desarrollar y mantener todo lo que mueve dinero en el servidor
(módulo M10). Explica las cuentas, la convención de signos, el asiento de cada
operación con un ejemplo numérico y las reglas que el código hace cumplir.

Código: `servidor_api/libro/` (modelos, API en `libro/libro.py`, comando
`verificar_libro`) y `servidor_api/transporte/servicios.py`
(`registrar_movimiento`, que asienta cada movimiento con dinero).

## 1. Idea general

Cada céntimo que entra al sistema, se mueve o sale de él queda registrado en un
**asiento** de doble partida: un asiento tiene dos o más **partidas** (cuenta y
monto) y sus montos **suman cero**. Así, el dinero nunca aparece ni desaparece:
si el saldo de un cliente sube, otra cuenta registra de dónde vino.

- Los montos son **enteros en céntimos de sol** (S/ 1.30 = `130`). Nunca
  decimales.
- Asientos y partidas son **inmutables**: no se modifican ni se borran (lo
  impiden el ORM y triggers de PostgreSQL). Un error se corrige con un asiento
  de **reverso**.
- `Cuenta.saldo` sigue existiendo como **caché** del saldo del cliente (es lo que
  leen el panel, la API y la sincronización). Se actualiza en la **misma
  transacción** que el asiento, y `verificar_libro` comprueba cada noche que
  coincide con el libro.

## 2. Convención de signos

| Monto | Lado | Significa |
|---|---|---|
| `> 0` | **Debe** | entra a la cuenta (una caja recibe dinero, la deuda con el cliente baja) |
| `< 0` | **Haber** | sale de la cuenta o nace una obligación (SubePe le debe más al cliente, gana un pasaje) |
| `= 0` | — | prohibido (la base de datos lo rechaza) |

Regla de oro: **en cada asiento, debe = haber**, es decir, la suma de todas sus
partidas es `0`.

## 3. Cuentas contables

| Código | Naturaleza | Qué representa | Saldo normal |
|---|---|---|---|
| `CAJA_PASARELA` | activo | Dinero cobrado por medios digitales: recargas web y Yape | deudor (`+`) |
| `CAJA_VENTANILLA` | activo | Efectivo cobrado en ventanilla y puntos de recarga, incluido el saldo inicial al emitir | deudor (`+`) |
| `INGRESOS_PASAJES` | ingreso | Lo que SubePe gana por los viajes cobrados | acreedor (`−`) |
| `AJUSTES` | activo | Contrapartida de los saldos de apertura y de las correcciones manuales de operadores | según el caso |
| `SALDO_CLIENTE_<id>` | pasivo | Lo que SubePe le debe al cliente `<id>`: su saldo | acreedor (`−`) |

Hay una `SALDO_CLIENTE_<id>` por cada `Cuenta` de cliente: se crea sola al crear
la cuenta (en la misma transacción).

**Saldo de un cliente** = el negativo de la suma de las partidas de su
`SALDO_CLIENTE_<id>` (`libro.saldo_cliente(cuenta)`). Una partida de `−1000` en
esa cuenta significa que el cliente tiene S/ 10.00 más.

## 4. Tabla de asientos

| Operación | Tipo de asiento | Ref | Debe (`+`) | Haber (`−`) | Dónde se registra |
|---|---|---|---|---|---|
| Recarga web (o Yape) | `recarga` | `R<id de recarga>` | `CAJA_PASARELA` | `SALDO_CLIENTE_<id>` | `servicios.crear_recarga(..., "web")` |
| Recarga en punto o ventanilla | `recarga` | `R<id de recarga>` | `CAJA_VENTANILLA` | `SALDO_CLIENTE_<id>` | `servicios.crear_recarga(..., "punto")` |
| Viaje | `viaje` | `V<validador>-<evento>` | `SALDO_CLIENTE_<id>` | `INGRESOS_PASAJES` | `servicios.procesar_evento` |
| Emisión de tarjeta con saldo inicial | `recarga` | `R<id de recarga>` | `CAJA_VENTANILLA` | `SALDO_CLIENTE_<id>` | `servicios.crear_recarga(..., "emision")` |
| Reembolso de una recarga no aplicada | `reverso` | `rev-R<id de recarga>` | `SALDO_CLIENTE_<id>` | la caja de la recarga original | `libro.revertir` (M11-08) |
| Ajuste (abono) | `ajuste` | la que fije el operador | `AJUSTES` | `SALDO_CLIENTE_<id>` | `libro.registrar` |
| Ajuste (cargo) | `ajuste` | la que fije el operador | `SALDO_CLIENTE_<id>` | `AJUSTES` | `libro.registrar` |
| Saldo de apertura | `apertura` | `apertura-<id de cuenta>` | `AJUSTES` | `SALDO_CLIENTE_<id>` | migración `libro/0003_apertura` |

Origen de una recarga → caja (`servicios.CAJA_DE_ORIGEN`): `web` y `yape` →
`CAJA_PASARELA`; `punto`, `ventanilla` y `emision` → `CAJA_VENTANILLA`. Un
origen que no esté en esa tabla se rechaza antes de tocar nada.

## 5. Ejemplos numéricos

Un día en el sistema con dos clientes: Ana (cuenta 7) y Beto (cuenta 8). Cada
asiento muestra el debe y el haber por separado, y la comprobación de que suman
lo mismo.

### 5.1 Saldo de apertura

Ana ya tenía S/ 2.40 antes de que existiera el libro. La migración de apertura
lo registra una sola vez.

Asiento `apertura-7` (tipo `apertura`):

| Cuenta | Debe | Haber | Partida |
|---|---:|---:|---:|
| `AJUSTES` | 240 | | `+240` |
| `SALDO_CLIENTE_7` | | 240 | `−240` |
| **Total** | **240** | **240** | **0** ✓ |

Saldo de Ana: S/ 2.40.

### 5.2 Recarga web

Ana recarga S/ 10.00 por la web (recarga 15).

Asiento `R15` (tipo `recarga`):

| Cuenta | Debe | Haber | Partida |
|---|---:|---:|---:|
| `CAJA_PASARELA` | 1000 | | `+1000` |
| `SALDO_CLIENTE_7` | | 1000 | `−1000` |
| **Total** | **1000** | **1000** | **0** ✓ |

Saldo de Ana: 2.40 + 10.00 = S/ 12.40.

### 5.3 Recarga en punto

Ana recarga S/ 5.00 en efectivo en un punto de recarga (recarga 16).

Asiento `R16` (tipo `recarga`):

| Cuenta | Debe | Haber | Partida |
|---|---:|---:|---:|
| `CAJA_VENTANILLA` | 500 | | `+500` |
| `SALDO_CLIENTE_7` | | 500 | `−500` |
| **Total** | **500** | **500** | **0** ✓ |

Saldo de Ana: 12.40 + 5.00 = S/ 17.40.

### 5.4 Viaje

Ana paga un pasaje general de S/ 1.30 en el bus del validador 101 (evento 57).

Asiento `V101-57` (tipo `viaje`):

| Cuenta | Debe | Haber | Partida |
|---|---:|---:|---:|
| `SALDO_CLIENTE_7` | 130 | | `+130` |
| `INGRESOS_PASAJES` | | 130 | `−130` |
| **Total** | **130** | **130** | **0** ✓ |

Saldo de Ana: 17.40 − 1.30 = S/ 16.10.

El viaje se asienta **siempre**, aunque deje el saldo en negativo (uso sin
conexión de una tarjeta bloqueada, clon o copia): el pasajero ya viajó. El
saldo negativo del cliente queda en el libro igual que en `Cuenta.saldo`.

### 5.5 Emisión de tarjeta con saldo inicial

Beto saca su tarjeta en ventanilla y paga S/ 3.00 de saldo inicial. El saldo
inicial se registra como una recarga de origen `emision` (recarga 17) antes de
grabar la tarjeta; la confirmación de la emisión es un movimiento de S/ 0 y no
genera asiento.

Asiento `R17` (tipo `recarga`):

| Cuenta | Debe | Haber | Partida |
|---|---:|---:|---:|
| `CAJA_VENTANILLA` | 300 | | `+300` |
| `SALDO_CLIENTE_8` | | 300 | `−300` |
| **Total** | **300** | **300** | **0** ✓ |

Saldo de Beto: S/ 3.00.

El tipo de asiento `emision` existe para cuando el movimiento de emisión cobre
un monto propio; hoy no se usa.

### 5.6 Reembolso

Beto recarga S/ 20.00 por la web (recarga 18) y pide que se le devuelva antes de
que la recarga llegue a su tarjeta. Primero, la recarga:

Asiento `R18` (tipo `recarga`):

| Cuenta | Debe | Haber | Partida |
|---|---:|---:|---:|
| `CAJA_PASARELA` | 2000 | | `+2000` |
| `SALDO_CLIENTE_8` | | 2000 | `−2000` |
| **Total** | **2000** | **2000** | **0** ✓ |

El reembolso no borra `R18`: registra su espejo con `libro.revertir(asiento,
motivo)`.

Asiento `rev-R18` (tipo `reverso`, descripción «Reverso de R18: <motivo>»):

| Cuenta | Debe | Haber | Partida |
|---|---:|---:|---:|
| `SALDO_CLIENTE_8` | 2000 | | `+2000` |
| `CAJA_PASARELA` | | 2000 | `−2000` |
| **Total** | **2000** | **2000** | **0** ✓ |

Saldo de Beto: 3.00 + 20.00 − 20.00 = S/ 3.00. `CAJA_PASARELA` también vuelve a
quedar como antes de `R18`.

Si la recarga ya llegó a la tarjeta, no se reembolsa (M11-08 responde «requiere
ajuste manual»): se corrige con un ajuste.

### 5.7 Ajuste

Un operador abona S/ 1.30 a Ana como compensación autorizada (por ejemplo, un
validador con falla que le cobró un pasaje que no corresponde y no se puede
identificar un único asiento que revertir).

Asiento `ajuste-2026-11-16-7` (tipo `ajuste`, `creado_por` = el operador):

| Cuenta | Debe | Haber | Partida |
|---|---:|---:|---:|
| `AJUSTES` | 130 | | `+130` |
| `SALDO_CLIENTE_7` | | 130 | `−130` |
| **Total** | **130** | **130** | **0** ✓ |

Saldo de Ana: 16.10 + 1.30 = S/ 17.40.

Un ajuste que **descuenta** saldo es el mismo asiento con los lados cambiados
(`SALDO_CLIENTE_<id>` al debe, `AJUSTES` al haber). Si el error es un asiento
concreto (un viaje cobrado dos veces con refs distintas, una recarga mal
registrada), se usa `revertir` sobre ese asiento en vez de un ajuste.

### 5.8 Balance de comprobación del día

Suma de las partidas de cada cuenta tras los ocho asientos anteriores:

| Cuenta | Partidas | Suma | Lectura |
|---|---|---:|---|
| `SALDO_CLIENTE_7` | −240 −1000 −500 +130 −130 | −1740 | Ana tiene S/ 17.40 |
| `SALDO_CLIENTE_8` | −300 −2000 +2000 | −300 | Beto tiene S/ 3.00 |
| `CAJA_PASARELA` | +1000 +2000 −2000 | +1000 | S/ 10.00 cobrados por la web |
| `CAJA_VENTANILLA` | +500 +300 | +800 | S/ 8.00 en efectivo |
| `INGRESOS_PASAJES` | −130 | −130 | S/ 1.30 ganados en pasajes |
| `AJUSTES` | +240 +130 | +370 | S/ 3.70 de aperturas y compensaciones |
| **Total del libro** | | **0** ✓ | |

Lectura: lo que hay en las cajas (S/ 18.00) más lo que se registró como ajuste
(S/ 3.70) es igual a lo que SubePe debe a sus clientes (S/ 20.40) más lo que
ganó (S/ 1.30): 18.00 + 3.70 = 20.40 + 1.30 = 21.70.

`Cuenta.saldo` de Ana debe ser 1740 y el de Beto 300: es lo que comprueba
`verificar_libro`.

## 6. Reglas que hace cumplir el código

**Todo dinero pasa por la API del libro.** Nadie crea `Asiento` ni `Partida` a
mano. Las tres funciones de `libro/libro.py`:

| Función | Uso |
|---|---|
| `registrar(tipo, ref, partidas, fecha=None, descripcion="", creado_por=None)` | Registra un asiento; `partidas = [("CAJA_PASARELA", 1000), ("SALDO_CLIENTE_7", -1000)]` |
| `saldo_cliente(cuenta)` | Saldo del cliente según el libro, en céntimos |
| `revertir(asiento, motivo, creado_por=None)` | Asiento espejo con ref `rev-<ref>` |

`registrar` rechaza sin escribir nada:

- menos de dos partidas, un monto `0` o que no sea un entero de céntimos
  (`1.3`, `True`), un tipo desconocido, una ref vacía o de más de 80
  caracteres, una cuenta inexistente → `ErrorLibro`;
- partidas que no suman cero → `AsientoDesbalanceado`.

**Idempotencia por ref.** La ref identifica la operación de origen. Registrar
otra vez la misma ref con el mismo tipo y las mismas partidas (en cualquier
orden) devuelve el asiento existente: un validador que reenvía un viaje o una
pasarela que repite un aviso no cobran dos veces. La misma ref con otro tipo u
otras partidas es `ConflictoAsiento`.

| Origen | Ref |
|---|---|
| Recarga | `R<id de Recarga>` |
| Viaje | `V<validador>-<id del evento>` |
| Movimiento sin ref propia | `mov-<id de Movimiento>` |
| Reverso | `rev-<ref original>` |
| Apertura | `apertura-<id de Cuenta>` |

**Reversos.** `revertir` es idempotente (revertir dos veces devuelve el mismo
reverso), exige un motivo y no revierte un reverso: si hacía falta la
operación, se registra de nuevo con otra ref.

**Saldo y libro en la misma transacción.** `registrar_movimiento` actualiza
`Cuenta.saldo`, crea el `Movimiento` y registra el asiento en una sola
transacción: si cualquiera de los tres falla, no queda nada. Los movimientos
de S/ 0 no se asientan.

> **Al implementar algo nuevo que mueva dinero** (reembolsos de M11-08,
> ajustes desde el panel): `libro.registrar` y `libro.revertir` solo escriben el
> libro, **no tocan `Cuenta.saldo`**. Quien los llame debe actualizar la caché
> en la misma transacción, con la cuenta bloqueada (`select_for_update`). Si
> no, `verificar_libro` lo marcará como descuadre esa misma noche.

## 7. Verificación diaria

`manage.py verificar_libro` comprueba que:

1. la suma de todas las partidas del libro es `0`, y
2. `Cuenta.saldo` coincide con `saldo_cliente()` en cada cliente.

Cada diferencia crea una alerta **«descuadre contable»** en el panel y el
comando sale con código 1. En la VM corre cada día a las 03:30 de Perú, después
del respaldo (ver «Verificación del libro mayor» en el README).

Un descuadre significa que algo cambió `Cuenta.saldo` o el libro por fuera de
las reglas de arriba (SQL a mano, código nuevo que olvidó una de las dos
partes). No se corrige editando: se busca la causa y se registra el ajuste o el
reverso que corresponda, con su motivo.
