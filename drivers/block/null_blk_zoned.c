// SPDX-License-Identifier: GPL-2.0
#include <linux/vmalloc.h>
#include <linux/sizes.h>
#include "null_blk.h"

#define MB_TO_SECTS(mb) (((sector_t)mb * SZ_1M) >> SECTOR_SHIFT)

static inline unsigned int null_zone_no(struct nullb_device *dev, sector_t sect)
{
	return sect >> ilog2(dev->zone_size_sects);
}

int null_zone_init(struct nullb_device *dev)
{
	sector_t dev_capacity_sects;
	sector_t sector = 0;
	unsigned int i;

	if (!is_power_of_2(dev->zone_size)) {
		pr_err("null_blk: zone_size must be power-of-two\n");
		return -EINVAL;
	}
	if (dev->zone_size > dev->size) {
		pr_err("Zone size larger than device capacity\n");
		return -EINVAL;
	}

	dev_capacity_sects = MB_TO_SECTS(dev->size);
	dev->zone_size_sects = MB_TO_SECTS(dev->zone_size);
	dev->nr_zones = dev_capacity_sects >> ilog2(dev->zone_size_sects);
	if (dev_capacity_sects & (dev->zone_size_sects - 1))
		dev->nr_zones++;

	dev->zones = kvmalloc_array(dev->nr_zones, sizeof(struct blk_zone),
			GFP_KERNEL | __GFP_ZERO);
	if (!dev->zones)
		return -ENOMEM;

	for (i = 0; i < dev->nr_zones; i++) {
		struct blk_zone *zone = &dev->zones[i];

		zone->start = zone->wp = sector;
		if (zone->start + dev->zone_size_sects > dev_capacity_sects)
			zone->len = dev_capacity_sects - zone->start;
		else
			zone->len = dev->zone_size_sects;
		zone->type = BLK_ZONE_TYPE_SEQWRITE_REQ;
		zone->cond = BLK_ZONE_COND_EMPTY;

		sector += dev->zone_size_sects;
	}

	return 0;
}

void null_zone_exit(struct nullb_device *dev)
{
	kvfree(dev->zones);
	dev->zones = NULL;
}

int null_zone_report(struct gendisk *disk, sector_t sector,
		     struct blk_zone *zones, unsigned int *nr_zones,
		     gfp_t gfp_mask)
{
	struct nullb *nullb = disk->private_data;
	struct nullb_device *dev = nullb->dev;
	unsigned int zno, nrz = 0;

	if (!dev->zoned)
		/* Not a zoned null device */
		return -EOPNOTSUPP;

	zno = null_zone_no(dev, sector);
	if (zno < dev->nr_zones) {
		nrz = min_t(unsigned int, *nr_zones, dev->nr_zones - zno);
		memcpy(zones, &dev->zones[zno], nrz * sizeof(struct blk_zone));
	}

	*nr_zones = nrz;

	return 0;
}

void null_zone_write(struct nullb_cmd *cmd, sector_t sector,
		     unsigned int nr_sectors)
{
	struct nullb_device *dev = cmd->nq->dev;
	unsigned int zno = null_zone_no(dev, sector);
	struct blk_zone *zone = &dev->zones[zno];

	switch (zone->cond) {
	case BLK_ZONE_COND_FULL:
		/* Cannot write to a full zone */
		cmd->error = BLK_STS_IOERR;
		break;
	case BLK_ZONE_COND_EMPTY:
	case BLK_ZONE_COND_IMP_OPEN:
		/* Writes must be at the write pointer position */
		if (sector != zone->wp) {
			cmd->error = BLK_STS_IOERR;
			break;
		}

		if (zone->cond == BLK_ZONE_COND_EMPTY)
			zone->cond = BLK_ZONE_COND_IMP_OPEN;

		zone->wp += nr_sectors;
		if (zone->wp == zone->start + zone->len)
			zone->cond = BLK_ZONE_COND_FULL;
		break;
	default:
		/* Invalid zone condition */
		cmd->error = BLK_STS_IOERR;
		break;
	}
}

void null_zone_reset(struct nullb_cmd *cmd, sector_t sector)
{
	struct nullb_device *dev = cmd->nq->dev;
	unsigned int zno = null_zone_no(dev, sector);
	struct blk_zone *zone = &dev->zones[zno];

	zone->cond = BLK_ZONE_COND_EMPTY;
	zone->wp = zone->start;
}
