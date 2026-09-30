// SPDX-License-Identifier: GPL-2.0
/*
 * Copyright (C) 2024-2025 Sultan Alsawaf <sultan@kerneltoast.com>.
 */

#include <linux/cpufreq.h>
#include <linux/perf_event.h>
#include <linux/reboot.h>
#include <linux/sched/topology.h>
#include <linux/units.h>
#include <asm/arch_timer.h>
#include <asm/cputype.h>
#include <asm/perf_event.h>
#include <trace/hooks/cpuidle.h>
#include "sched.h"

/*
 * The minimum sample time required to measure the performance counters. This
 * should take into account the resolution of the system timer. At Qualcomm's
 * timer rate of 19200000 Hz, a sample window of 3 us provides an error margin
 * of ~1.7%.
 */
static u64 cpu_min_sample_cntpct __read_mostly = 3 * NSEC_PER_USEC;

/* Max frequencies for SM8550 (kHz) */
static const u64 max_freqs[] = {
	2016000, 2016000, 2016000,	/* Cores 0-2 (Silver/LITTLE) */
	2803200, 2803200,		/* Cores 3-4 (Gold/Big) */
	2803200, 2803200,		/* Cores 5-6 (Gold+/Big) */
	3187200				/* Core 7 (Prime) */
};

/*
 * CNTPCT_EL0 arithmetic helpers to avoid overflowing a u64 when converting
 * between ticks and nanoseconds. This avoids needing mult_frac() in a hot path.
 */
static u64 cntpct_mult __read_mostly;
static u64 cntpct_div __read_mostly;

static u64 cntpct_to_ns(u64 cntpct)
{
	return cntpct * cntpct_mult / cntpct_div;
}

static u64 ns_to_cntpct(u64 ns)
{
	return DIV_ROUND_UP_ULL(ns * cntpct_div, cntpct_mult);
}

static void calc_cntpct_arith(void)
{
	int cd;

	/*
	 * Calculate lossless arithmetic to convert between timer ticks and
	 * nanoseconds, extracting all common denominators up through 10.
	 */
	cntpct_mult = NSEC_PER_SEC;
	cntpct_div = arch_timer_get_rate();
	for (cd = 10; cd > 1; cd--) {
		while (!(cntpct_mult % cd) && !(cntpct_div % cd)) {
			cntpct_div /= cd;
			cntpct_mult /= cd;
		}
	}

	/* Compute all nanosecond time intervals in terms of CNTPCT_EL0 ticks */
	cpu_min_sample_cntpct = ns_to_cntpct(cpu_min_sample_cntpct);
}

/* The PMU/AMU event stats. Order is assumed by the *pmu_read() functions. */
struct pmu_stat {
	u64 cntpct;
	u64 const_cyc;
	u64 cpu_cyc;
};

struct cpu_pmu {
	/*
	 * The cur_ptr array is passed as an argument to cmpxchg_double_local(),
	 * which is implemented with the CASP instruction on AAarch64.
	 *
	 * When FEAT_LSE2 isn't implemented, "The CASP instructions require
	 * alignment to the total size of the memory being accessed."
	 * - 'ARM DDI 0487K.a C3.2.12.4 ("Compare and Swap")'
	 *
	 * When FEAT_LSE2 is implemented, "If all the bytes of the memory access
	 * lie within a 16-byte quantity aligned to 16 bytes and are to Normal
	 * Inner Write-Back, Outer Write-Back Cacheable memory, an unaligned
	 * access is performed." Otherwise, an alignment fault may be generated.
	 * - 'ARM DDI 0487K.a B2.13.2.1.2 ("Load-Exclusive/ Store-Exclusive and
	 *    Atomic instructions")'
	 *
	 * Therefore, the 16-byte cur_ptr array must be aligned to 16 bytes,
	 * since it's a hard requirement for !FEAT_LSE2, and since it's needed
	 * for FEAT_LSE2 in order to avoid generating alignment faults which are
	 * fatal in illegal contexts such as the cpuidle callbacks.
	 */
	struct pmu_stat *cur_ptr[2] __aligned(16);
	struct pmu_stat cur[2];
	struct sfd_data {
		raw_spinlock_t lock;
		u64 cpu_cyc;
		u64 const_cyc;
		bool stale;
	} sfd; /* Scale Frequency Data */
};

