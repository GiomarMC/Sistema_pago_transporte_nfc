# Guía del conductor: sincronizar el bus

Esta guía se aplica cuando la sincronización Bluetooth esté habilitada y el validador admita el canal BLE.

1. En un celular Android con Chrome, abre `https://subepe.app/conductor/` e inicia sesión con tu usuario de conductor. Activa Bluetooth y datos móviles.
2. Acércate al validador y pulsa **Conectar validador**. Elige el dispositivo **SubePe-V…** que coincida con el número de tu bus.
3. Comprueba que aparezca **Bus V… · … pendientes**. Mantén el celular cerca y espera a que indique los viajes subidos y la versión de listas.
4. Si no tienes datos, la página guardará los paquetes en el celular. Cuando recuperes la conexión, vuelve a abrir la página: subirá lo pendiente. Los acuses llegarán al validador en la próxima sincronización. No borres los datos del navegador hasta entonces.

## Si aparece un problema

| Mensaje o situación | Acción |
|---|---|
| Usa Chrome en Android | Abre la página en Chrome para Android; Web Bluetooth no funciona en todos los navegadores. |
| No aparece el validador | Comprueba que esté encendido, acércate y desconecta otro celular que lo esté usando. |
| Paquete incompleto o conexión cortada | Acércate y pulsa de nuevo. Reenviar paquetes no duplica viajes en el servidor. |
| Paquetes guardados | Mantén el mismo navegador y vuelve a abrir la página cuando tengas datos. |
| El servidor rechazó los paquetes | No borres la cola del celular; avisa al responsable de operaciones. |

La versión para imprimir está en `/conductor/guia/`. Faltan las capturas reales y la prueba con un conductor y un validador físico para cerrar M16-04.
