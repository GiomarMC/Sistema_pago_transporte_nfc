# M20: flujos del portal

El modo real (sin `?simulado=1`) usa `/api/v1/` a través de `cliente.js` (ver `docs/portal-cliente.md`). El modo de demostración se activa únicamente con ese parámetro. Las recargas reales siguen usando la pasarela simulada de M11; esta integración no habilita cobros bancarios.

## Verificado en el servidor local

| Paso | Estado | Evidencia |
| --- | --- | --- |
| Resumen y movimientos propios | Automatizado | `portal.tests_integracion`: aislamiento entre cuentas, filtros y paginación de 20. |
| Sesión ya iniciada y cierre | Automatizado | `portal.tests_integracion`: GET y DELETE de sesión. `GET /api/v1/sesion/` no está en C4; el portal comprueba la sesión con `resumen()`. |
| Recarga simulada | Automatizado | La orden abre `/pagos/simulador/`; al volver se muestra `/recargar/resultado/?orden=…` (bajo `/mi/` en desarrollo, en la raíz del host `mi.`). La redirección no acredita saldo; solo la verificación del pago lo hace. |
| Bloqueo y reposición | Automatizado en M17 | `panel.tests_m17`: bloqueo autenticado, aviso, solicitud y tarjeta nueva visible. |

## Recorrido y capturas pendientes

1. Emitir una NTAG215 y conservar su código de activación. **Pendiente de tarjeta física.**
2. Activar la cuenta y entrar con OTP en un navegador nuevo. **Pendiente de las rutas M12-03 y M12-04.**
3. Guardar captura del inicio con saldo y movimientos; crear una recarga en la pasarela simulada y guardar capturas de la orden, el resultado y el nuevo saldo.
4. Viajar con un validador real, sincronizarlo y guardar captura del movimiento. **Pendiente de hardware y sincronización.**
5. Bloquear la tarjeta desde el portal, solicitar reposición, emitir otra tarjeta y guardar capturas de ambos estados. **Pendiente de prueba física de entrega.**
6. Abrir la URL SUN como dueño, otro cliente y visitante. **Pendiente de M1-08 y M12-04; `/mi/t` todavía devuelve 501.**

No hay capturas de `subepe.app` ni se afirma que el recorrido físico esté aprobado. El ruteo por host (M12-02) ya está incorporado, pero las rutas `/api/v1/` y `/pagos/simulador/` aún no se sirven en el host `mi.`: eso queda para el backend de M12.
