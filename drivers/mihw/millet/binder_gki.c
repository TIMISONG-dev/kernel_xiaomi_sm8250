/*
 * Copyright (c) Xiaomi Technologies Co., Ltd. 2020. All rights reserved.
 *
 * File name: oem_binder.c
 * Description: millet-binder-driver
 * Author: guchao1@xiaomi.com
 * Version: 1.0
 * Date:  2020/9/9
 */
#define pr_fmt(fmt) "millet-binder_gki: " fmt

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/freezer.h>
#include <linux/ktime.h>
#include <linux/hrtimer.h>
#include <linux/proc_fs.h>
#include "millet.h"
#include "binder_oem.h"
#include <trace/hooks/binder.h>
#include <../../android/binder_internal.h>

static struct hlist_head * get_binder_hhead = NULL;
static struct mutex * get_binder_lock = NULL;
static bool bgot = false;

struct oem_binder_hook oem_binder_hook_set = {
	.oem_wahead_thresh = 0,
	.oem_wahead_space = 0,
	.oem_reply_hook = NULL,
	.oem_trans_hook = NULL,
	.oem_wait4_hook = NULL,
	.oem_query_st_hook = NULL,
	.oem_buf_overflow_hook = NULL,
};

/**
 * binder_inner_proc_lock() - Acquire inner lock for given binder_proc
 * @proc:         struct binder_proc to acquire
 *
 * Acquires proc->inner_lock. Used to protect todo lists
 */
#define binder_inner_proc_lock(proc) _binder_inner_proc_lock(proc, __LINE__)
static void
_binder_inner_proc_lock(struct binder_proc *proc, int line)
	__acquires(&proc->inner_lock)
{
	spin_lock(&proc->inner_lock);
}

/**
 * binder_inner_proc_unlock() - Release inner lock for given binder_proc
 * @proc:         struct binder_proc to acquire
 *
 * Release lock acquired via binder_inner_proc_lock()
 */
#define binder_inner_proc_unlock(proc) _binder_inner_proc_unlock(proc, __LINE__)
static void
_binder_inner_proc_unlock(struct binder_proc *proc, int line)
	__releases(&proc->inner_lock)
{
	spin_unlock(&proc->inner_lock);
}

static bool binder_worklist_empty_ilocked(struct list_head *list)
{
	return list_empty(list);
}


static enum BINDER_STAT query_binder_stat(struct binder_proc *proc)
{
	struct rb_node *n = NULL;
	struct binder_thread *thread = NULL;
	int pid, tid, uid = 0;
	enum BINDER_STAT stat;
	struct task_struct *tsk;

	if (!oem_binder_hook_set.oem_query_st_hook)
		return BINDER_IN_IDLE;

	if (proc->tsk)
		uid = task_uid(proc->tsk).val;
	else
		return BINDER_IN_IDLE;
	binder_inner_proc_lock(proc);
	if (proc->tsk && !binder_worklist_empty_ilocked(&proc->todo)) {
		tsk = proc->tsk;
		tid = tsk->pid;
		pid = task_pid_nr(tsk);
		stat = BINDER_PROC_IN_BUSY;
		goto busy;
	}

	for (n = rb_first(&proc->threads); n != NULL; n = rb_next(n)) {
		thread = rb_entry(n, struct binder_thread, rb_node);
		if (!thread->task)
			continue;

		if (!binder_worklist_empty_ilocked(&thread->todo)) {
			tsk = thread->task;
			pid = task_tgid_nr(tsk);
			tid = thread->pid;
			stat = BINDER_THREAD_IN_BUSY;
			goto busy;
		}

		if (!thread->transaction_stack)
			continue;

		spin_lock(&thread->transaction_stack->lock);
		if (thread->transaction_stack->to_thread == thread) {
			tsk = thread->task;
			pid = task_tgid_nr(tsk);
			tid = thread->pid;
			stat = BINDER_IN_TRANSACTION;
			spin_unlock(&thread->transaction_stack->lock);
			goto busy;
		}
		spin_unlock(&thread->transaction_stack->lock);
	}

	binder_inner_proc_unlock(proc);
	return BINDER_IN_IDLE;
busy:
	binder_inner_proc_unlock(proc);
//	oem_binder_hook_set.oem_query_st_hook(uid, tsk, tid, pid, stat);
	return stat;
}

void query_binder_app_stat(int uid)
{
	struct binder_proc *proc;
	bool idle_f = true;
	enum BINDER_STAT stat;
	if (!oem_binder_hook_set.oem_query_st_hook ||
		!get_binder_lock ||!get_binder_hhead)
		return;
	mutex_lock(get_binder_lock);
	hlist_for_each_entry(proc, get_binder_hhead, proc_node) {
		if (proc != NULL && proc->tsk
			&& (task_uid(proc->tsk).val == uid)) {
			if (query_binder_stat(proc) != BINDER_IN_IDLE)
				idle_f = false;
		}
	}

	if (idle_f)
		stat = BINDER_IN_IDLE;
	else
		stat = BINDER_IN_BUSY;

	oem_binder_hook_set.oem_query_st_hook(uid, current, 0, current->pid, stat);
	mutex_unlock(get_binder_lock);
}
EXPORT_SYMBOL_GPL(query_binder_app_stat);


