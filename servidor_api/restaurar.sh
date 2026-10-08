#!/bin/bash
# Restaura una copia hecha con respaldar.sh, REEMPLAZANDO los datos actuales.
#
#   bash restaurar.sh respaldo.dump
#
# Para una copia de GitHub (respaldo-*.dump.gpg), descifrarla antes con:
#   gpg --decrypt respaldo-AAAAMMDD-HHMM.dump.gpg > respaldo.dump
set -euo pipefail
copia=$(realpath "${1:?uso: bash restaurar.sh respaldo.dump}")
cd "$(dirname "$0")"
compose="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

read -rp "Se borrarán los datos actuales y se cargarán los de $copia. ¿Seguir? [s/N] " ok
[ "$ok" = "s" ] || exit 1

$compose up -d db
$compose stop web   # que nadie escriba mientras se restaura
$compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' < "$copia"
$compose start web
echo "Copia restaurada. Las migraciones pendientes se aplican al arrancar web."
