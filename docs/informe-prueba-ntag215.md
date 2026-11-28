# M1-01b — Prueba manual con una NTAG215 real (#35, parte 35b)

Comprueba con hardware que, tras la interfaz `TarjetaFisica` de #198 (35a), emitir, cobrar, recargar en el punto y bloquear una NTAG215 funcionan igual que antes.

## Equipo y versión

- Lector ACR122U por USB (`ACS ACR122U PICC Interface 00 00`), pcscd y pyscard del sistema (Python 3.14.10, Fedora).
- Tarjeta NTAG215 en blanco, UID `04269612CE2A81`.
- Código de `transporte/` igual al de develop `84ca126` (el merge de #198). `tarjeta/base.py`, `tarjeta/ntag215.py`, `tarjeta/lector.py` y los programas usados no cambian entre develop y la rama donde se ejecutó.
- Servidor de producción (`https://subepe.app`), 27 nov 2026.

## Resultados

| Paso | Comando | Resultado |
|---|---|---|
| Identificar la tarjeta en blanco | `leer_tarjeta.py` | `abrir_tarjeta()` la reconoce como NTAG215 por GET_VERSION: «la tarjeta no está emitida (sin marca 'TR')» |
| Emitir | `emitir_tarjeta.py --nombre "Prueba 35b" --dni 00000035 --tarifa general` | «Tarjeta emitida y verificada»: cuenta 6, tarjeta 6, saldo S/ 0.00, contador 1 |
| Recargar en el punto | `recargar.py --monto 5.00` | Saldo en tarjeta S/ 0.00 → S/ 5.00, cuenta S/ 5.00 |
| Cobrar | `validador.py` (validador 101) | `[PASA] pasaje general S/ 1.30, saldo S/ 3.70 (158 ms)`. La tarjeta se quedó 40 s en el lector y solo se cobró una vez. El evento se subió en la sincronización |
| Comparar con el servidor | `leer_tarjeta.py` | Registro A vigente: S/ 3.70, op 3, validador 101. Registro B: S/ 5.00, op 2, validador 900 (punto de recarga). Servidor: tarjeta activa, cuenta S/ 3.70 |
| Bloquear | `bloquear_tarjeta.py --tarjeta 6` | «Bloqueada tarjeta n.º 6» |
| Cobrar una tarjeta bloqueada | `validador.py` tras sincronizar | `[RECHAZADA] Tarjeta bloqueada (Acuda a atención) (159 ms)` |

La tarjeta 6 queda bloqueada y no vuelve a usarse.

## Mediciones pendientes de #198

**GET_VERSION.** Se midió con la tarjeta quieta en el lector:

| Medida | Mediana |
|---|---:|
| GET_VERSION repetido en una misma conexión (30 veces) | 6,9 ms (máx. 35,9 ms) |
| `NTAG215(conexion)` sin identificar | 0,4 ms |
| `abrir_tarjeta(conexion)` (incluye el primer GET_VERSION) | 36,3 ms |

El primer GET_VERSION de cada conexión es el lento. Identificar la tarjeta suma unos 36 ms a cada lectura del validador de PC, que en esta prueba tardó 158 ms por cobro.

**Contador NFC.** Se leyó con READ_CNT y la tarjeta autenticada:

| Momento | Contador |
|---|---:|
| Conexión 1, antes de leer el área | 5 |
| Conexión 1, después de leer el área | 6 |
| Conexión 2, antes / después de leer | 6 → 7 |
| Conexión 3, antes y tras 3 lecturas seguidas | 7 → 8, 8, 8 |

El contador sube una vez, con la primera lectura después de seleccionar la tarjeta, y no con las siguientes. Es el comportamiento que emula el simulador. El ACR122U vuelve a seleccionar la tarjeta en cada conexión PC/SC, así que el contador sube en cada conexión aunque la tarjeta no se mueva.

## Observación: el cliente espera 5 s al servidor

El primer intento de emisión falló con «The read operation timed out». `api_cliente.llamar` espera 5 s por omisión y la micro de Oracle a veces tarda más. El servidor sí había creado la reserva (tarjeta 5 en estado `pendiente`, con su cuenta), pero `emitir_tarjeta.py` no pudo cancelarla porque nunca recibió la respuesta. La tarjeta física no se tocó. La reserva se canceló a mano con `POST /api/emisiones/5/cancelar/`, que borró la tarjeta pendiente y la cuenta vacía.

El resto de la prueba se hizo con 30 s de espera. Para eso se usó un envoltorio local que sustituye el valor por omisión de `api_cliente.llamar` sin modificar el repositorio. Esta rama no cambia el valor de 5 s: queda para un issue aparte.