static DEFINE_PER_CPU(struct cpu_pmu, cpu_pmu_evs) = {
	.sfd.lock = __RAW_SPIN_LOCK_UNLOCKED(cpu_pmu_evs.sfd.lock)
};

static DEFINE_PER_CPU_READ_MOSTLY(bool, cpu_has_amu);
static DEFINE_PER_CPU_READ_MOSTLY(bool, cpu_has_amu_const);
static DEFINE_STATIC_KEY_FALSE(fie_ready);
static int cpuhp_state;

enum pmu_events {
	CPU_CYCLES,
	PMU_EVT_MAX
};

static const u32 pmu_evt_id[PMU_EVT_MAX] = {
	[CPU_CYCLES] = ARMV8_PMUV3_PERFCTR_CPU_CYCLES
};

struct cpu_pmu_evt {
	struct perf_event *pev[PMU_EVT_MAX];
};

static DEFINE_PER_CPU(struct cpu_pmu_evt, pevt_pcpu);

static __always_inline bool cpu_supports_amu_const(int cpu)
{
	return per_cpu(cpu_has_amu_const, cpu);
}

static struct perf_event *create_pev(struct perf_event_attr *attr, int cpu)
{
	return perf_event_create_kernel_counter(attr, cpu, NULL, NULL, NULL);
}

static void release_perf_events(int cpu)
{
	struct cpu_pmu_evt *cpev = &per_cpu(pevt_pcpu, cpu);
	int i;

	for (i = 0; i < PMU_EVT_MAX; i++) {
		if (IS_ERR(cpev->pev[i]))
			break;

		perf_event_release_kernel(cpev->pev[i]);
	}
}

static int create_perf_events(int cpu)
{
	struct cpu_pmu_evt *cpev = &per_cpu(pevt_pcpu, cpu);
	struct perf_event_attr attr = {
		.type = PERF_TYPE_RAW,
		.size = sizeof(attr),
		.pinned = 1,
		/*
		 * Request a long counter (i.e., 64-bit instead of 32-bit) by
		 * setting bit 0 in config1. See armv8pmu_event_is_64bit().
		 */
		.config1 = 0x1
	};
	int i;

	for (i = 0; i < PMU_EVT_MAX; i++) {
		attr.config = pmu_evt_id[i];
		cpev->pev[i] = create_pev(&attr, cpu);
		if (WARN_ON(IS_ERR(cpev->pev[i])))
			goto release_pevs;
	}

	return 0;

release_pevs:
	release_perf_events(cpu);
	return PTR_ERR(cpev->pev[i]);
}

