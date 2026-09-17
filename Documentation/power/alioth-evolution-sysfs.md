# Alioth 4.19 / Evolution X cnb sysfs audit

The interface is a contract between the configured HAL, the driver's advertised
properties, the getter/setter implementation, runtime probe success, and DAC /
SELinux permissions. A file in the global power_supply attribute array does not
by itself make it available on every supply. Linux 6.x-only attributes must not
be imitated with fabricated values to silence optional capability probing.

## Corrected in this change

* power_supply_is_system_supplied() now checks the descriptor's property list
  before asking for optional SCOPE and ONLINE. In this downstream property enum,
  these are 67 and 4. BMS, battery and pc_port do not advertise SCOPE; BMS does
  not advertise ONLINE. Previously the core called their callbacks anyway on
  repeated external-power checks, producing the reported warnings.
* No fake BMS online value or device scope is introduced. The pre-existing
  system/device scope, battery-only, real-charger and no-supply desktop policies
  remain intact. Genuine failures of supported driver operations are not hidden.
* SMB5's battery supply now exposes read-only current_avg from the existing BMS
  filtered-current measurement. Evolution X's default BatteryMonitor discovers
  fields on type Battery, whereas the gauge has downstream type BMS. Having
  bms/current_avg alone therefore did not satisfy automatic discovery.

No battery calibration, FCC/FV vote, safety limit, capacity-learning algorithm,
cycle history, profile identity or power_supply enum ordering is changed.

## Interface inventory

The battery descriptor already advertises status, health, present, capacity,
capacity_level, voltage_now, current_now, charge_counter, charge_full,
charge_full_design, cycle_count, temp, technology and time_to_full_now. The new
current_avg forwarding completes the average-current discovery path. Charger
online, current_max and voltage_max are read from USB/pc_port, not invented on
BMS. A registered getter can legitimately report temporarily unavailable data.

The Lineage charging-control service can use battery/input_suspend (0 permits
input, 1 suspends it). battery/charging_enabled is not an advertised SMB5 alias;
do not add a pretend alias just because the HAL probes several alternatives.
The board explicitly declares no charging-bypass support.

Charging speed is configured through /sys/class/qcom-battery/restrict_chg, not
through the gauge's fastcharge_mode. Value 1 enables the configured current
restriction and 0 removes that restriction. restrict_cur gives the restriction
in microamps. Removing a preference restriction does not bypass temperature,
profile, voltage or charger capability constraints.

battery/charge_control_limit is the downstream thermal-mitigation level, not a
charge-percentage cutoff. It must not be wired to an Android 80% limit UI. A
userspace controller should compare capacity and operate its configured toggle.

battery_model, capacity_learning and cycle_count_info are custom read-only FG
platform-device diagnostics. They are not standard BatteryMonitor discovery
paths. Android's model_name field does not automatically consume battery_model.

## Remaining ROM / on-device checks

* Verify ownership and labels of restrict_chg and input_suspend for the actual
  vendor.lineage.health service, which runs as system. Root reading a node or
  seeing the service registered does not prove the service can write it. The
  reviewed init.xiaomi.rc grants input_suspend access on boot; no restrict_chg
  grant was found in the searched common/device tree. Inherited vendor scripts
  may supply it. Confirm live permissions before adding narrowly scoped init /
  SELinux changes. Never use global permissive mode or world-writable charging
  controls as an integration fix.
* Android expects positive battery current while charging, negative while
  discharging, in microamps. This Qualcomm stack uses the opposite raw gauge
  convention in internal charging consumers. The current_avg forwarding keeps
  the established kernel convention. Audit/normalize both current fields at
  the Health HAL boundary rather than globally reversing BMS current, which
  would change charge-pump and learning decisions. Check actual Binder values;
  don't manufacture a sign from the status string during transitions.
* Kernel thermal sensors are present, but the supplied cnb boot log reports no
  connected Thermal HAL. Correct the HAL implementation, sensor mapping and
  VINTF declaration on the ROM side; a dummy sysfs node is not a fix.
* Newer health metadata (manufacturing/first-use dates, serial, model_name,
  state_of_health/health_index, charging_policy/state) is not automatically
  supplied by this older driver. Unknown metadata must remain unsupported.
  bms/soh and learned/design charge ratios are not reliable replacement-cell
  factory identity or a validated health certificate.
* CPU/devfreq/KGSL/touch controls depend on the configured Power HAL paths and
  permissions. Use the runtime readback script; host tests cannot establish
  that every probed device and path exists on a running DTBO.

The new host test compiles production optional-property discovery functions
with stubs and asserts average-current registration / forwarding. Existing
capacity, cycle, display and voltage tests are independent regression checks.
They are not a complete ARM64 kernel build, VTS run or hardware charging test.

## Read-only device collection

From the kernel checkout, with an authorized rooted device:

```sh
adb shell su -c sh < tools/testing/selftests/power_supply/alioth_sysfs_readback.sh > alioth-sysfs-readback.txt
```

The script makes no charging writes and does not change SELinux. Missing
optional files are listed for inspection, not automatically classified as bugs.
