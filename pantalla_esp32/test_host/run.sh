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
# M3-04: recargas y tarifas binarias (orden, idempotencia, cuerpo de TARIFAS de C1 y cortes de energía)
g++ -std=c++17 -Wall -Wextra -Werror -Itest_host -Ilib/Almacenamiento -Ilib/ProtocoloSP -Ilib/micro-ecc \
    lib/Almacenamiento/Crc32.cpp lib/Almacenamiento/ArchivoAtomico.cpp lib/Almacenamiento/Recargas.cpp \
    lib/Almacenamiento/TarifasGuardadas.cpp lib/ProtocoloSP/ProtocoloSP.cpp /tmp/subepe-uecc.o \
    test_host/test_recargas_tarifas.cpp -lmbedcrypto -o /tmp/subepe-m3-test-recargas
/tmp/subepe-m3-test-recargas
# M3-05: paquetes ajenos y LISTAS firmados (diario, límites de 500 y 50, acuse, cortes de energía, CanalFalso)
g++ -std=c++17 -Wall -Wextra -Werror -Itest_host -Ilib/Almacenamiento -Ilib/ProtocoloSP -Ilib/Enlaces -Ilib/micro-ecc \
    lib/Almacenamiento/Crc32.cpp lib/Almacenamiento/PaquetesGuardados.cpp lib/ProtocoloSP/ProtocoloSP.cpp \
    /tmp/subepe-uecc.o test_host/test_paquetes_guardados.cpp -lmbedcrypto -o /tmp/subepe-m3-test-paquetes
/tmp/subepe-m3-test-paquetes
# M3-06: consumo de datos por canal y día (31 días, guardado cada 5 minutos, cortes de energía, contadores de enlace)
g++ -std=c++17 -Wall -Wextra -Werror -Itest_host -Ilib/Almacenamiento lib/Almacenamiento/Crc32.cpp \
    lib/Almacenamiento/ArchivoAtomico.cpp lib/Almacenamiento/ConsumoDatos.cpp test_host/test_consumo.cpp \
    -o /tmp/subepe-m3-test-consumo
/tmp/subepe-m3-test-consumo
# M4-02: las cinco reglas de LISTAS (C1 5.5) y TARIFAS con el SincronizadorV2 y el almacenamiento binario, con cortes de energía
g++ -std=c++17 -Wall -Wextra -Werror -Itest_host -Isrc/validador -Ilib/Enlaces -Ilib/Almacenamiento -Ilib/ProtocoloSP -Ilib/micro-ecc \
    src/validador/sincronizador_v2.cpp lib/ProtocoloSP/ProtocoloSP.cpp lib/Almacenamiento/Crc32.cpp lib/Almacenamiento/ArchivoAtomico.cpp \
    lib/Almacenamiento/EstadoPersistente.cpp lib/Almacenamiento/ListaNegra.cpp lib/Almacenamiento/Recargas.cpp \
    lib/Almacenamiento/TarifasGuardadas.cpp lib/Almacenamiento/PaquetesGuardados.cpp lib/Almacenamiento/AplicadorListas.cpp \
    /tmp/subepe-uecc.o test_host/test_listas_v2.cpp -lmbedcrypto -o /tmp/subepe-m4-test-listas
/tmp/subepe-m4-test-listas
# AlmacenSyncBinario: el sincronizador v2 sobre el almacenamiento binario completo (solo se escribe en paso(), por pasos)
g++ -std=c++17 -Wall -Wextra -Werror -Itest_host -Isrc/validador -Ilib/Enlaces -Ilib/Almacenamiento -Ilib/ProtocoloSP -Ilib/micro-ecc \
    src/validador/sincronizador_v2.cpp src/validador/almacen_sync_binario.cpp lib/ProtocoloSP/ProtocoloSP.cpp \
    lib/Almacenamiento/Crc32.cpp lib/Almacenamiento/ArchivoAtomico.cpp lib/Almacenamiento/EstadoPersistente.cpp \
    lib/Almacenamiento/ColaEventos.cpp lib/Almacenamiento/ListaNegra.cpp lib/Almacenamiento/Recargas.cpp \
    lib/Almacenamiento/TarifasGuardadas.cpp lib/Almacenamiento/PaquetesGuardados.cpp lib/Almacenamiento/AplicadorListas.cpp \
    /tmp/subepe-uecc.o test_host/test_almacen_sync.cpp -lmbedcrypto -o /tmp/subepe-m4-test-almacen-sync
/tmp/subepe-m4-test-almacen-sync
# M3-01: eventos binarios <-> línea JSON del sincronizador v1 y de /eventos.txt (formato sin cambios, ida y vuelta)
g++ -std=c++17 -Wall -Wextra -Werror -Ilib/Almacenamiento -Ilib/ProtocoloSP -Ilib/micro-ecc \
    lib/Almacenamiento/EventosTexto.cpp lib/ProtocoloSP/ProtocoloSP.cpp /tmp/subepe-uecc.o \
    test_host/test_eventos_texto.cpp -lmbedcrypto -o /tmp/subepe-m3-test-eventos-texto
/tmp/subepe-m3-test-eventos-texto
