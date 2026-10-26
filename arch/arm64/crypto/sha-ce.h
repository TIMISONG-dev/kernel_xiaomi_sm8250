/* SPDX-License-Identifier: GPL-2.0 */
#ifndef __ARM64_CRYPTO_SHA_CE_H
#define __ARM64_CRYPTO_SHA_CE_H

#include <linux/types.h>

/* State offsets referenced by the SHA Crypto Extensions assembly. */
extern const u32 sha1_ce_offsetof_count;
extern const u32 sha1_ce_offsetof_finalize;
extern const u32 sha256_ce_offsetof_count;
extern const u32 sha256_ce_offsetof_finalize;

#endif /* __ARM64_CRYPTO_SHA_CE_H */
