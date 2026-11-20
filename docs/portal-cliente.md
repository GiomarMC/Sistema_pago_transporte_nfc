# Portal del cliente (M14)

Interfaz web de `mi.subepe.app`: HTML con plantillas de Django y JavaScript sin framework ni compilación. Las plantillas están en `servidor_api/portal/templates/portal/` y los archivos estáticos en `servidor_api/portal/static/portal/`.

## Cómo se sirve

- **Producción:** solo en el host `DOMINIO_CLIENTE` (ruteo por host: `config/middleware.py`). Las rutas son `/ingresar/`, `/activar/`, `/movimientos/`, etc., y ahí mismo viven `/api/v1/` y `/pagos/simulador/`.
- **Desarrollo:** además bajo `/mi/` del dominio principal, solo si `PORTAL_EN_RUTA_MI=1` (por defecto activo con `DJANGO_DEBUG=1`). En producción esa ruta responde 404 para que la sesión del portal no comparta host con el panel (decisión D7).

## Capas del JavaScript

| Archivo | Para qué sirve |
|---|---|
| `api.js` | Cliente del contrato C4: una función por ruta (`docs/contratos/api-cliente-v1.yaml`). |
| `api-simulada.js` | Las mismas funciones con datos falsos y sin servidor (`?simulado=1`). |
| `cliente.js` | Adaptador: las páginas llaman a `api.ingresar({dni, clave})` y este módulo lo traduce a `api.js` o `api-simulada.js`. No define rutas, salvo la extensión `reenviarOtp`. |
| `app.js`, `paginas.js`, `intentos.js` | Utilidades comunes, lógica de cada página y la clave de compra (Idempotency-Key). |

Al agregar una ruta nueva a C4, se añade primero a `api.js` y `api-simulada.js`, y luego una línea en `cliente.js`.

## Recorrido de prueba

Con Django en marcha, abre `/mi/ingresar/?simulado=1`. Datos de `api-simulada.js`: DNI `45678912`, contraseña `subepe123` y código OTP `123456`. Para activar una tarjeta: DNI `45678912`, tarjeta `1234 5678 2`, código `K7Q2-M9XA` y OTP `123456`. El estado simulado vive solo en el navegador (`sessionStorage`); `?error=<codigo>` fuerza un error de C4.

En Recargar, el simulador no usa pasarela: la orden pasa a pagada a los 2 s y a acreditada a los 4 s, y el resultado la consulta cada 2 s. Los montos S/ 6.66, S/ 7.77 y S/ 8.88 producen una orden fallida, expirada y pendiente. La clave `Idempotency-Key` se conserva en `sessionStorage` al recargar la página o reintentar tras una caída de red.

## Con el servidor real

Sin `simulado=1`, `cliente.js` usa `api.js` con la sesión y el CSRF de Django, y las rutas de activación, sesión, contraseña, resumen, movimientos, perfil, seguridad, bloqueo, reposiciones y recargas están conectadas (M12, M17, M20). Falta en el contrato C4 la extensión `POST /api/v1/sesion/otp/reenviar/` (reenviar el código de inicio de sesión), que el servidor ya atiende. El simulador de M11 devuelve al cliente a `/recargar/resultado/?orden=…` en el host dedicado o a `/mi/recargar/resultado/?orden=…` en desarrollo. El resultado muestra éxito solo con estado `acreditada`. No hay pasarela bancaria real configurada.

## Verificación

- Python: `python manage.py test portal` (desde `servidor_api`).
- JavaScript: `node --test servidor_api/portal/tests/*.test.mjs` (desde la raíz; es lo que ejecuta la CI).
- Pendiente de M14-09: Lighthouse en Inicio, Movimientos y Recargar (móvil: accesibilidad y buenas prácticas ≥95, rendimiento ≥90), revisión a 360, 390 y 1280 px en Chrome Android y Safari iOS reales, y capturas en el PR. Son pasos de QA manual.
