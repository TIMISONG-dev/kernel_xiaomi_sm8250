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
for K11A_REPLACEMENT_SAFE). CHARGE_FULL reports the stored learned estimate.
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
