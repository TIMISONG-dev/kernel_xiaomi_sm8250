#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise production KGSL pool/shrinker functions with a host page model.

This checks accounting, scan bounds and progress, not GPU hardware or FPS.
An optional source argument can select the pre-fix kgsl_pool.c for regression.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]


def extract(text, name, optional=False):
    match = re.search(r"^static\s+(?:unsigned long|unsigned int|int)\s+" +
                      re.escape(name) + r"\([^;]*?\)\s*\{", text, re.M)
    if match is None:
        if optional:
            return ""
        raise ValueError("Missing function: " + name)
    begin = text.index("{", match.start())
    depth = 0
    for end in range(begin, len(text)):
        depth += (text[end] == "{") - (text[end] == "}")
        if not depth:
            return text[match.start():end + 1]
    raise ValueError("Unterminated function: " + name)


PRELUDE = r'''
#include <stdbool.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#define SHRINK_STOP (~0UL)
#define SHRINK_EMPTY (~0UL - 1)
#define ALIGN(x, a) (((x) + (a) - 1) & ~((a) - 1))
#define spin_lock(p) ((void)(p))
#define spin_unlock(p) ((void)(p))
struct kgsl_page_pool {
    unsigned int pool_order;
    int page_count;
    bool allocation_allowed;
    int list_lock;
};
static struct kgsl_page_pool kgsl_pools[4];
static int kgsl_num_pools;
struct shrinker { int unused; };
struct shrink_control { unsigned long nr_to_scan; };
static struct { int mem_work; } kgsl_driver;
static unsigned int work_requests;
static bool deny_progress;
static void kgsl_schedule_work(int *work)
{ (void)work; work_requests++; }
/* Model page removal only; size selection/accounting remain production code. */
static unsigned int _kgsl_pool_shrink(struct kgsl_page_pool *p, int nr)
{
    unsigned int chunks;
    if (nr <= 0 || deny_progress) return 0;
    chunks = (unsigned int)nr >> p->pool_order;
    if (chunks > (unsigned int)p->page_count) chunks = p->page_count;
    p->page_count -= chunks;
    return chunks << p->pool_order;
}
static void reset(void)
{
    kgsl_num_pools = 4;
    kgsl_pools[0] = (struct kgsl_page_pool){0, 8, true, 0};
    kgsl_pools[1] = (struct kgsl_page_pool){1, 4, true, 0};
    kgsl_pools[2] = (struct kgsl_page_pool){4, 2, false, 0};
    kgsl_pools[3] = (struct kgsl_page_pool){8, 1, false, 0};
    work_requests = 0;
    deny_progress = false;
}
#define CHECK(x) do { if (!(x)) { \
    fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); return 1; \
} } while (0)
'''

TEST = r'''
int main(void)
{
    struct shrinker s = {0};
    struct shrink_control sc = {6};
    reset();
    CHECK(kgsl_pool_size_total() == 304);
    CHECK(kgsl_pool_shrink_count_objects(&s, &sc) == 16);
    CHECK(work_requests == 1);
    CHECK(kgsl_pool_shrink_scan_objects(&s, &sc) == 6);
    CHECK(kgsl_pool_shrink_count_objects(&s, &sc) == 10);
    CHECK(kgsl_pools[2].page_count == 2 && kgsl_pools[3].page_count == 1);

    sc.nr_to_scan = ULONG_MAX;
    CHECK(kgsl_pool_shrink_scan_objects(&s, &sc) == 10);
    CHECK(kgsl_pool_size_total() == 288);
    CHECK(kgsl_pool_shrink_count_objects(&s, &sc) == SHRINK_EMPTY);
    CHECK(kgsl_pool_shrink_scan_objects(&s, &sc) == SHRINK_STOP);

    reset();
    CHECK(kgsl_pool_shrink_count_objects(&s, &sc) == 16);
    kgsl_pools[0].page_count = kgsl_pools[1].page_count = 0;
    CHECK(kgsl_pool_shrink_scan_objects(&s, &sc) == SHRINK_STOP);

    reset();
    deny_progress = true;
    CHECK(kgsl_pool_shrink_scan_objects(&s, &sc) == SHRINK_STOP);
    CHECK(kgsl_pool_shrink_count_objects(&s, &sc) == 16);

    reset();
    sc.nr_to_scan = 1;
    CHECK(kgsl_pool_shrink_scan_objects(&s, &sc) == 2);
    CHECK(kgsl_pool_shrink_count_objects(&s, &sc) == 14);

    reset();
    sc.nr_to_scan = 0;
    CHECK(kgsl_pool_shrink_scan_objects(&s, &sc) == SHRINK_STOP);
    CHECK(kgsl_pool_size_total() == 304);
    CHECK(kgsl_pool_reduce(0, true) == 304);
    CHECK(kgsl_pool_size_total() == 0);
    CHECK(kgsl_pool_shrink_count_objects(&s, &sc) == SHRINK_EMPTY);
    CHECK(work_requests == 1);

    reset();
    kgsl_num_pools = 0;
    CHECK(kgsl_pool_shrink_count_objects(&s, &sc) == SHRINK_EMPTY);

#if ULONG_MAX > UINT_MAX
    reset();
    sc.nr_to_scan = (1UL << 32) + 1;
    CHECK(kgsl_pool_shrink_scan_objects(&s, &sc) == 16);
#endif
    puts("PASS: reclaimable count, reserves, empty/raced pools, scan bounds and teardown");
    return 0;
}
'''


def main():
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        ROOT / "drivers/gpu/msm/kgsl_pool.c")
    text = source.read_text()
    functions = [extract(text, "kgsl_pool_size"),
                 extract(text, "kgsl_pool_size_total"),
                 extract(text, "kgsl_pool_size_reclaimable", optional=True),
                 extract(text, "kgsl_pool_reduce"),
                 extract(text, "kgsl_pool_shrink_scan_objects"),
                 extract(text, "kgsl_pool_shrink_count_objects")]
    compiler = os.environ.get("CC", "cc")
    with tempfile.TemporaryDirectory(prefix="kgsl-pool-") as tmp:
        cfile = Path(tmp) / "test.c"
        binary = Path(tmp) / "test"
        cfile.write_text(PRELUDE + "\n".join(functions) + TEST)
        subprocess.run([compiler, "-std=gnu11", "-O2", "-Wall", "-Wextra",
                        "-Werror", "-Wno-unused-parameter", str(cfile),
                        "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
