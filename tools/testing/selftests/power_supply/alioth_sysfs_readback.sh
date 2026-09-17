#!/system/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Read-only audit. Root reads do not establish HAL-domain access.
node() {
    p="$1"
    echo "--- $p"
    if [ ! -e "$p" ]; then
        echo 'MISSING (or inaccessible parent)'
        return
    fi
    ls -ldZ "$p" 2>&1
    target=$(readlink -f "$p" 2>/dev/null)
    if [ -n "$target" ] && [ "$target" != "$p" ]; then
        echo "target=$target"
        ls -ldZ "$target" 2>&1
    fi
    cat "$p" 2>&1
    echo "read_exit=$?"
}
echo '=== Build / access context ==='
uname -r
id
getenforce
getprop ro.build.version.release
getprop ro.build.version.sdk
getprop init.svc.vendor.lineage_health
ps -AZ 2>/dev/null | grep -E 'health|thermal|power' || true

echo '=== Battery HAL ABI ==='
for supply in battery bms usb pc_port main; do
    for prop in type scope online present status health capacity capacity_level \
        voltage_now voltage_max voltage_max_design current_now current_avg current_max \
        charge_counter charge_full charge_full_design cycle_count time_to_full_now \
        temp technology input_suspend battery_charging_enabled charge_control_limit \
        charge_control_limit_max fastcharge_mode; do
        case "$supply/$prop" in
            battery/*|bms/type|bms/scope|bms/current_avg|bms/current_now|bms/voltage_max_design|bms/cycle_count|bms/charge_full|bms/charge_full_design|usb/type|usb/online|usb/current_max|usb/voltage_max|usb/voltage_now|usb/fastcharge_mode|pc_port/type|pc_port/scope|pc_port/online|main/health|main/voltage_max)
                node "/sys/class/power_supply/$supply/$prop" ;;
        esac
    done
done
for prop in restrict_chg restrict_cur; do node "/sys/class/qcom-battery/$prop"; done
for f in /sys/bus/platform/devices/*/battery_model \
         /sys/bus/platform/devices/*/capacity_learning \
         /sys/bus/platform/devices/*/cycle_count_info; do
    [ ! -e "$f" ] || node "$f"
done

echo '=== Configured Power HAL interfaces ==='
for cpu in 0 4 7; do
    node "/sys/devices/system/cpu/cpu$cpu/cpufreq/scaling_min_freq"
    node "/sys/devices/system/cpu/cpu$cpu/cpufreq/scaling_max_freq"
    node "/sys/class/devfreq/18590000.qcom,devfreq-l3:qcom,cpu$cpu-cpu-l3-lat/min_freq"
done
for p in min_pwrlevel max_pwrlevel force_rail_on force_clk_on idle_timer; do
    node "/sys/class/kgsl/kgsl-3d0/$p"
done
for dev in soc:qcom,cpu-cpu-llcc-bw soc:qcom,cpu-llcc-ddr-bw; do
    node "/sys/class/devfreq/$dev/min_freq"
    for p in hyst_trigger_count hist_memory hyst_length sample_ms io_percent; do
        node "/sys/class/devfreq/$dev/bw_hwmon/$p"
    done
done
node /sys/touchpanel/double_tap
ls -ldZ /dev/cpu_dma_latency 2>&1 # Metadata only; never write/read the QoS device.

echo '=== Thermal / memory / suspend interfaces ==='
for f in /sys/class/thermal/thermal_zone*/type /sys/class/thermal/thermal_zone*/temp \
         /sys/class/thermal/cooling_device*/type; do
    [ ! -e "$f" ] || node "$f"
done
for f in /proc/pressure/memory /sys/fs/cgroup/cgroup.controllers /sys/power/state; do node "$f"; done

echo '=== Exposed services ==='
service list 2>/dev/null | grep -Ei 'health|thermal|power|suspend' || true
