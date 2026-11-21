#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
g++ -std=c++17 -Wall -Wextra -Werror -Itest_host/stubs -Ilib/Enlaces -Isrc/validador \
    src/validador/gestor_enlaces.cpp test_host/test_gestor.cpp -o /tmp/subepe-m15-test-gestor
/tmp/subepe-m15-test-gestor
g++ -std=c++17 -Wall -Wextra -Werror -Itest_host/stubs -Ilib/Enlaces \
    lib/Enlaces/CanalMqtt.cpp test_host/test_mqtt.cpp -o /tmp/subepe-m15-test-mqtt
/tmp/subepe-m15-test-mqtt
# M4-01: SincronizadorV2 con firmas reales (necesita mbedtls y micro-ecc en el ordenador)
gcc -O2 -c -DuECC_SQUARE_FUNC=1 -DuECC_OPTIMIZATION_LEVEL=3 -DuECC_SUPPORTS_secp160r1=0 -DuECC_SUPPORTS_secp192r1=0 \
    -DuECC_SUPPORTS_secp224r1=0 -DuECC_SUPPORTS_secp256k1=0 -Ilib/micro-ecc lib/micro-ecc/uECC.c -o /tmp/subepe-uecc.o
g++ -std=c++17 -Wall -Wextra -Werror -Itest_host -Isrc/validador -Ilib/Enlaces -Ilib/ProtocoloSP -Ilib/micro-ecc \
    src/validador/sincronizador_v2.cpp lib/ProtocoloSP/ProtocoloSP.cpp /tmp/subepe-uecc.o \
    test_host/test_sincronizador_v2.cpp -lmbedcrypto -o /tmp/subepe-m4-test-sync
/tmp/subepe-m4-test-sync
g++ -std=c++17 -Wall -Wextra -Werror -Ilib/ProtocoloSP -Ilib/Enlaces \
    lib/ProtocoloSP/Fragmentos.cpp lib/Enlaces/TablaVecinos.cpp test_host/test_vecinos.cpp \
    -o /tmp/subepe-m8-test-vecinos
/tmp/subepe-m8-test-vecinos
g++ -std=c++17 -Wall -Wextra -Werror -Ilib/ProtocoloSP -Ilib/Enlaces \
    lib/ProtocoloSP/Fragmentos.cpp lib/Enlaces/TablaVecinos.cpp lib/Enlaces/EmisorFragmentos.cpp \
    test_host/test_emisor.cpp -o /tmp/subepe-m8-test-emisor
/tmp/subepe-m8-test-emisor
