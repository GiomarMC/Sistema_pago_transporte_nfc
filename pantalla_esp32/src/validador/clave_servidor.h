// Clave pública ECDSA P-256 del servidor (contrato C1, M0-06): la ESP32 la usa para
// verificar los LISTAS, ACUSE y TARIFAS que recibe.
//
// TODAVÍA SIN CONFIGURAR. Se genera junto con la clave privada del servidor:
//   cd servidor_api && python manage.py generar_clave_listas
// que sustituye este archivo por la clave real. Mientras CLAVE_SERVIDOR_CONFIGURADA
// valga 0, el firmware no debe aceptar paquetes del servidor.
#pragma once
#include <stdint.h>

#define CLAVE_SERVIDOR_CONFIGURADA 0

static const uint8_t CLAVE_SERVIDOR[65] = {0};
