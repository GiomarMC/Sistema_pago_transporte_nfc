// CRC-32 (el de zlib y Ethernet: polinomio 0xEDB88320, valor inicial y final 0xFFFFFFFF).
// Comprueba que un registro guardado no se corrompió (p. ej. por un corte de energía).
#pragma once

#include <stddef.h>
#include <stdint.h>

uint32_t crc32(const void *datos, size_t n);
