#!/bin/bash
# Verificación diaria del libro mayor (M10-05): manage.py verificar_libro dentro
# del contenedor web. Cada descuadre queda como alerta "descuadre contable" en el
# panel, y el script sale con código 1. Después, revisar_operaciones (#237) deja
# alertas de las operaciones de tarjeta que faltan.
#
# La lanza el cron de la VM cada día a las 03:30 de Perú, media hora después del
# respaldo (desplegar.sh instala la línea del crontab). Salida en
# ~/verificar_libro.log. A mano, en la VM:  bash verificar_libro.sh
set -uo pipefail
cd "$(dirname "$0")"
echo "== $(date -u +%FT%TZ)"
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T web \
  python manage.py verificar_libro
estado=$?
# Operaciones de tarjeta que faltan (#237): cobros grabados en una tarjeta que el servidor no tiene. Solo deja
# alertas; no cambia el código de salida
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T web \
  python manage.py revisar_operaciones
exit $estado