/*
 * These are the optimized functions for reading the PMU/AMU event counters for
 * each supported SoC.
 *
 * The generic timer counter (CNTPCT_EL0) is read directly for the lowest
 * possible latency incurred from reading the current time, as well as the
 * greatest precision since we can convert the number of ticks into nanoseconds
 * without sched_clock()'s approximation that aims to do the conversion as
 * quickly as possible at a loss of precision. The preceeding ISB prevents
 * speculative reads of the counter register, though is unnecessary when
 * CNTPCTSS_EL0 is available (which is indicated at runtime via ARM64_HAS_ECV).
 *
 * Next, the constant cycles counter is read. On CPUs which don't support the
 * AMU constant cycles event, the value from the generic timer counter is used
 * instead. The AMU constant cycles event is useful because it always stops
 * incrementing when the CPU is in WFE/WFI, which can be entered from a number
 * of places in the kernel besides cpuidle, such as __delay(). CPUs which don't
 * support AMU constant cycles only account for WFI from cpuidle; as a result,
 * the CPU frequency calculation for such CPUs may be lower than expected. This
 * is because of unaccounted WFE/WFI activity, since the generic timer counter
 * doesn't stop incrementing in WFE/WFI. This isn't typically a huge problem,
 * but it's worth noting.
 *
 * Then, the CPU cycles are read from AMU event counters on CPUs which support
 * AMU. On SoCs where this isn't the case, the value is read directly from the
 * PMU cycle count register (PMCCNTR_EL0) configured by the PMUv3 driver. This
 * is done directly in assembly rather than using the perf event API in order to
 * achieve the best possible accuracy, since any delays between the counter
 * reads injects inaccuracy into later calculations performed on these values.
 *
 * A succeeding ISB ensures all counter register reads are complete before the
 * CPU proceeds. Any instruction reordering within the ISBs is of negligible
 * consequence, so no barriers are used in between the counter reads.
 *
 * These functions must be noinline in order to force the compiler to align them
 * to an L1 cache line. The goal is to fit all of the MRS instructions in each
 * function into the same cache line, to avoid timing discrepancies between one
 * MRS and another. A stall due to a cache line fill to get the next MRS can
 * influence calculations using these readings; e.g., if the current time is
 * read first and then the current number of CPU cycles is read next after a
 * stall due to fetching the instruction from beyond L1, the resulting CPU
 * frequency calculation from these figures would produce a higher result than
 * expected.
 *
 * None of these functions have stack-allocated variables. Therefore, the
 * prologue of each function may consist of up to two instructions: BTI and
 * PACIASP, from CONFIG_ARM64_BTI_KERNEL and CONFIG_ARM64_PTR_AUTH_KERNEL,
 * respectively. This leaves room for six instructions to be guaranteed to fit
 * within the same cache line as the prologue, which is enough to cover all MRS
 * instruction sequences for every SoC, including ISBs.
 *
 * The resulting counter values are stored into a `struct pmu_stat` using a
 * compound literal to encourage the compiler to reorder or coalesce the stores
 * as it sees fit, without being constrained by any explicit store ordering
 * declared at compile time (e.g., `stat->foo = foo; stat->bar = bar; ...`).
 */
static noinline void __aligned(L1_CACHE_BYTES)
fie_amu_const_read(struct pmu_stat *stat)
{
	register u64 cntpct, const_cyc, cpu_cyc;

	asm volatile("isb\n\t"
		     "mrs %0, cntpct_el0\n\t"
		     "mrs %1, amevcntr01_el0\n\t"
		     "mrs %2, amevcntr00_el0\n\t"
		     "isb"
		     : "=r" (cntpct), "=r" (const_cyc), "=r" (cpu_cyc));

	*stat = (typeof(*stat)){ cntpct, const_cyc, cpu_cyc };
}

static noinline void __aligned(L1_CACHE_BYTES)
fie_amu_read(struct pmu_stat *stat)
{
	register u64 cntpct, cpu_cyc;

	asm volatile("isb\n\t"
		     "mrs %0, cntpct_el0\n\t"
		     "mrs %1, amevcntr00_el0\n\t"
		     "isb"
		     : "=r" (cntpct), "=r" (cpu_cyc));

	*stat = (typeof(*stat)){ cntpct, cntpct, cpu_cyc };
}

static noinline void __aligned(L1_CACHE_BYTES)
fie_pmu_read(struct pmu_stat *stat)
{
	register u64 cntpct, cpu_cyc;

	asm volatile("isb\n\t"
		     "mrs %0, cntpct_el0\n\t"
		     "mrs %1, pmccntr_el0\n\t"
		     "isb"
		     : "=r" (cntpct), "=r" (cpu_cyc));

	*stat = (typeof(*stat)){ cntpct, cntpct, cpu_cyc };
}

static void pmu_get_stats(struct pmu_stat *stat)
{
	int cpu = raw_smp_processor_id();

	/* Read the stats using the matching assembly function */
	if (likely(per_cpu(cpu_has_amu, cpu))) {
		if (cpu_supports_amu_const(cpu))
			fie_amu_const_read(stat);
		else
			fie_amu_read(stat);
	} else {
		fie_pmu_read(stat);
	}
}

