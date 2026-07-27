// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (C) 2024 Xiaomi Ltd.
 */
#define pr_fmt(fmt) "shrink_async: " fmt

#include <linux/swap.h>
#include <linux/proc_fs.h>
#include <linux/gfp.h>
#include <linux/types.h>
#include <linux/cpufreq.h>
#include <linux/freezer.h>
#include <linux/wait.h>
#include <linux/errno.h>
#include <linux/time.h>
#include <linux/stat.h>
#include <linux/init.h>
#include <linux/sched.h>
#include <linux/sched/loadavg.h>
#include <linux/sched/stat.h>
#include <linux/module.h>
#include "internal.h"

#define SHRINK_SLABD_NAME "kshrink_slabd"

static struct task_struct *shrink_slabd_tsk = NULL;
static bool async_shrink_slabd_setup = false;
static wait_queue_head_t shrink_slabd_wait;

static struct async_slabd_parameter {
	struct mem_cgroup *shrink_slabd_memcg;
	gfp_t shrink_slabd_gfp_mask;
	atomic_t shrink_slabd_runnable;
	int shrink_slabd_nid;
	int priority;
} asp;

static inline bool is_shrink_slabd_task(struct task_struct *tsk)
{
	return (shrink_slabd_tsk->pid == tsk->pid);
}

static bool wakeup_shrink_slabd(gfp_t gfp_mask, int nid,
				 struct mem_cgroup *memcg,
				 int priority)
{
	if (atomic_read(&(asp.shrink_slabd_runnable)) == 1)
		return false;

	asp.shrink_slabd_gfp_mask = gfp_mask;
	asp.shrink_slabd_nid = nid;
	asp.shrink_slabd_memcg = memcg;
	asp.priority = priority;
	atomic_set(&(asp.shrink_slabd_runnable), 1);

	wake_up_interruptible(&shrink_slabd_wait);

	return true;
}

static void set_async_slabd_cpus(void)
{
	struct cpumask mask;
	struct cpumask *cpumask = &mask;
	pg_data_t *pgdat = NODE_DATA(0);
	unsigned int cpu = 0, cpufreq_max_tmp = 0;
	struct cpufreq_policy *policy_max;
	static bool set_slabd_cpus_success = false;

	if (likely(set_slabd_cpus_success))
		return;
	for_each_possible_cpu(cpu) {
		struct cpufreq_policy *policy = cpufreq_cpu_get(cpu);

		if (policy == NULL)
			continue;

		if (policy->cpuinfo.max_freq >= cpufreq_max_tmp) {
			cpufreq_max_tmp = policy->cpuinfo.max_freq;
			policy_max = policy;
		}
	}

	cpumask_copy(cpumask, cpumask_of_node(pgdat->node_id));
	cpumask_andnot(cpumask, cpumask, policy_max->related_cpus);

	if (!cpumask_empty(cpumask)) {
		set_cpus_allowed_ptr(shrink_slabd_tsk, cpumask);
		set_slabd_cpus_success = true;
	}
}

static int kshrink_slabd_func(void *p)
{
	struct mem_cgroup *memcg;
	gfp_t gfp_mask;
	int nid, priority;
	/*
	 * Tell the memory management that we're a "memory allocator",
	 * and that if we need more memory we should get access to it
	 * regardless (see "__alloc_pages()"). "kswapd" should
	 * never get caught in the normal page freeing logic.
	 *
	 * (Kswapd normally doesn't need memory anyway, but sometimes
	 * you need a small amount of memory in order to be able to
	 * page out something else, and this flag essentially protects
	 * us from recursively trying to free more memory as we're
	 * trying to free the first piece of memory in the first place).
	 */
	current->flags |= PF_MEMALLOC | PF_KSWAPD;
	set_freezable();

	asp.shrink_slabd_gfp_mask = 0;
	asp.shrink_slabd_nid = 0;
	asp.shrink_slabd_memcg = NULL;
	atomic_set(&(asp.shrink_slabd_runnable), 0);
	asp.priority = 0;

	while (!kthread_should_stop()) {
		wait_event_freezable(shrink_slabd_wait,
					(atomic_read(&(asp.shrink_slabd_runnable)) == 1));

		set_async_slabd_cpus();

		nid = asp.shrink_slabd_nid;
		gfp_mask = asp.shrink_slabd_gfp_mask;
		priority = asp.priority;
		memcg = asp.shrink_slabd_memcg;
		shrink_slab(gfp_mask, nid, memcg, priority);
		atomic_set(&(asp.shrink_slabd_runnable), 0);
	}
	current->flags &= ~(PF_MEMALLOC | PF_KSWAPD);

	return 0;
}

void should_shrink_async(gfp_t gfp_mask, int nid,
			struct mem_cgroup *memcg, int priority, bool *bypass)
{
	static unsigned long prev_jiffies = 0;
	unsigned long curr_jiffies = 0;
	unsigned long diff_jiffies = 0;

	if (unlikely(!async_shrink_slabd_setup)) {
		*bypass = false;
		return;
	}

	curr_jiffies = jiffies;
	diff_jiffies = curr_jiffies - prev_jiffies;
	prev_jiffies = curr_jiffies;

	if (is_shrink_slabd_task(current) || (diff_jiffies < HZ*1)) {
		*bypass = false;
	} else {
		*bypass = true;
		wakeup_shrink_slabd(gfp_mask, nid, memcg, priority);
	}
}

static int __init shrink_async_init(void)
{
	int ret = 0;

	init_waitqueue_head(&shrink_slabd_wait);

	shrink_slabd_tsk = kthread_run(kshrink_slabd_func, NULL, SHRINK_SLABD_NAME);
	if (IS_ERR_OR_NULL(shrink_slabd_tsk)) {
		pr_err("Failed to start shrink_slabd on node 0\n");
		ret = PTR_ERR(shrink_slabd_tsk);
		shrink_slabd_tsk = NULL;
		return ret;
	}

	async_shrink_slabd_setup = true;
	pr_info("kshrink_async succeed!\n");
	return 0;
}
module_init(shrink_async_init);
