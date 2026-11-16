#!/bin/bash
# Regenera el password_file de Mosquitto desde la base de datos y lo recarga:
# el usuario puente (MQTT_PUENTE_CLAVE del .env) y cada validador activo con
# contraseña MQTT (manage.py clave_validador <n.º> --mqtt).
#
#   bash mosquitto/credenciales.sh
#
# Lo corre desplegar.sh en cada despliegue; a mano, después de dar o cambiar una
# contraseña MQTT. Solo maneja hashes: la contraseña en claro no pasa por aquí.
set -euo pipefail
cd "$(dirname "$0")/.."
compose=${COMPOSE:-docker compose}

archivo=$(mktemp)
trap 'rm -f "$archivo"' EXIT
$compose exec -T web python manage.py mqtt_credenciales > "$archivo"
$compose exec -T mosquitto sh -c '
  set -e
  cat > /mosquitto/data/passwd.nuevo
  chown 1883:1883 /mosquitto/data/passwd.nuevo
  chmod 600 /mosquitto/data/passwd.nuevo
  mv -f /mosquitto/data/passwd.nuevo /mosquitto/data/passwd' < "$archivo"
$compose kill -s HUP mosquitto >/dev/null 2>&1
echo "   Mosquitto: $(grep -c . "$archivo" || true) usuarios ($(cut -d: -f1 "$archivo" | paste -sd' ' -))"
