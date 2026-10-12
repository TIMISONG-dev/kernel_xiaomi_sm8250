# Alioth 4.19 maintenance — 2026-10-12

This maintenance pass applies to the aosp-17 branch in
PocoF3Releases/kernel_xiaomi_sm8250. Linux 5.15 bring-up remains archived.
These source changes have not been flashed or runtime tested.

## Fuel gauge fixes

- Validate the fast ESR timer's TIMER_MAX value instead of comparing the
  address of its array; keep the 50-second fallback for invalid intervals.
- Preserve programmed KI coefficients in full, idle and unknown charge
  states instead of using uninitialized locals.
- Check battery authentication property-read status before accepting output.
- Require both warm and overheat exclusions before marking charge_full;
  the previous OR condition was always true.
- Keep ESR differences and accumulation in int until saturation, preserving
  signed SRAM decoding. The previous s16 assignment wrapped before clamping.

## Charging fixes

- Stop fast-charge processing when the temperature read fails, before an
  undefined temperature can affect votes.
- Propagate night-charge read/write failures and update remembered state
  only after successful suspension/resumption operations.
- Return defined errors for unavailable or unsupported wireless backends;
  avoid scaling failed property reads.
- Return a defined success for the existing USB-absent and VBUS GPIO paths;
  propagate USB-online read errors before examining their result.
- Keep the charge-pump missing-provider return inside its intended guard.
- Avoid a NULL charger dereference in the quick-charge error log.
- Correct timer, register-mask and PTR_ERR diagnostics; calculate elapsed
  FFC time before logging it. Preserve thresholds, charging votes and timing.
- Scope translation-unit-only SMB5 helpers and remove dead locals/tables;
  preserve the existing Type-C property read.

## Keyboard driver maintenance

Remove UTF-8 byte-order marks from nanosic source/header files so static
parsers can read them, and make its private wakeup IRQ handler static.
This does not add keyboard hardware support to alioth.

## Evidence and limitations

The configured Qualcomm charging directory and nanosic nano_gpio.o compiled
with W=1, using alioth_defconfig, checkout Android Clang r563880c (Clang 21),
and a separate output directory. Existing ThinLTO/CFI configuration was
retained. No warning suppression was added. This is affected-target
validation, not a full kernel link or ROM build.

The actual ESR arithmetic block passed 5,488 boundary combinations and
three explicit regressions under host GCC with UBSan. Reintroducing the
original 16-bit intermediate types reproduced an assertion failure.
This validates arithmetic, not charging hardware or battery calibration.

Cumulative strict code checkpatch: zero errors and zero checks; 13 warnings
flag pre-existing missing SPDX identifiers exposed by BOM removal. No new
license was invented. Human Signed-off-by certification was not fabricated.
All maintenance commits end with the requested Codex co-author trailer.

Smatch examined the charging sources. Clang 21 builtin-header parsing was
worked around only in the checker wrapper using GCC builtin headers;
production compile flags were unchanged. The checker still reports token
concatenation errors in the vendor ThinLTO initcall macro. A zero checker
exit code is not a clean static-analysis pass. Remaining indentation,
unreachable-code and successful-rc return warnings were not established as
functional defects. The confirmed health, saturation, NULL-dereference and
missing-brace findings were corrected.

No hardware boot, USB/PD, charge-pump, fast-charge, thermal, authentication,
night-charge or suspend behavior has been validated with these new sources.
Before a release, build the complete matched kernel/ROM and perform those
runtime tests. Other device-specific SMB5 variants were not changed; this
pass validates the implementation selected by alioth_defconfig.

Private evidence is preserved under
~/evo/kernel-research/alioth-5.15/logs/419-* and
~/evo/kernel-research/alioth-5.15/out/419-driver-regression/.
Validated code checkpoint: 6767b6eac192fc32dedabe9204e306480b1e5ea3 .
