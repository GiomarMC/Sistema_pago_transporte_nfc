/* Añadido para este proyecto (no es parte de micro-ecc).
 * La biblioteca Bluetooth del núcleo ESP32 (libbt.a) trae TinyCrypt, que exporta funciones
 * con los mismos nombres (uECC_verify, uECC_valid_public_key...) pero con otro
 * comportamiento. Según el orden de enlace se usaba una u otra y la verificación fallaba al
 * azar. Con este prefijo los símbolos de esta copia no chocan con los del núcleo. */
#ifndef _UECC_PREFIJO_H_
#define _UECC_PREFIJO_H_

#define uECC_compress sp_uECC_compress
#define uECC_compute_public_key sp_uECC_compute_public_key
#define uECC_curve_private_key_size sp_uECC_curve_private_key_size
#define uECC_curve_public_key_size sp_uECC_curve_public_key_size
#define uECC_decompress sp_uECC_decompress
#define uECC_get_rng sp_uECC_get_rng
#define uECC_make_key sp_uECC_make_key
#define uECC_secp160r1 sp_uECC_secp160r1
#define uECC_secp192r1 sp_uECC_secp192r1
#define uECC_secp224r1 sp_uECC_secp224r1
#define uECC_secp256k1 sp_uECC_secp256k1
#define uECC_secp256r1 sp_uECC_secp256r1
#define uECC_set_rng sp_uECC_set_rng
#define uECC_shared_secret sp_uECC_shared_secret
#define uECC_sign sp_uECC_sign
#define uECC_sign_deterministic sp_uECC_sign_deterministic
#define uECC_valid_point sp_uECC_valid_point
#define uECC_valid_public_key sp_uECC_valid_public_key
#define uECC_verify sp_uECC_verify

#endif
