#!/bin/bash
# Prueba la configuración de este directorio en un Mosquitto desechable (no toca
# ningún despliegue): contraseñas en formato $7$ generadas como lo hace Django,
# ACL de C1 5.8 y que no entra nadie anónimo.
#
#   bash mosquitto/probar_acl.sh
set -euo pipefail
cd "$(dirname "$0")"
nombre=subepe-mqtt-prueba-$$
red=$nombre
trap 'docker rm -f "$nombre" >/dev/null 2>&1 || true; docker network rm "$red" >/dev/null 2>&1 || true; rm -rf "$tmp"' EXIT
tmp=$(mktemp -d)

# Hashes como los de transporte/sync_v2/mqtt.py (PBKDF2-SHA512, 1000 iteraciones)
python3 - "$tmp/passwd" <<'PY'
import base64, hashlib, secrets, sys
def h(clave):
    sal = secrets.token_bytes(64)
    d = hashlib.pbkdf2_hmac("sha512", clave.encode(), sal, 1000, 64)
    return f"$7$1000${base64.b64encode(sal).decode()}${base64.b64encode(d).decode()}"
with open(sys.argv[1], "w") as f:
    for usuario in ("puente", "101", "102"):
        f.write(f"{usuario}:{h('clave-' + usuario)}\n")
PY

docker network create "$red" >/dev/null
docker run -d --name "$nombre" --network "$red" \
  -v "$PWD:/mosquitto/config:ro" -v "$tmp/passwd:/semilla/passwd:ro" \
  --entrypoint sh eclipse-mosquitto:2 -c \
  'mkdir -p /mosquitto/data && cp /semilla/passwd /mosquitto/data/passwd &&
   exec /docker-entrypoint.sh sh /mosquitto/config/arrancar.sh' >/dev/null
for _ in $(seq 1 20); do
  docker logs "$nombre" 2>&1 | grep -q "running" && break
  sleep 0.5
done

cliente() { docker run --rm --network "$red" eclipse-mosquitto:2 "$@" -h "$nombre" -p 1883; }

# Lee un tópico mientras otro usuario publica; imprime lo recibido (vacío si nada)
recibe() {  # usuario_lee topico usuario_publica
  local salida
  salida=$(mktemp)
  cliente mosquitto_sub -u "$1" -P "clave-$1" -t "$2" -C 1 -W 3 > "$salida" 2>/dev/null &
  local pid=$!
  sleep 1
  cliente mosquitto_pub -u "$3" -P "clave-$3" -t "$2" -m "hola-$3" -q 1 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  cat "$salida"; rm -f "$salida"
}

fallos=0
comprobar() {  # descripción esperado obtenido
  if [ "$2" = "$3" ]; then echo "  ok    $1"; else echo "  FALLA $1 (esperado '$2', obtenido '$3')"; fallos=$((fallos + 1)); fi
}

echo "ACL de C1 5.8:"
comprobar "101 no puede leer sp/v/102/baja"            ""            "$(recibe 101 sp/v/102/baja puente)"
comprobar "101 no puede publicar en sp/todos/urgente"  ""            "$(recibe puente sp/todos/urgente 101)"
comprobar "101 lee sp/todos/urgente del puente"        "hola-puente" "$(recibe 101 sp/todos/urgente puente)"
comprobar "101 lee su sp/v/101/baja"                   "hola-puente" "$(recibe 101 sp/v/101/baja puente)"
comprobar "el puente lee sp/v/101/sube"                "hola-101"    "$(recibe puente sp/v/101/sube 101)"
comprobar "101 no puede publicar en sp/v/102/sube"     ""            "$(recibe puente sp/v/102/sube 101)"
comprobar "el puente lee sp/v/101/conexion"            "hola-101"    "$(recibe puente sp/v/101/conexion 101)"

echo "Autenticación:"
anon=$(cliente mosquitto_pub -t sp/v/101/sube -m x 2>&1 || true)
comprobar "anónimo rechazado" "si" "$(grep -qi 'not authorised\|refused' <<<"$anon" && echo si || echo no)"
mala=$(cliente mosquitto_pub -u 101 -P mala -t sp/v/101/sube -m x 2>&1 || true)
comprobar "contraseña incorrecta rechazada" "si" "$(grep -qi 'not authorised\|refused' <<<"$mala" && echo si || echo no)"

echo "RAM en reposo: $(docker stats --no-stream --format '{{.MemUsage}}' "$nombre")"
[ "$fallos" -eq 0 ] && echo "Todo bien." || { echo "$fallos fallos."; exit 1; }