/*
 * ARM DDI 0487K.a B2.2.1 ("Requirements for single-copy atomicity"):
 * "Reads that are generated by a Load Pair instruction that loads two
 *  general-purpose registers and are aligned to the size of the load to each
 *  register are treated as two single-copy atomic reads, one for each register
 *  being loaded."
 *
 * So using LDP to read the old values for the cmpxchg_double() loop guarantees
 * that they are _individually_ read without data races. This is *not* the same
 * as one atomic 128-bit load, but rather two atomic 64-bit loads, so it is
 * possible to observe an impossible combination of the two 64-bit quantities.
 */
#define pmu_read_cur_ptrs(pmu, val1, val2) \
	asm volatile("ldp %[v1], %[v2], %[v]"				\
		     : [v1] "=r" (val1), [v2] "=r" (val2)		\
		     : [v] "Q" (*(__uint128_t *)pmu->cur_ptr))

/*
 * Helpers to locklessly grab a pmu_stat pointer and put it back, with or
 * without having updated it. This is implemented by keeping pointers saved for
 * two pmu_stat structs and moving them around with a 128-bit cmpxchg, since
 * there is only one producer (a specific CPU) and one consumer, so at most only
 * two pmu_stat copies are needed at any given moment.
 *
 * The goal is to always consume the newest pmu_stat data without blocking CPUs
 * from producing new pmu_stat data. Hence the lockless scheme using
 * cmpxchg_double_local(), which is just cmpxchg_double() without any explicit
 * memory ordering semantics.
 *
 * When a CPU updates the pmu_stat with fresh data, a pointer to the newest
 * pmu_stat is kept around to be read locklessly.
 *
 * pmu_get_cur_writer() gets the older pmu_stat (pmu->cur_ptr[1]), so that it
 * can be overwritten with new data. If pmu->cur_ptr[1] is NULL then that means
 * a reader is still busy reading the last pmu_stat and hasn't touched the
 * newest pmu_stat that was generated. When this happens, the newest pmu_stat is
 * returned so that it can be overwritten with even newer data.
 *
 * pmu_put_cur_writer() always puts the writer's pmu_stat back into the newest
 * pmu_stat slot, since it always has the newest data.
 */
#define __PMU_CMPXCHG_DBL_LOOP(new1_expr, new2_expr) \
	struct pmu_stat *old1, *old2, *new1, *new2;			\
	do {								\
		pmu_read_cur_ptrs(pmu, old1, old2);			\
		new1 = (new1_expr), new2 = (new2_expr);			\
	} while (!cmpxchg_double_local(&pmu->cur_ptr[0],		\
				       &pmu->cur_ptr[1],		\
				       old1, old2, new1, new2))
#define pmu_get_cur_writer(pmu) \
({									\
	__PMU_CMPXCHG_DBL_LOOP(old2 ? old1 : NULL, NULL);		\
	old2 ? old2 : old1;						\
})
#define pmu_put_cur_writer(pmu, cur) \
({									\
	__PMU_CMPXCHG_DBL_LOOP(cur, old1 ? old1 : old2);		\
})

static void pmu_update_stats(int cpu, struct cpu_pmu *pmu,
			     struct pmu_stat *cur, struct pmu_stat *prev)
{
	struct pmu_stat *cur_ptr;

	if (prev) {
		struct pmu_stat *ptr1, *ptr2;

		/*
		 * Since we only need to read the latest stats, and we are the
		 * only producer of new stats, no synchronization is needed. But
		 * the latest stat pointer may have been taken by a reader, in
		 * which case we can still find it knowing that it's not the
		 * pointer stored in ptr2.
		 */
		pmu_read_cur_ptrs(pmu, ptr1, ptr2);
		if (!ptr1)
			ptr1 = &pmu->cur[!(ptr2 - &pmu->cur[0])];
		*prev = *ptr1;
	}

	pmu_get_stats(cur);

	/* Publish the updated stats */
	cur_ptr = pmu_get_cur_writer(pmu);
	*cur_ptr = *cur;
	pmu_put_cur_writer(pmu, cur_ptr);
}

