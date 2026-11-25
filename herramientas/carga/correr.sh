#!/bin/bash
# Prueba de carga del servidor por MQTT (M21-02, #156), de punta a punta y en local:
#   1. levanta una copia del servidor (proyecto Compose "carga") con los límites de
#      docker-compose.carga.yml, con secretos propios y su propia base de datos;
#   2. crea los validadores con preparar.py y regenera las credenciales de Mosquitto;
#   3. corre herramientas/carga_mqtt.py, que mide latencias, CPU, RAM y swap;
#   4. borra la copia (contenedores y volúmenes) aunque algo falle.
#
#   herramientas/carga/correr.sh [carpeta de resultados]
#
# Variables: VALIDADORES (50), CADA (10 s), DURACION (1800 s), CARGA_CPU (núcleo, 2),
# PYTHON (un python con paho-mqtt y cryptography; por defecto python3).
# No toca el servidor local (servidor_api/.env, sus contenedores ni sus volúmenes).
set -euo pipefail
raiz=$(cd "$(dirname "$0")/../.." && pwd)
salida=${1:-$raiz/herramientas/carga/resultados/$(date +%Y%m%d-%H%M)}
python=${PYTHON:-python3}
export CARGA_VALIDADORES=${VALIDADORES:-50}
cada=${CADA:-10}
duracion=${DURACION:-1800}
export CARGA_CPU=${CARGA_CPU:-2}
export CARGA_WEB_PORT=${CARGA_WEB_PORT:-18000}
export CARGA_MQTT_PORT=${CARGA_MQTT_PORT:-18830}

trabajo=$(mktemp -d)          # secretos de la copia: se borran al terminar
export CARGA_ENV="$trabajo/env"
compose="docker compose -p carga --env-file $CARGA_ENV -f $raiz/servidor_api/docker-compose.yml -f $raiz/herramientas/carga/docker-compose.carga.yml"
limpiar() {
  echo "== Borrando la copia del servidor"
  $compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$trabajo"
}
trap limpiar EXIT

echo "== Secretos propios de la copia"
"$python" - "$trabajo" <<'EOF'
import pathlib, secrets, sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
d = pathlib.Path(sys.argv[1])
clave = ec.generate_private_key(ec.SECP256R1())
privada = clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode().strip().replace("\n", "\\n")
(d / "servidor.pem").write_bytes(clave.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
(d / "env").write_text("\n".join([
    f"DJANGO_SECRET_KEY={secrets.token_urlsafe(50)}", "DJANGO_DEBUG=0", "DJANGO_ALLOWED_HOSTS=*",
    "DOMINIO_CLIENTE=mi.localhost", "DJANGO_HTTPS=0",
    "POSTGRES_DB=transporte", "POSTGRES_USER=transporte", f"POSTGRES_PASSWORD={secrets.token_urlsafe(24)}",
    "PAGOS_PASARELA=simulada", f"PAGOS_SECRETO_WEBHOOK={secrets.token_urlsafe(32)}",
    f"LISTAS_CLAVE_PRIVADA={privada}", f"MQTT_PUENTE_CLAVE={secrets.token_urlsafe(32)}",
    "CORREO_USUARIO=", "CORREO_CLAVE_APP=", ""]))
EOF

echo "== Levantando la copia (proyecto carga, CPU $CARGA_CPU)"
$compose --progress quiet up -d --build
for i in $(seq 1 90); do
  codigo=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$CARGA_WEB_PORT/panel/ingresar/" || true)
  [ "$codigo" = "200" ] && break
  sleep 2
done
[ "$codigo" = "200" ] || { echo "El servidor de la copia no respondió"; $compose logs --tail 40 web; exit 1; }

echo "== Creando $CARGA_VALIDADORES validadores"
# -v 0 y tail: manage.py shell puede escribir avisos antes del JSON
$compose exec -T -e CARGA_VALIDADORES web python manage.py shell -v 0 -c "$(cat "$raiz/herramientas/carga/preparar.py")" \
  | tail -n 1 > "$trabajo/credenciales.json"
COMPOSE="$compose" bash "$raiz/servidor_api/mosquitto/credenciales.sh"
$compose restart puente >/dev/null
for i in $(seq 1 30); do
  $compose logs puente 2>/dev/null | grep -q "conectado a" && break
  sleep 1
done
$compose logs puente 2>/dev/null | grep -q "conectado a" || { echo "El puente no conectó"; $compose logs --tail 20 puente; exit 1; }

mkdir -p "$salida"
{
  echo "fecha: $(date -Iseconds)"
  echo "commit: $(git -C "$raiz" rev-parse --short HEAD)"
  echo "validadores: $CARGA_VALIDADORES · evento cada $cada s · $duracion s · núcleo $CARGA_CPU"
  echo "equipo: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs) · $(nproc) CPU · kernel $(uname -r)"
  echo "docker: $(docker version --format '{{.Server.Version}}') · compose $(docker compose version --short)"
  echo "límites:"
  for s in db web mosquitto puente; do
    id=$($compose ps -q "$s")
    echo "  $s: $(docker inspect -f 'RAM {{.HostConfig.Memory}} B · RAM+swap {{.HostConfig.MemorySwap}} B · CPU {{.HostConfig.CpusetCpus}}' "$id")"
  done
} > "$salida/entorno.txt"
cat "$salida/entorno.txt"

echo "== Carga"
"$python" "$raiz/herramientas/carga_mqtt.py" --credenciales "$trabajo/credenciales.json" \
  --clave-publica "$trabajo/servidor.pem" --puerto "$CARGA_MQTT_PORT" --cada "$cada" --duracion "$duracion" \
  --salida "$salida" --medir-proyecto carga

echo "== Comprobaciones finales en el servidor"
$compose exec -T web python manage.py shell -v 0 -c "
from transporte.models import Alerta, EventoRecibido, Movimiento
print('viajes registrados:', Movimiento.objects.filter(tipo='viaje').count())
print('eventos recibidos por MQTT:', EventoRecibido.objects.filter(via='mqtt').count())
print('alertas:', dict((a, Alerta.objects.filter(tipo=a).count()) for a in Alerta.objects.values_list('tipo', flat=True).distinct()))
" | tee "$salida/servidor.txt"
$compose exec -T web python manage.py verificar_libro 2>&1 | tail -3 | tee -a "$salida/servidor.txt" || true
$compose logs --no-color puente > "$salida/puente.log" 2>&1 || true
# Antes de cargar las credenciales el puente no puede entrar ("rechazó la conexión"): eso no cuenta
echo "fallos al procesar en el puente: $(grep -c 'no se pudo\|Traceback' "$salida/puente.log" || true)" | tee -a "$salida/servidor.txt"
echo "Resultados en $salida"