struct task_struct *binder_buff_owner(struct binder_alloc *alloc)
{
	struct binder_proc *proc = NULL;
	if (!alloc)
		return NULL;

	proc = container_of(alloc, struct binder_proc, alloc);
	return proc->tsk;
}



void mi_binder_alloc_new_buf_locked(void * data, size_t size, struct binder_alloc * alloc, int is_async)
{
	if (oem_binder_hook_set.oem_buf_overflow_hook && is_async
		&& ((alloc->free_async_space < oem_binder_hook_set.oem_wahead_thresh
		* (size + sizeof(struct binder_buffer)))
		|| (alloc->free_async_space < oem_binder_hook_set.oem_wahead_space))) {
			struct task_struct *owner;
			owner = binder_buff_owner(alloc);

			if (owner)
				oem_binder_hook_set.oem_buf_overflow_hook(owner, current,
						current->pid, false, 0);
	}
}

void mi_binder_replay(void * data, struct binder_proc * target_proc, struct binder_proc * proc,
	struct binder_thread * thread, struct binder_transaction_data * tr)
{
	if (oem_binder_hook_set.oem_reply_hook && target_proc->tsk)
		oem_binder_hook_set.oem_reply_hook(target_proc->tsk, proc->tsk,
				thread->pid, tr->flags & TF_ONE_WAY,
				tr->code);
}

void mi_binder_transaction(void * data, struct binder_proc * target_proc, struct binder_proc * proc,
	struct binder_thread * thread, struct binder_transaction_data * tr)
{
	if (oem_binder_hook_set.oem_trans_hook && target_proc && target_proc->tsk)
		oem_binder_hook_set.oem_trans_hook(target_proc->tsk, proc->tsk,
				thread->pid, tr->flags & TF_ONE_WAY,
				tr->code);
}

void mi_binder_wait_for_work(void * data, bool do_proc_work, struct binder_thread * thread, struct binder_proc * proc)
{
	struct task_struct *dst;

	if(!thread->transaction_stack)
		return;

	spin_lock(&thread->transaction_stack->lock);
	if (oem_binder_hook_set.oem_wait4_hook
			&& !thread->is_dead
			&& thread->transaction_stack
			&& thread->transaction_stack->to_proc
			&& thread->transaction_stack->to_proc->tsk){
		dst = thread->transaction_stack->to_proc->tsk;
		get_task_struct(dst);
		spin_unlock(&thread->transaction_stack->lock);
	}else{
		spin_unlock(&thread->transaction_stack->lock);
		return;
	}
	oem_binder_hook_set.oem_wait4_hook(dst,
				proc->tsk,
				thread->pid,
				thread->transaction_stack->flags & TF_ONE_WAY,
				thread->transaction_stack->code);
	put_task_struct(dst);
}

void mi_get_hhead_and_lock(void * data, struct hlist_head * hhead, struct mutex * lock)
{
	if(!bgot) {
		 if(hhead)
		 	get_binder_hhead= hhead;
		 if(lock)
		 	get_binder_lock= lock;
		 bgot= true;
	}
}

void oem_register_binder_hook(struct oem_binder_hook *set)
{
	if (!set)
		return;

	oem_binder_hook_set.oem_wahead_thresh = set->oem_wahead_thresh;
	oem_binder_hook_set.oem_wahead_space = set->oem_wahead_space;
	oem_binder_hook_set.oem_reply_hook = set->oem_reply_hook;
	oem_binder_hook_set.oem_trans_hook = set->oem_trans_hook;
	oem_binder_hook_set.oem_wait4_hook = set->oem_wait4_hook;
	oem_binder_hook_set.oem_query_st_hook = set->oem_query_st_hook;
	oem_binder_hook_set.oem_buf_overflow_hook = set->oem_buf_overflow_hook;
}
EXPORT_SYMBOL_GPL(oem_register_binder_hook);


static int __init init_binder_gki(void)
{
	pr_err("enter init_binder_gki func!\n");
	register_trace_android_vh_binder_alloc_new_buf_locked(mi_binder_alloc_new_buf_locked, NULL);
	register_trace_android_vh_binder_reply(mi_binder_replay, NULL);
	register_trace_android_vh_binder_trans(mi_binder_transaction, NULL);
	register_trace_android_vh_binder_wait_for_work(mi_binder_wait_for_work, NULL);
	register_trace_android_vh_binder_preset(mi_get_hhead_and_lock, NULL);

	return 0;
}

module_init(init_binder_gki);

MODULE_LICENSE("GPL");

