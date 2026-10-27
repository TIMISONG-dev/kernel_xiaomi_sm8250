/* SPDX-License-Identifier: GPL-2.0+ */
/*
 * Read-Copy Update mechanism for mutual exclusion, adapted for tracing.
 *
 * Copyright (C) 2020 Paul E. McKenney.
 */

#ifndef __LINUX_RCUPDATE_TRACE_H
#define __LINUX_RCUPDATE_TRACE_H

#include <linux/sched.h>
#include <linux/rcupdate.h>

#ifdef CONFIG_DEBUG_LOCK_ALLOC

extern struct lockdep_map rcu_trace_lock_map;

static inline int rcu_read_lock_trace_held(void)
{
	return lock_is_held(&rcu_trace_lock_map);
}

#else /* #ifdef CONFIG_DEBUG_LOCK_ALLOC */

static inline int rcu_read_lock_trace_held(void)
{
	return 1;
}

#endif /* #else #ifdef CONFIG_DEBUG_LOCK_ALLOC */

#ifdef CONFIG_TASKS_TRACE_RCU

void rcu_read_unlock_trace_special(struct task_struct *t, int nesting);

/**
 * rcu_read_lock_trace - mark beginning of RCU-trace read-side critical section
 *
 * A Tasks Trace RCU read-side critical section begins with this function
 * and ends with the matching rcu_read_unlock_trace(). Nesting is allowed,
 * and a task may block within a section.
 *
 * synchronize_rcu_tasks_trace() waits for all pre-existing Tasks Trace RCU
 * read-side critical sections to complete. call_rcu_trace() defers
 * its callback until after a Tasks Trace RCU grace period. Context switches
 * do not end these read-side critical sections.
 *
 * For more details, please see the documentation for rcu_read_lock().
 */
static inline void rcu_read_lock_trace(void)
{
	struct task_struct *t = current;

	WRITE_ONCE(t->trc_reader_nesting, READ_ONCE(t->trc_reader_nesting) + 1);
	barrier();
	if (IS_ENABLED(CONFIG_TASKS_TRACE_RCU_READ_MB) &&
	    t->trc_reader_special.b.need_mb)
		smp_mb(); // Pairs with update-side barriers
	rcu_lock_acquire(&rcu_trace_lock_map);
}

/**
 * rcu_read_unlock_trace - mark end of RCU-trace read-side critical section
 *
 * Pairs with a preceding call to rcu_read_lock_trace(), and nesting is
 * allowed.  Invoking a rcu_read_unlock_trace() when there is no matching
 * rcu_read_lock_trace() is verboten, and will result in lockdep complaints.
 *
 * For more details, please see the documentation for rcu_read_unlock().
 */
static inline void rcu_read_unlock_trace(void)
{
	int nesting;
	struct task_struct *t = current;

	rcu_lock_release(&rcu_trace_lock_map);
	nesting = READ_ONCE(t->trc_reader_nesting) - 1;
	barrier(); // Critical section before disabling.
	// Disable IPI-based setting of .need_qs.
	WRITE_ONCE(t->trc_reader_nesting, INT_MIN);
	if (likely(!READ_ONCE(t->trc_reader_special.s)) || nesting) {
		WRITE_ONCE(t->trc_reader_nesting, nesting);
		return;  // We assume shallow reader nesting.
	}
	rcu_read_unlock_trace_special(t, nesting);
}

void call_rcu_tasks_trace(struct rcu_head *rhp, rcu_callback_t func);
void synchronize_rcu_tasks_trace(void);
void rcu_barrier_tasks_trace(void);

#endif /* #ifdef CONFIG_TASKS_TRACE_RCU */

#endif /* __LINUX_RCUPDATE_TRACE_H */
