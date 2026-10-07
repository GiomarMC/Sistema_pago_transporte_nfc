#!/bin/bash
# Despliegue en la VM: deja el servidor igual que la rama main de GitHub.
#
# Lo ejecuta GitHub Actions por SSH (.github/workflows/servidor.yml). En la VM,
# la clave de despliegue está limitada en ~/.ssh/authorized_keys a este script:
#   command="bash ~/Sistema_pago_transporte_nfc/servidor_api/desplegar.sh",restrict ssh-ed25519 ...
# También se puede lanzar a mano en la VM. El .env no se toca (no está en git).
#
# Todo va dentro de main(): bash lee el archivo mientras lo ejecuta, y el
# "git reset" de abajo puede cambiar este mismo script.
main() {
  set -euo pipefail
  cd "$(dirname "$0")"
  local compose="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

  echo "== Código"
  git fetch --quiet origin main
  git reset --quiet --hard origin/main
  git log -1 --format="   %h %s (%an)"

  echo "== Contenedores (las migraciones se aplican al arrancar web)"
  $compose up -d --build --remove-orphans 2>&1 | grep -vE "^ *#|^$" | tail -5

  echo "== Esperando a Django"
  for _ in $(seq 1 30); do
    if $compose exec -T web python -c "
import os, urllib.request as u
host = os.environ['DJANGO_ALLOWED_HOSTS'].split(',')[0].replace('*', 'localhost')
u.urlopen(u.Request('http://localhost:8000/panel/ingresar/', headers={'Host': host}), timeout=3)" 2>/dev/null; then
      echo "   web responde"
      docker image prune -f >/dev/null
      return 0
    fi
    sleep 2
  done
  echo "   web no responde: últimos registros" >&2
  $compose logs --tail 40 web >&2
  return 1
}
main "$@"
exit
