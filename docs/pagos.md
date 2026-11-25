# Pagos y recargas (M11)

La única pasarela activa es `PAGOS_PASARELA=simulada`. El cobro de prueba no mueve dinero real. La URL del simulador se construye con `PAGOS_SIMULADOR_BASE_URL`. Configure `PAGOS_WEBHOOK_SECRET` con un secreto propio antes de exponer el endpoint. La confirmación del navegador nunca acredita saldo: el webhook firmado se contrasta con `consultar()` y el mismo proceso se puede recuperar por reconsulta.

La API de cliente vive en `/api/v1/recargas/ordenes/` bajo el host de portal configurado con `DOMINIO_CLIENTE`; en desarrollo también funciona en la configuración local heredada. `ClienteCuenta` sigue enlazando usuario y cuenta, coherente con `Cliente.cuenta` de M12. Se requiere sesión de cliente y CSRF en POST. El esquema de estas rutas está en `docs/contratos/api-cliente-v1.yaml`.

La recarga pagada queda acreditada en la cuenta y pendiente de aplicación a la tarjeta.

## Política: las recargas no son reembolsables

Una recarga pagada no se devuelve, ni a pedido del cliente ni desde el panel (#102). El portal lo avisa en «Recargar saldo», antes de pagar. Razones:

- **Riesgo de pagar dos veces.** La recarga se distribuye por `sync` y `sync/v2` a los validadores, que la escriben en la tarjeta sin conexión. Un validador desconectado podría aplicarla después de devolver el dinero, y no hay forma fiable de saber que todos la retiraron.
- **Las plataformas de pago.** Las recargas llegan por bancos y monederos virtuales; devolver el dinero depende de las comisiones, plazos y reglas de cada uno.
- **Fraude.** Recargar, gastar el saldo en buses sin conexión y pedir la devolución.

**Cómo se corrige un error** (un cobro doble de la pasarela, un pasaje mal cobrado): con un **ajuste de saldo** que registra un operador (`ajuste` en `docs/libro-contable.md`, sección 5.7), no devolviendo el pago. El panel aún no tiene pantalla para hacerlo.

**Lo que la política no evita:** el banco del cliente puede revertir un pago por su cuenta (contracargo). La pasarela simulada no los genera; con una pasarela real, la conciliación diaria (`conciliar_pagos`) mostraría la diferencia y habría que tratarla. Por eso la interfaz de pasarela (C5) conserva `reembolsar()` y el estado `reembolsado` de sus transacciones, aunque el servidor no lo use.

El estado `reembolsada` de `OrdenRecarga` sigue en el modelo para no migrar, pero ninguna transición lleva a él.

Programar en la VM, tras el despliegue y validación operativa, `python manage.py revisar_ordenes` cada minuto y `python manage.py conciliar_pagos --dia AAAA-MM-DD` una vez al día. El PDF propone cada 5 minutos, pero con el umbral de reconsulta de 2 minutos eso podría tardar hasta 7 minutos desde la creación; cada minuto permite recuperar un webhook perdido antes de 5 minutos. El comando diario usa ayer si se omite `--dia`. Ambos devuelven error ante incidencias; la conciliación crea alertas. Aún no hay tareas configuradas en producción.
