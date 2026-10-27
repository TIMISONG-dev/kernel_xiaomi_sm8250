// SPDX-License-Identifier: GPL-2.0
#ifndef DM_LINEAR_H
#define DM_LINEAR_H

#include <linux/device-mapper.h>

void dm_linear_dtr(struct dm_target *ti);
int dm_linear_map(struct dm_target *ti, struct bio *bio);
void dm_linear_status(struct dm_target *ti, status_type_t type,
			unsigned status_flags, char *result, unsigned maxlen);
int dm_linear_prepare_ioctl(struct dm_target *ti, struct block_device **bdev);
int dm_linear_iterate_devices(struct dm_target *ti,
			iterate_devices_callout_fn fn, void *data);
int dm_linear_ctr(struct dm_target *ti, unsigned int argc, char **argv);

#endif
