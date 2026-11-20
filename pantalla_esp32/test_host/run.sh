#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
g++ -std=c++17 -Wall -Wextra -Werror -Itest_host/stubs -Ilib/Enlaces -Isrc/validador \
    src/validador/gestor_enlaces.cpp test_host/test_gestor.cpp -o /tmp/subepe-m15-test-gestor
/tmp/subepe-m15-test-gestor
g++ -std=c++17 -Wall -Wextra -Werror -Itest_host/stubs -Ilib/Enlaces \
    lib/Enlaces/CanalMqtt.cpp test_host/test_mqtt.cpp -o /tmp/subepe-m15-test-mqtt
/tmp/subepe-m15-test-mqtt
g++ -std=c++17 -Wall -Wextra -Werror -Ilib/ProtocoloSP -Ilib/Enlaces \
    lib/ProtocoloSP/Fragmentos.cpp lib/Enlaces/TablaVecinos.cpp test_host/test_vecinos.cpp \
    -o /tmp/subepe-m8-test-vecinos
/tmp/subepe-m8-test-vecinos
g++ -std=c++17 -Wall -Wextra -Werror -Ilib/ProtocoloSP -Ilib/Enlaces \
    lib/ProtocoloSP/Fragmentos.cpp lib/Enlaces/TablaVecinos.cpp lib/Enlaces/EmisorFragmentos.cpp \
    test_host/test_emisor.cpp -o /tmp/subepe-m8-test-emisor
/tmp/subepe-m8-test-emisor
