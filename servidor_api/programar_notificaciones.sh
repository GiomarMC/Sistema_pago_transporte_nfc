#!/bin/bash
# Programa en la VM el reintento de correos: `manage.py enviar_notificaciones` cada 5 minutos.
#
# Se ejecuta una vez en la VM, desde servidor_api/:
#   bash programar_notificaciones.sh
# Es idempotente: si ya está programado, reemplaza la línea en vez de duplicarla.
# La salida queda en ~/enviar_notificaciones.log.
set -euo pipefail
cd "$(dirname "$0")"

marca="# subepe: enviar_notificaciones"
linea="*/5 * * * * cd $PWD && docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T web python manage.py enviar_notificaciones >> \$HOME/enviar_notificaciones.log 2>&1 $marca"

actual=$(crontab -l 2>/dev/null || true)
{ grep -vF "$marca" <<<"$actual" || true; echo "$linea"; } | crontab -

echo "Programado:"
crontab -l | grep -F "$marca"
