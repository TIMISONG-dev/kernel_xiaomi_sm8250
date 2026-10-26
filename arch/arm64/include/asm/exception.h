/*
 * Based on arch/arm/include/asm/exception.h
 *
 * Copyright (C) 2012 ARM Ltd.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */
#ifndef __ASM_EXCEPTION_H
#define __ASM_EXCEPTION_H

#include <asm/esr.h>

#include <linux/interrupt.h>

#define __exception	__attribute__((section(".exception.text")))
#ifdef CONFIG_FUNCTION_GRAPH_TRACER
#define __exception_irq_entry	__irq_entry
#else
#define __exception_irq_entry	__exception
#endif

struct pt_regs;

/* C handlers reached from entry.S; keep external assembly linkage. */
asmlinkage void do_sve_acc(unsigned int esr, struct pt_regs *regs);
asmlinkage void do_fpsimd_acc(unsigned int esr, struct pt_regs *regs);
asmlinkage void do_fpsimd_exc(unsigned int esr, struct pt_regs *regs);
asmlinkage void do_mem_abort(unsigned long addr, unsigned int esr, struct pt_regs *regs);
asmlinkage void do_el0_irq_bp_hardening(void);
asmlinkage void do_el0_ia_bp_hardening(unsigned long addr, unsigned int esr, struct pt_regs *regs);
asmlinkage void do_sp_pc_abort(unsigned long addr, unsigned int esr, struct pt_regs *regs);
asmlinkage int do_debug_exception(unsigned long addr_if_watchpoint,
				  unsigned int esr, struct pt_regs *regs);
asmlinkage void do_undefinstr(struct pt_regs *regs);
asmlinkage void do_sysinstr(unsigned int esr, struct pt_regs *regs);
asmlinkage void do_cp15instr(unsigned int esr, struct pt_regs *regs);

asmlinkage void do_notify_resume(struct pt_regs *regs, unsigned long thread_flags);
asmlinkage void bad_mode(struct pt_regs *regs, int reason, unsigned int esr);
asmlinkage void bad_el0_sync(struct pt_regs *regs, int reason, unsigned int esr);
asmlinkage void handle_bad_stack(struct pt_regs *regs);
asmlinkage void do_serror(struct pt_regs *regs, unsigned int esr);
int early_brk64(unsigned long addr, unsigned int esr, struct pt_regs *regs);

static inline u32 disr_to_esr(u64 disr)
{
	unsigned int esr = ESR_ELx_EC_SERROR << ESR_ELx_EC_SHIFT;

	if ((disr & DISR_EL1_IDS) == 0)
		esr |= (disr & DISR_EL1_ESR_MASK);
	else
		esr |= (disr & ESR_ELx_ISS_MASK);

	return esr;
}

#endif	/* __ASM_EXCEPTION_H */
