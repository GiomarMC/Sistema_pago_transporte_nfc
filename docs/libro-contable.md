# Libro contable M10

El libro registra **céntimos enteros**. Una partida positiva es **debe**; una negativa es **haber**. Cada asiento confirmado tiene al menos dos partidas y suma cero. `Cuenta.saldo` es una caché operativa del pasivo del pasajero: `saldo_cliente(cuenta) = -sum(partidas SALDO_CLIENTE_<id>)`.

| Operación | Debe | Haber | Efecto en saldo del pasajero |
|---|---|---|---|
| Recarga web S/ 10 | CAJA_PASARELA +1000 | SALDO_CLIENTE_7 −1000 | +1000 |
| Recarga en punto S/ 5 | CAJA_VENTANILLA +500 | SALDO_CLIENTE_7 −500 | +500 |
| Viaje S/ 1.30 | SALDO_CLIENTE_7 +130 | PASAJES_POR_LIQUIDAR −130 | −130 |
| Apertura de saldo histórico S/ 8 | AJUSTES +800 | SALDO_CLIENTE_7 −800 | No modifica la caché: ya era 800 |
| Reverso del viaje | PASAJES_POR_LIQUIDAR +130 | SALDO_CLIENTE_7 −130 | +130 |
| Ajuste positivo autorizado S/ 2 | AJUSTES +200 | SALDO_CLIENTE_7 −200 | +200 |

`PASAJES_POR_LIQUIDAR` es una cuenta transitoria de obligación por pasajes cobrados. No equivale a ingresos de SUBEPE ni atribuye todavía un viaje a una empresa. La entidad/clave de liquidación por empresa se añadirá cuando exista el contrato multiempresa. `AJUSTES` es contrapartida provisional de apertura y ajustes; su tratamiento financiero definitivo requiere acuerdo contable. Un reembolso usaría un asiento compensatorio y el canal efectivo de devolución cuando ese flujo exista; M10 no implementa pagos externos.

La emisión actual crea un `Movimiento` de monto cero para registrar la tarjeta. No crea asiento monetario; la recarga de emisión, cuando existe, genera su asiento separado con `R<id>`. Esto evita dos partidas de monto cero. Los viajes offline entran al libro **al procesarse en el servidor**, no al guardarse en el validador.

## Referencias e idempotencia

`libro.libro.registrar(tipo, ref, partidas, fecha=None, descripcion="")` exige referencia única. Si ya existe con tipo y partidas idénticas, devuelve el asiento; con datos incompatibles lanza `ConflictoAsiento`. `registrar_movimiento` usa `Movimiento.ref` (`R<id>`, `V<validador>-<evento>`) o genera `mov-<id>`. Cuenta, movimiento y asiento se escriben en una transacción con bloqueo de la cuenta. `revertir(asiento, motivo)` crea `rev-<ref>` y, si afecta a un cliente, actualiza saldo y deja un movimiento compensatorio. Nunca edita el asiento original.

## Apertura y despliegue seguro

`libro.0003_integridad_y_apertura` bloquea `transporte_cuenta` durante la foto de apertura; crea `apertura-<id>` para cada saldo no cero y un `CorteHistorico` para excluir de la revisión por referencia los movimientos anteriores. **No contabiliza cada movimiento histórico otra vez ni modifica saldos previos.** La migración y sus triggers se confirman juntos. La referencia única y el corte evitan reejecución duplicada. En una importación SQLite posterior se crea cada cuenta en cero, se abre su saldo histórico y se guarda el corte de movimientos importados.

Para desplegar: detener escritores viejos del servidor, respaldar y verificar una copia, ejecutar migraciones en PostgreSQL con la aplicación detenida, verificar el libro y arrancar solo código M10. El trigger de `Cuenta.saldo` rechaza actualizaciones de procesos viejos que no señalen la escritura contable. No ejecutar esta migración directamente sobre producción sin ensayo de restauración y ventana de mantenimiento. El DDL de inmutabilidad y el guardián requieren PostgreSQL; una cuenta SQL con permisos de superusuario o de dueño de tabla puede desactivar triggers, por lo que esos permisos deben quedar fuera del usuario de aplicación. Un `UPDATE` directo malicioso que imite la marca de sesión puede alterar la caché; `verificar_libro` lo detecta. El control final de acceso SQL requiere roles separados y despliegue revisado.

## Verificación diaria

Desde `servidor_api/` con PostgreSQL y las dependencias instaladas:

```bash
python manage.py migrate
python manage.py test libro
python manage.py test transporte panel
python manage.py test
python manage.py verificar_libro
```

El verificador comprueba cada asiento, suma global, saldos y correspondencia entre movimientos nuevos y asientos; crea una `Alerta` de tipo `descuadre contable` y termina con código distinto de cero si hay errores. Queda preparado para programarlo a las **03:30 hora de Perú** después del respaldo, pero M10 no modifica la VM ni instala un cron. La revisión humana de estos criterios, especialmente cuentas transitorias y apertura, sigue pendiente.
