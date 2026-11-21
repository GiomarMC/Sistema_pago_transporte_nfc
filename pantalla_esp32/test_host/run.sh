#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
g++ -std=c++17 -Wall -Wextra -Werror -Itest_host/stubs -Ilib/Enlaces -Isrc/validador \
    src/validador/gestor_enlaces.cpp test_host/test_gestor.cpp -o /tmp/subepe-m15-test-gestor
/tmp/subepe-m15-test-gestor
g++ -std=c++17 -Wall -Wextra -Werror -Itest_host/stubs -Ilib/Enlaces \
    lib/Enlaces/CanalMqtt.cpp test_host/test_mqtt.cpp -o /tmp/subepe-m15-test-mqtt
/tmp/subepe-m15-test-mqtt
# M3-01 y M3-02: almacenamiento binario con cortes de energía simulados
g++ -std=c++17 -Wall -Wextra -Werror -Itest_host -Ilib/Almacenamiento -Ilib/ProtocoloSP -Ilib/micro-ecc \
    lib/Almacenamiento/Crc32.cpp lib/Almacenamiento/EstadoPersistente.cpp lib/Almacenamiento/ColaEventos.cpp \
    lib/ProtocoloSP/ProtocoloSP.cpp /tmp/subepe-uecc.o test_host/test_almacenamiento.cpp -lmbedcrypto \
    -o /tmp/subepe-m3-test-almacen
/tmp/subepe-m3-test-almacen
# M3-03: lista negra binaria (búsqueda por bloques, mezcla por partes y cortes de energía)
g++ -std=c++17 -Wall -Wextra -Werror -Itest_host -Ilib/Almacenamiento \
    lib/Almacenamiento/Crc32.cpp lib/Almacenamiento/ListaNegra.cpp test_host/test_listanegra.cpp \
    -o /tmp/subepe-m3-test-lista
/tmp/subepe-m3-test-lista
