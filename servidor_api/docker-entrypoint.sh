#!/bin/sh
# Aplica las migraciones pendientes antes de arrancar el servidor
set -e
python manage.py migrate --noinput
exec "$@"
