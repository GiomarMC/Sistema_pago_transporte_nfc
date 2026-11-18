# M12/M20: flujos del portal

El modo real (sin `?simulado=1`) usa `/api/v1/` a través de `cliente.js` (ver `docs/portal-cliente.md`). El modo de demostración se activa únicamente con ese parámetro. Las recargas reales siguen usando la pasarela simulada de M11; esta integración no habilita cobros bancarios.

## Verificado en el servidor local

| Paso | Estado | Evidencia |
| --- | --- | --- |
| Resumen y movimientos propios | Automatizado | `portal.tests_integracion`: aislamiento entre cuentas, filtros y paginación de 20. |
| Sesión ya iniciada y cierre | Automatizado | `portal.tests_integracion`: GET y DELETE de sesión. `GET /api/v1/sesion/` no está en C4; el portal comprueba la sesión con `resumen()`. |
| Recarga simulada | Automatizado | La orden abre `/pagos/simulador/`; al volver se muestra `/recargar/resultado/?orden=…` (bajo `/mi/` en desarrollo, en la raíz del host `mi.`). La redirección no acredita saldo; solo la verificación del pago lo hace. |
| Bloqueo y reposición | Automatizado en M17 | `panel.tests_m17`: bloqueo autenticado, aviso, solicitud y tarjeta nueva visible. |
| Activación, ingreso y recuperación | Automatizado | `clientes.tests_api_identidad`: código de un solo uso, OTP, bloqueo, dispositivos, recuperación y cambio de correo. |
| Separación por dominio | Automatizado | `portal.tests_host`: el host del cliente excluye panel y admin; el principal excluye portal y API C4. |

## Configuración de dominios

En `.env`, `DOMINIO` contiene el host de operaciones y `DOMINIO_CLIENTE` el
host del portal; ambos deben figurar en `DJANGO_ALLOWED_HOSTS`. En producción,
`DJANGO_HTTPS=1` activa cookies seguras y HSTS. Las cookies de sesión quedan
limitadas al host que las emitió porque no se configura un dominio compartido.
El servidor enruta por host a URLconf distintas; en desarrollo permanece `/mi/`
para las pruebas heredadas. Ningún pago se acredita por la redirección del
navegador. La API de M11 verifica el cobro y la URL de retorno se genera para el
host que creó la orden.

Las sesiones del portal vencen tras 15 minutos de inactividad. La activación
usa el código impreso y un OTP enviados al correo; el ingreso en un navegador
nuevo exige OTP. Los códigos se guardan como hash y los dispositivos recordados
pueden revocarse desde Seguridad. La recuperación de contraseña usa un enlace
de 30 minutos y cierra las sesiones anteriores. El cambio de correo solo se
confirma con un OTP enviado al nuevo destino. M12 reutiliza la bandeja de M13,
`ClienteCuenta` de M11 y la reposición existente de M17/M19.

Antes de habilitar el portal público faltan los DNS y certificados del dominio
elegido, ejecutar las migraciones en el entorno de destino y comprobar la
configuración de HTTPS/cabeceras sobre el host real. No se configuró la VM.

## Recorrido y capturas pendientes

1. Emitir una NTAG215 y conservar su código de activación. **Pendiente de tarjeta física.**
2. Activar la cuenta y entrar con OTP en un navegador nuevo. **Rutas verificadas en pruebas; pendiente de correo y navegador reales.**
3. Guardar captura del inicio con saldo y movimientos; crear una recarga en la pasarela simulada y guardar capturas de la orden, el resultado y el nuevo saldo.
4. Viajar con un validador real, sincronizarlo y guardar captura del movimiento. **Pendiente de hardware y sincronización.**
5. Bloquear la tarjeta desde el portal, solicitar reposición, emitir otra tarjeta y guardar capturas de ambos estados. **Pendiente de prueba física de entrega.**
6. Abrir la URL SUN como dueño, otro cliente y visitante. **Pendiente de M1-08; `/t` todavía devuelve 501.**

No hay capturas de `subepe.app` ni se afirma que el recorrido físico esté aprobado.
