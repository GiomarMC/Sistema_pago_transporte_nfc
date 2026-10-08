#!/bin/bash
# Copia de la base de datos (pg_dump, formato custom) por la salida estándar.
#
# La pide GitHub Actions cada noche (.github/workflows/respaldo.yml) con la clave
# de despliegue: desplegar.sh la ejecuta cuando el comando SSH es "respaldar".
# A mano, en la VM:  bash respaldar.sh > respaldo.dump
set -euo pipefail
cd "$(dirname "$0")"
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner'
