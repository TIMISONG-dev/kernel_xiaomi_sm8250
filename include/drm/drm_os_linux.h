/* SPDX-License-Identifier: GPL-2.0 */
/**
 * \file drm_os_linux.h
 * OS abstraction macros.
 */

#include <linux/interrupt.h>	/* For task queue support */
#include <linux/sched/signal.h>
#include <linux/delay.h>
#include <linux/io-64-nonatomic-lo-hi.h>

/** Current process ID */
#define DRM_CURRENTPID			task_pid_nr(current)
#define DRM_UDELAY(d)			udelay(d)
/** Read a byte from a MMIO region */
#define DRM_READ8(map, offset)		readb(((map)->iomem) + (offset))
/** Read a word from a MMIO region */
#define DRM_READ16(map, offset)         readw(((map)->iomem) + (offset))
/** Read a dword from a MMIO region */
#define DRM_READ32(map, offset)		readl(((map)->iomem) + (offset))
/** Write a byte into a MMIO region */
#define DRM_WRITE8(map, offset, val)	writeb(val, ((map)->iomem) + (offset))
/** Write a word into a MMIO region */
#define DRM_WRITE16(map, offset, val)   writew(val, ((map)->iomem) + (offset))
/** Write a dword into a MMIO region */
#define DRM_WRITE32(map, offset, val)	writel(val, ((map)->iomem) + (offset))

/** Read a qword from a MMIO region - be careful using these unless you really understand them */
#define DRM_READ64(map, offset)		readq(((map)->iomem) + (offset))
/** Write a qword into a MMIO region */
#define DRM_WRITE64(map, offset, val)	writeq(val, ((map)->iomem) + (offset))

#define DRM_WAIT_ON( ret, queue, timeout, condition )		\
do {								\
	DECLARE_WAITQUEUE(entry, current);			\
	unsigned long end = jiffies + (timeout);		\
	add_wait_queue(&(queue), &entry);			\
								\
	for (;;) {						\
		__set_current_state(TASK_INTERRUPTIBLE);	\
		if (condition)					\
			break;					\
		if (time_after_eq(jiffies, end)) {		\
			ret = -EBUSY;				\
			break;					\
		}						\
		schedule_timeout((HZ/100 > 1) ? HZ/100 : 1);	\
		if (signal_pending(current)) {			\
			ret = -EINTR;				\
			break;					\
		}						\
	}							\
	__set_current_state(TASK_RUNNING);			\
	remove_wait_queue(&(queue), &entry);			\
} while (0)