/* The sfd helpers must be called with sfd->lock held */
static void reset_sfd_data(struct sfd_data *sfd)
{
	sfd->cpu_cyc = sfd->const_cyc = sfd->stale = 0;
}

static void add_sfd_data(struct sfd_data *sfd, const struct pmu_stat *cur,
			 const struct pmu_stat *prev)
{
	u64 delta_const_cyc = cur->const_cyc - prev->const_cyc;

	/*
	 * Check the delta since the last reading and ditch any stale readings
	 * if this sample window is sufficiently large.
	 */
	if (sfd->stale && delta_const_cyc >= cpu_min_sample_cntpct)
		reset_sfd_data(sfd);

	/* Accumulate data for calculating the CPU's frequency */
	sfd->cpu_cyc += cur->cpu_cyc - prev->cpu_cyc;
	sfd->const_cyc += delta_const_cyc;
}

static void update_freq_scale(int cpu, struct rq *rq, bool local_cpu)
{
	struct cpu_pmu *pmu = &per_cpu(cpu_pmu_evs, cpu);
	struct sfd_data *sfd = &pmu->sfd;
	struct pmu_stat cur, prev;

	if (local_cpu)
		pmu_update_stats(cpu, pmu, &cur, &prev);

	/*
	 * Don't race with remote CPUs which may update the current CPU's
	 * runqueue clock and thus access sfd in parallel, and vice versa.
	 */
	raw_spin_lock(&sfd->lock);
	if (local_cpu)
		add_sfd_data(sfd, &cur, &prev);

	/*
	 * Set the CPU frequency scale measured via counters if enough data is
	 * present for the runqueue that's getting its clock updated (and thus
	 * about to use the frequency scale). This excludes idle time because
	 * although the cycle counter stops incrementing while the CPU idles,
	 * the system timer doesn't.
	 */
	if (rq->cpu == cpu) {
		if (sfd->const_cyc >= cpu_min_sample_cntpct) {
			u64 max_freq = max_freqs[cpu];
			u64 freq, ns = cntpct_to_ns(sfd->const_cyc);

			/* Report the measured frequency and reset the stats */
			freq = min(max_freq, USEC_PER_SEC * sfd->cpu_cyc / ns);
			per_cpu(arch_freq_scale, cpu) =
				SCHED_CAPACITY_SCALE * freq / max_freq;
			reset_sfd_data(sfd);
		} else if (sfd->const_cyc) {
			/*
			 * Track that the sfd statistics now contain stale data,
			 * since the frequency measurement won't perfectly
			 * correlate to the runqueue clock update window
			 * anymore. Keeping stale data for a previous window
			 * technically perpetuates this inaccuracy, but it is
			 * better than being unable to update the CPU frequency
			 * scale due to not having accumulated enough data. The
			 * stale data won't be used if the next window is long
			 * enough to compute the CPU's frequency.
			 */
			sfd->stale = true;
		}
	}
	raw_spin_unlock(&sfd->lock);

	/*
	 * Update the frequency scale data for the remote CPU when the updated
	 * runqueue doesn't belong to this CPU. This recursion is bounded.
	 */
	if (rq->cpu != cpu)
		update_freq_scale(rq->cpu, rq, false);
}

/*
 * Called from update_rq_clock(), just before update_rq_clock_task(). This way,
 * the CPU's frequency scale info has a chance to get updated just before it is
 * used by update_rq_clock_pelt() for computing load.
 */
