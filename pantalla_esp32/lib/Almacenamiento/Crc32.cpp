#include "Crc32.h"

uint32_t crc32(const void *datos, size_t n, uint32_t previo) {
  const uint8_t *p = static_cast<const uint8_t *>(datos);
  uint32_t crc = ~previo;
  for (size_t i = 0; i < n; i++) {
    crc ^= p[i];
    for (int bit = 0; bit < 8; bit++) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
  }
  return ~crc;
}
