// CRC-32 (el de zlib y Ethernet: polinomio 0xEDB88320, valor inicial y final 0xFFFFFFFF).
// Comprueba que un registro guardado no se corrompió (p. ej. por un corte de energía).
#pragma once

#include <stddef.h>
#include <stdint.h>

// `previo` encadena: crc32(b, nb, crc32(a, na)) == crc32(a ++ b), así se firma un archivo grande
// por partes. Sin él (0) es el CRC de los datos solos.
uint32_t crc32(const void *datos, size_t n, uint32_t previo = 0);