void fie_update_rq_clock(struct rq *rq)
{
	int cpu = raw_smp_processor_id();

	/* Don't race with reboot or probe, since this isn't a vendor hook */
	if (!static_branch_unlikely(&fie_ready))
		return;

	/* Don't race with CPU hotplug for this CPU or the runqueue's CPU */
	if (unlikely(!cpu_active(cpu) || !cpu_active(rq->cpu)))
		return;

	/*
	 * Update the local CPU's frequency scale info, even if the runqueue in
	 * question doesn't belong to the current CPU. This way, any runqueue
	 * clock updates for remote CPUs will have fresh counter data, for when
	 * the current CPU's runqueue is the one being updated remotely.
	 *
	 * This also handles updating the frequency scale info for the remote
	 * CPU if the runqueue is indeed remote.
	 *
	 * Although the measured CPU frequency is ignored by PELT for the idle
	 * task, measurements are still allowed inside the idle task so that IRQ
	 * load average can still be tracked accurately for interrupts which
	 * fire while the idle task runs. There is otherwise no point to
	 * measuring CPU frequency within the idle task. PELT only cares about
	 * precisely tracking non-idle tasks' runtime, which it does in terms of
	 * time a task consumed relative to CPU frequency, so that the scheduler
	 * can accurately calculate the load of each actual task.
	 */
	update_freq_scale(cpu, rq, true);
}

static void fie_cpu_idle(int cpu, bool idle)
{
	struct cpu_pmu *pmu = &per_cpu(cpu_pmu_evs, cpu);
	struct sfd_data *sfd = &pmu->sfd;
	struct pmu_stat cur, prev;

	/* Don't race with reboot */
	if (!static_branch_unlikely(&fie_ready))
		return;

	/* Don't race with CPU hotplug */
	if (unlikely(!cpu_active(cpu)))
		return;

	if (idle) {
		/* Update the current counters one last time before idling */
		pmu_update_stats(cpu, pmu, &cur, &prev);

		/* Accumulate data for calculating the CPU's frequency */
		raw_spin_lock(&sfd->lock);
		add_sfd_data(sfd, &cur, &prev);
		raw_spin_unlock(&sfd->lock);
	} else {
		/*
		 * For CPUs which don't support AMU const cycles: update the
		 * counters upon exiting idle without accumulating frequency
		 * data, in order to disregard all statistics from the period
		 * when the CPU was idle. This is because the system timer keeps
		 * incrementing while the CPU is idle, while the cycle counter
		 * doesn't because the CPU clock is gated in idle. This isn't a
		 * problem for AMU const cycles because it *does* stop
		 * incrementing while the CPU is idle.
		 */
		if (!cpu_supports_amu_const(cpu))
			pmu_update_stats(cpu, pmu, &cur, NULL);
	}
}

static void fie_idle_enter(void *data, int *state,
			   struct cpuidle_device *dev)
{
	fie_cpu_idle(raw_smp_processor_id(), true);
}

static void fie_idle_exit(void *data, int state,
			  struct cpuidle_device *dev)
{
	fie_cpu_idle(raw_smp_processor_id(), false);
}

static void fie_tick(void)
{
}

static struct scale_freq_data fie_sfd = {
	.source = SCALE_FREQ_SOURCE_ARCH,
	.set_freq_scale = fie_tick
};

static int fie_cpuhp_up(unsigned int cpu)
{
	struct cpu_pmu *pmu = &per_cpu(cpu_pmu_evs, cpu);
	struct sfd_data *sfd = &pmu->sfd;
	bool has_amu;
	int ret;

	/* Detect AMU capabilities at runtime */
#ifdef CONFIG_ARM64_AMU_EXTN
	has_amu = this_cpu_has_cap(ARM64_HAS_AMU_EXTN);
#else
	has_amu = false;
#endif
	per_cpu(cpu_has_amu, cpu) = has_amu;

	/*
	 * Cortex-A510 is affected by ARM erratum 2457168 which causes the AMU
	 * constant cycles counter to stop working after a CPU hotplug.
	 */
	if (has_amu) {
		u32 midr = read_cpuid_id();

		per_cpu(cpu_has_amu_const, cpu) =
			!(MIDR_IMPLEMENTOR(midr) == ARM_CPU_IMP_ARM &&
			  MIDR_PARTNUM(midr) == ARM_CPU_PART_CORTEX_A510);
	} else {
		per_cpu(cpu_has_amu_const, cpu) = false;
	}

	if (!has_amu) {
		ret = create_perf_events(cpu);
		if (ret)
			return ret;
	}

	/* Initialize the pointers to the saved CPU stats */
	pmu->cur_ptr[0] = &pmu->cur[0];
	pmu->cur_ptr[1] = &pmu->cur[1];

	/*
	 * Update and reset the statistics for this CPU as it comes online. No
	 * need to take any locks since `cpu_active(cpu) == false`, so no shared
	 * data can be accessed concurrently with the hotplug handler. Disabling
	 * IRQs when reading the PMU statistics is needed to prevent interrupts
	 * from firing during the measurement and thus skewing the data.
	 */
	local_irq_disable();
	pmu_get_stats(&pmu->cur[0]);
	local_irq_enable();
	reset_sfd_data(sfd);

	topology_set_scale_freq_source(&fie_sfd, cpumask_of(cpu));
	return 0;
}

