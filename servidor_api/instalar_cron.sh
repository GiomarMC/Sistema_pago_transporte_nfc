#!/bin/bash
# Instala en el crontab del usuario la verificación diaria del libro mayor
# (verificar_libro.sh) a las 03:30 de Perú, después del respaldo de las 03:00.
#
# Perú es UTC-5 sin horario de verano; la VM está en UTC y el cron de Ubuntu no
# entiende CRON_TZ: por eso 08:30. Solo toca su propia línea (la marca del
# final); si ya está, no hace nada. desplegar.sh lo llama en cada despliegue.
# A mano, en la VM:  bash instalar_cron.sh
set -euo pipefail
cd "$(dirname "$0")"
MARCA="# subepe: verificar_libro"
linea="30 8 * * * bash $PWD/verificar_libro.sh >> $HOME/verificar_libro.log 2>&1 $MARCA"
actual=$(crontab -l 2>/dev/null || true)
if grep -qxF "$linea" <<<"$actual"; then
  exit 0
fi
{ grep -vF "$MARCA" <<<"$actual" | grep -v '^$' || true; echo "$linea"; } | crontab -
echo "== Cron: verificar_libro a las 03:30 de Perú (log en ~/verificar_libro.log)"
