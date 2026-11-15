#!/bin/sh
# Arranque del contenedor mosquitto (lo ejecuta como root el entrypoint de la
# imagen). Prepara /mosquitto/data y luego Mosquitto baja a su usuario.
set -eu
datos=/mosquitto/data
mkdir -p "$datos/tls"
if [ ! -f "$datos/passwd" ]; then
  # Vacío hasta que credenciales.sh lo llene: nadie entra, pero arranca
  : > "$datos/passwd"
fi
chown -R 1883:1883 "$datos"
chmod 700 "$datos/tls"
chmod 600 "$datos/passwd"

if [ -f /mosquitto/config/conf.d/8883.conf ]; then
  sh /mosquitto/config/certificado.sh --esperar
fi
exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
