# Alioth replacement-battery implementation

This series consolidates the eight battery commits after
8614cd0b4496dec4f9f5f86549f07396b0950289 and applies the voltage/interface
corrections reviewed against beta at 786090340a11b46129a458a59a8ecac732a4be78.
It is source for target validation, not a battery safety certification.

## Identification, learning and cycle history

Recognized FMT, GY and Apollo/J3S packs retain their identified profiles.
An unidentified pack uses the explicit replacement fallback when the board
opts in. Resistance, learned capacity and shared authenticator IDs do not
establish a physical battery model or its age.

CHARGE_FULL_DESIGN remains the selected profile's nominal metadata (4520 mAh
for K11A_custom). CHARGE_FULL reports the stored learned estimate.
The internal SRAM nominal value can differ from the metadata because the
fallback reuses the FMT characterization blob. The replacement policy permits
at most +2.0% per qualified successful learning update and caps the stored
estimate at 5300 mAh. Neither number is a capacity target or an inferred rating.
Learning does not change the battery identity, voltage/current limits or JEITA.

The replacement cycle count is the restored Qualcomm eight-bucket average.
Counts are not reset on profile reload and not replaced with a guessed cell age.
The selected counter source and restoration errors remain visible through
cycle_count_info. Historical board data can predate the installed cell.

## Voltage and interface corrections

* fastcharge_mode and vbatt_full_vol occupy the correct power-supply enum slots
  in sysfs. The enum is not renumbered and the existing fcc_vbatt_full_vol filename
  is retained. A mode read returns a boolean, not a millivolt threshold; mode
  writes no longer dispatch to the voltage-threshold setter.
* The selected qcom,max-voltage-uv is saved separately from runtime FG float
  bookkeeping. VOLTAGE_MAX_DESIGN is unavailable until profile/SOC readiness and
  returns that immutable selected-profile limit thereafter.
* Replacement CC/CV updates preserve the existing 10 mV headroom and cannot raise
  the replacement runtime float value above its profile maximum. The 4.45 V
  fallback uses 4.44 V CC/CV thresholds for both modes. Known-pack runtime
  calibration is retained. Missing/overflowing thresholds return defined errors.
* On mi,respect-bms-voltage-limit boards, PPS applies the lesser of the board/mode
  limit and the selected BMS profile limit. Charge-pump and BMS voltages are read
  on each status poll, and the higher valid value controls upper-voltage/CV
  decisions. Raw telemetry is not relabeled or artificially offset.
* Targets round downward to millivolts; measured values round upward. Failed or
  invalid voltage reads cannot leave a previous polling result eligible for
  pump admission. An already excessive reading prevents pump enable. Failed
  input-suspend or temperature reads also prevent the opted-in pump path.

These are software target and admission controls. ADC offsets, sampling latency
and physical transient overshoot still require hardware validation. Using the
higher reading can reduce charging speed when the two sensors disagree; it is
not evidence that either sensor has been calibrated.

## Unchanged limits and required validation

The source still inherits the existing current and thermal framework, including
6 A profile capability. A replacement cell's capacity/4.45 V label alone does
not establish a 6 A charging specification. No current limit is raised here.
The FMT electrochemical characterization is not a manufacturer characterization
of the aftermarket DEJI or seller-built BM4Y cell.

Before release: build the target kernel and DTBO, boot with SELinux enforcing,
verify mode reads 0/1 and the BMS profile ceiling stays 4450000 uV across FFC
transitions, inspect both voltage sensors near CV, and confirm pump exit/recovery.
Then verify one qualified capacity-learning update and persistent cycle history
across reboot. No full-discharge or high-power experiment should substitute for
resolving unsafe voltage readings or obtaining the replacement cell's ratings.

The supplied Python host tests compile production function bodies using hardware
stubs. They do not compile the complete target kernel or operate a real charger.

## K11A_custom name and learned display label

The replacement profile is now named K11A_custom. Its firmware profile data,
4520 mAh nominal metadata, 4.45 V ceiling, current limits and learning policy
are unchanged. The old DT property names remain supported.

For Alioth with DS28E16 profile identification enabled, matching Xiaomi-format
ROM metadata and vendor byte U select FMT, C/V select GY, and S/X select J3S
when its board flag is enabled. A successful named lookup wins over fallback.
The fallback means unidentified, not proven aftermarket: an original pack
with unreadable or unsupported identification can also use K11A_custom.
Likewise, reused or emulated identification does not certify an original cell.

The stable power-supply battery_type remains K11A_custom. Do not append capacity
to this lookup key: software JEITA selects an exact DT profile by that string.
The separate read-only battery_model attribute on the FG platform device
provides a display label. It uses K11A_custom until the initialized learning
algorithm has accepted at least one qualified update in the current profile
initialization. It then shows e.g. K11A_custom_4572mah using the accepted learned
capacity, never an invented nominal rating. Known FMT/GY/J3S names are unchanged.

This label is not a convergence certificate or a physical battery identity.
Learning continues with use and aging. A restored number alone does not prove
that the current physical cell was measured. The capacity remains persistent,
but qualification of the display suffix is deliberately not persisted: after
reboot/profile reinitialization the label returns to K11A_custom until another
qualified update. No unallocated SDAM slot or guessed per-cell identifier is
used. Failed or skipped updates do not manufacture a suffix.

battery_model is a display/diagnostic attribute, not a changed Android API.
The ROM must explicitly read it to display it. CHARGE_FULL and
CHARGE_FULL_DESIGN retain their separate meanings. Neither the label nor a
larger learned capacity authorizes a different charging voltage or current.
