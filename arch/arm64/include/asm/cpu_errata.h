/* SPDX-License-Identifier: GPL-2.0 */
#ifndef __ASM_CPU_ERRATA_H
#define __ASM_CPU_ERRATA_H

#include <linux/types.h>
#include <linux/percpu.h>

struct bp_hardening_data;
DECLARE_PER_CPU_READ_MOSTLY(struct bp_hardening_data, bp_hardening_data);
DECLARE_PER_CPU_READ_MOSTLY(u64, arm64_ssbd_callback_required);
#ifdef CONFIG_ARM64_ERRATUM_1463225
DECLARE_PER_CPU(int, __in_cortex_a76_erratum_1463225_wa);
#endif

#endif /* __ASM_CPU_ERRATA_H */
