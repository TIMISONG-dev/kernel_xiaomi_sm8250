#define pr_fmt(fmt) "millet-oem_cgroup: " fmt

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/freezer.h>
#include <linux/signal.h>
#include <linux/cgroup.h>
#include <../../kernel/cgroup/cgroup-internal.h>
#include "millet.h"
#include "cgrp_oem.h"

extern struct cgroup_subsys freezer_cgrp_subsys;
extern struct task_struct *cgroup_taskset_first(struct cgroup_taskset *tset,
					 struct cgroup_subsys_state **dst_cssp);
extern struct task_struct *cgroup_taskset_next(struct cgroup_taskset *tset,
					struct cgroup_subsys_state **dst_cssp);
struct cgroup_subsys oem_cgroup_hook[CGRP_SUBSYS_NUM];

int millet_can_attach(struct cgroup_taskset *tset)
{
	const struct cred *cred = current_cred(), *tcred;
	struct task_struct *task;
	struct cgroup_subsys_state *css;

	cgroup_taskset_for_each(task, css, tset)
	{
		tcred = __task_cred(task);

		if ((current != task) &&
		    !(cred->euid.val == 1000
			    || capable(CAP_SYS_ADMIN))) {
			pr_err("Permission problem\n");
			return 1; // >0 means can't attach
		}
	}

	return 0;
}

static int freezer_can_attach(struct cgroup_taskset *tset)
{
	if (oem_cgroup_hook[FREERE_SUBSYS].can_attach)
		return oem_cgroup_hook[FREERE_SUBSYS].can_attach(tset);

	return 0;
}

static void freezer_cancel_attach(struct cgroup_taskset *tset)
{
	if (oem_cgroup_hook[FREERE_SUBSYS].cancel_attach)
		return oem_cgroup_hook[FREERE_SUBSYS].cancel_attach(tset);
}


static __init int oem_cgrp_init(void)
{
	pr_err("enter oem_cgrp_init func!\n");
	oem_cgroup_hook[FREERE_SUBSYS].can_attach = millet_can_attach;
	freezer_cgrp_subsys.can_attach = freezer_can_attach;
	freezer_cgrp_subsys.cancel_attach = freezer_cancel_attach;

	return 0;
}

module_init(oem_cgrp_init);

MODULE_LICENSE("GPL");

