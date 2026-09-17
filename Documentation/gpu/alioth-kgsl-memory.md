# Alioth KGSL memory review

Reviewed base: `4312eab5f856777b2ddac64730b4c940daa2b945`.
Targets: Alioth/Aliothin, 6/8 GB physical RAM, Adreno 650v3, downstream 4.19,
stock Qualcomm 0502.0 userspace. This is a source review, not a certification
of the proprietary driver or a successful Endfield/hardware stress test.

## Corrected pool reclaim

The pool shrinker counted every cached page, but `kgsl_pool_reduce(...,
false)` deliberately skips pools without `allocation_allowed`. Reserved-only
pools were therefore advertised as reclaimable even when no eligible pages
remained. Count only eligible pool pages, return `SHRINK_EMPTY` for an empty
eligible set, and stop a scan that makes no progress. Keep `nr_to_scan` at its
native unsigned-long width rather than truncating it to signed int.

The change preserves pool locking, deferred-free work, compound-page scan
rounding, reserved pages and driver-close cleanup. It does not increase a
memory quota, reclaim in-use GPU buffers, or guarantee lower frame times.

Run the production-function host regression:

```
CC=gcc python3 tools/testing/selftests/kgsl/pool_reclaim_test.py
CC=clang python3 tools/testing/selftests/kgsl/pool_reclaim_test.py
```

An optional source-path argument tests the old implementation. The old code
fails the mixed reserved/reclaimable count assertion. The test covers mixed
page orders, count/scan races, no progress, zero and wide scan budgets, and
teardown. Its stubbed page model is not a physical multi-gigabyte allocation.

## Existing allocation behavior

- `gpumem_alloc_entry()` and `kgsl_sharedmem_page_alloc_user()` do not impose a
  256/512 MiB per-game cap. The paged allocator uses a dynamically allocated
  `kvcalloc` page-pointer array and scattered physical pages.
- Allocation falls back from unavailable large-page pools to smaller orders,
  including ordinary system order-0 allocation. A higher-order pool being
  exhausted does not mean that the entire buffer must fail.
- The allocator already retries once after flushing deferred memory frees on
  `-ENOMEM`. Partial failures are cleaned up instead of returning a false
  successful allocation.
- Page-pool reserve/cache sizes are not the total amount of RAM a game may
  allocate. Do not enlarge firmware/secure CMA carveouts for ordinary textures.
- Size and cache-range paths retain 32-bit byte limits. The paged allocator's
  largest accepted page-aligned size is `0xfffff000` with 4 KiB pages, before
  address-space, guard-page and physical-memory constraints. This is not a
  promise that a buffer of that size can actually be backed or GPU-mapped.
- The native GPU-only virtual arena is `[0x500000000, 0x600000000)`: 4 GiB per
  applicable pagetable, shared by allocations using that arena. Native SVM has
  a separate `[0x700000000, 0x800000000)` window. These are virtual addresses,
  not 8 GiB of reserved physical RAM, nor universally additive application
  budgets. Compatibility and secure mappings have different ranges.
- The A650 core's 1152 KiB GMEM is on-chip tile memory, not a texture-memory
  limit. Firmware global memory and secure-address limits are separate too.

Neither RAM tier can safely dedicate all physical memory to GPU allocations:
the CPU side of a game, system services, camera/display and kernel also need
backing memory. Userspace Vulkan heap/budget and per-allocation limits remain
relevant; a kernel-only change cannot force the application or stock driver
to accept an otherwise unsupported image layout.

## Deliberately unchanged

No GPU address-window expansion, global buffer-limit removal, new RAM-tier
quota, clocks/voltages, GMEM size, sparse binding, reclaim feature flags,
firmware, or userspace ABI changes. A650 already advertises its 64-bit,
preemption and supported power/cache features. Dormant generic KGSL features
are not evidence that they can safely be enabled for this GPU/firmware/blob
combination. In particular, do not enable shmem/process-reclaim or sparse
features by copying flags from another core.

Existing SVM overflow, bounds, guard-page and mapping protections must stay.
The reported Endfield GPU read translation faults at low addresses 0x140 and
0x240 do not establish exhaustion of the normal high GPU address window.
This pool patch is not claimed to fix those faults.

## Target validation still required

Build the configured ARM64 kernel and test on both RAM tiers with SELinux
enforcing. Repeat the same game scene and compare allocation/map failures,
frame-time distribution, memory PSI, pool usage and post-exit reclamation.
Use the real Vulkan userspace path for controlled large-buffer/image tests;
record requested size, flags, VkResult and KGSL failure stage, and stop well
before system-wide exhaustion. A host test cannot establish how many physical
GiB a particular device, game and driver can allocate concurrently.
