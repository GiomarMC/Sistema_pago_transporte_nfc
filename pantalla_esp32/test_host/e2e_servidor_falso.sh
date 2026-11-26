#!/bin/sh
# M4-04: el SincronizadorV2 (C++) contra el servidor C1 falso (Python), por HTTP en el ordenador.
#   sh pantalla_esp32/test_host/e2e_servidor_falso.sh
set -eu
cd "$(dirname "$0")/.."
RAIZ=$(cd .. && pwd)
PUERTO=18765
CLAVE_PEM=/tmp/subepe-e2e-clave.pem
REGISTRO=/tmp/subepe-e2e-servidor.log
CLAVE_HMAC=$(python3 -c "print(bytes((0x10+i)&0xFF for i in range(32)).hex())")

gcc -O2 -c -DuECC_SQUARE_FUNC=1 -DuECC_OPTIMIZATION_LEVEL=3 -DuECC_SUPPORTS_secp160r1=0 -DuECC_SUPPORTS_secp192r1=0 \
    -DuECC_SUPPORTS_secp224r1=0 -DuECC_SUPPORTS_secp256k1=0 -Ilib/micro-ecc lib/micro-ecc/uECC.c -o /tmp/subepe-uecc.o
g++ -std=c++17 -Wall -Wextra -Werror -Isrc/validador -Ilib/Enlaces -Ilib/ProtocoloSP -Ilib/micro-ecc \
    src/validador/sincronizador_v2.cpp lib/ProtocoloSP/ProtocoloSP.cpp /tmp/subepe-uecc.o \
    test_host/e2e_servidor_falso.cpp -lmbedcrypto -o /tmp/subepe-e2e

rm -f "$CLAVE_PEM"
PUBLICA=$(python3 "$RAIZ/herramientas/servidor_c1_falso.py" --clave-privada "$CLAVE_PEM" --imprimir-clave-publica | sed -n 2p)
python3 -u "$RAIZ/herramientas/servidor_c1_falso.py" --direccion 127.0.0.1 --puerto $PUERTO --clave "107=$CLAVE_HMAC" \
    --clave-privada "$CLAVE_PEM" --bloquear 4242 --bloquear 77 > "$REGISTRO" 2>&1 &
SERVIDOR=$!
trap 'kill $SERVIDOR 2>/dev/null || true; rm -f "$CLAVE_PEM"' EXIT
for _ in 1 2 3 4 5 6 7 8 9 10; do
    curl -s -o /dev/null "http://127.0.0.1:$PUERTO/" && break
    sleep 0.3
done

/tmp/subepe-e2e $PUERTO "$PUBLICA"

# Lo que el servidor falso vio y decodificó
grep -q "← HOLA .* de validador 107" "$REGISTRO"
grep -q "← EVENTOS .* 30 evento(s)" "$REGISTRO"
grep -q "ACUSE   para el validador 107: confirma hasta el evento 100" "$REGISTRO"
echo "El servidor falso decodificó HOLA y EVENTOS y confirmó hasta el 100: OK"
