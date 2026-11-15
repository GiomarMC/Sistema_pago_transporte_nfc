#!/bin/sh
# Copia el certificado de Let's Encrypt de Caddy (volumen caddy_data, montado en
# /caddy) a /mosquitto/data/tls con permisos para el usuario mosquitto. Lo usan
# arrancar.sh y la renovación diaria (desplegar.sh renovar-mqtt), que luego
# manda HUP para que Mosquitto lo vuelva a leer.
#   --esperar: espera hasta 3 min a que Caddy tenga el certificado (primer arranque)
set -eu
dominio=$(printf '%s' "${DOMINIO:?falta DOMINIO}" | cut -d, -f1 | tr -d ' ')
origen=/caddy/caddy/certificates/acme-v02.api.letsencrypt.org-directory/$dominio
destino=/mosquitto/data/tls

intentos=1
[ "${1:-}" = "--esperar" ] && intentos=36
while [ ! -s "$origen/$dominio.crt" ] || [ ! -s "$origen/$dominio.key" ]; do
  intentos=$((intentos - 1))
  if [ "$intentos" -le 0 ]; then
    echo "certificado.sh: no está el certificado de $dominio en $origen" >&2
    exit 1
  fi
  sleep 5
done
install -o 1883 -g 1883 -m 644 "$origen/$dominio.crt" "$destino/servidor.crt.nuevo"
install -o 1883 -g 1883 -m 600 "$origen/$dominio.key" "$destino/servidor.key.nuevo"
mv -f "$destino/servidor.crt.nuevo" "$destino/servidor.crt"
mv -f "$destino/servidor.key.nuevo" "$destino/servidor.key"
echo "certificado.sh: certificado de $dominio copiado"