static int fie_cpuhp_down(unsigned int cpu)
{
	topology_clear_scale_freq_source(SCALE_FREQ_SOURCE_ARCH,
					 cpumask_of(cpu));
	if (!per_cpu(cpu_has_amu, cpu))
		release_perf_events(cpu);
	return 0;
}

static int fie_reboot(struct notifier_block *notifier, unsigned long val,
		      void *cmd)
{
	/*
	 * Disable all hooks and clear scale_freq source to prevent further PMU
	 * register access after this. PMU registers must not be accessed after
	 * kvm_reboot() finishes; attempting to do so will fault.
	 *
	 * This also needs to kick all CPUs to ensure that the scheduler and
	 * cpuidle hooks aren't running anymore. This works because the hooks
	 * themselves are always called from IRQs-disabled context, so when the
	 * IPI kick goes through it means that all in-flight IRQs-disabled
	 * contexts are done executing. Thus, once kick_all_cpus_sync() returns,
	 * it is guaranteed that all hooks which may read PMU registers will
	 * observe `fie_ready == false`.
	 */
	topology_clear_scale_freq_source(SCALE_FREQ_SOURCE_ARCH,
					 cpu_possible_mask);
	static_branch_disable(&fie_ready);
	kick_all_cpus_sync();
	cpuhp_remove_state_nocalls(cpuhp_state);
	return NOTIFY_OK;
}

/* Use the highest priority in order to run before kvm_reboot() */
static struct notifier_block fie_reboot_nb = {
	.notifier_call = fie_reboot,
	.priority = INT_MAX
};

static int __init fie_init(void)
{
	/*
	 * Delete the arch's scale_freq_data callback to get rid of the
	 * duplicated work by the arch's callback, since we read the same
	 * values. This also lets the frequency invariance engine work on cores
	 * that lack the AMU const cycles counter, since we use a workaround for
	 * such CPUs by using cpuidle callbacks to deduct time spent in WFE/WFI,
	 * which is good enough despite not tracking WFE/WFI usage outside of
	 * cpuidle (such as WFE/WFI usage in __delay()).
	 *
	 * A new scale_freq_data callback is installed in fie_cpuhp_up().
	 */
	topology_clear_scale_freq_source(SCALE_FREQ_SOURCE_ARCH,
					 cpu_possible_mask);

	/* Register the CPU hotplug notifier with calls to all online CPUs */
	cpuhp_state = cpuhp_setup_state(CPUHP_AP_ONLINE_DYN, "fie",
					fie_cpuhp_up, fie_cpuhp_down);
	BUG_ON(cpuhp_state <= 0);

	/* Precompute arithmetic to convert between ticks and nanoseconds */
	calc_cntpct_arith();

	/*
	 * Register the cpuidle callback for frequency-invariant counting needed
	 * to set the CPU frequency scale correctly in update_freq_scale().
	 */
	BUG_ON(register_trace_android_vh_cpu_idle_enter(fie_idle_enter, NULL));
	BUG_ON(register_trace_android_vh_cpu_idle_exit(fie_idle_exit, NULL));

	/* Begin updating CPU scheduler statistics from update_rq_clock() */
	static_branch_enable(&fie_ready);

	register_reboot_notifier(&fie_reboot_nb);

	pr_info("FIE: Frequency Invariance Engine initialized\n");
	return 0;
}
late_initcall(fie_init);
