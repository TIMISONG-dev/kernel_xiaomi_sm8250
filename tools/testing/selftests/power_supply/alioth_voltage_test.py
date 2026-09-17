#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Host regression tests using production function bodies, with hardware stubs.

Not an ARM64 kernel build, hardware-in-the-loop test, or ADC calibration.
Run from any directory: python3 tools/testing/selftests/power_supply/alioth_voltage_test.py
"""
import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


def braced(text, opening):
    depth = 0
    tok = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]', re.S)
    for m in tok.finditer(text, opening):
        if m.group() == '{':
            depth += 1
        elif m.group() == '}':
            depth -= 1
            if not depth:
                return m.end()
    raise AssertionError('Unclosed source block')


def function(s, name):
    m = re.search(r'^static\s+(?:int|void|bool)\s+' + re.escape(name)
                  + r'\([^;]*?\)\s*\{', s, re.M)
    assert m, name
    return s[m.start():braced(s, s.index('{', m.start()))]


def compile_run(code, name, definitions=()):
    with tempfile.TemporaryDirectory(prefix='alioth-voltage-test-') as td:
        src, exe = Path(td) / (name + '.c'), Path(td) / name
        src.write_text(code)
        cmd = shlex.split(os.environ.get('HOSTCC', 'cc'))
        cmd += ['-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror',
                '-Wno-unused-parameter', '-fsanitize=undefined',
                '-fno-sanitize-recover=all', *definitions, str(src), '-o', str(exe)]
        subprocess.run(cmd, check=True)
        subprocess.run([str(exe)], check=True)


def check_sysfs(root):
    header = (root / 'include/linux/power_supply.h').read_text()
    sysfs = (root / 'drivers/power/supply/power_supply_sysfs.c').read_text()
    start = header.index('enum power_supply_property {')
    enum = header[start:braced(header, header.index('{', start))] + ';'
    m = re.search(r'static struct device_attribute power_supply_attrs\[\]\s*=\s*\{', sysfs)
    assert m
    end = braced(sysfs, sysfs.index('{', m.start()))
    table = sysfs[m.start():end] + ';'
    cases = {
        'status': 'STATUS', 'cycle_count': 'CYCLE_COUNT',
        'charge_full': 'CHARGE_FULL', 'charge_full_design': 'CHARGE_FULL_DESIGN',
        'cycle_counts': 'CYCLE_COUNTS', 'battery_type': 'BATTERY_TYPE',
        'voltage_max_design': 'VOLTAGE_MAX_DESIGN', 'voltage_now': 'VOLTAGE_NOW',
        'ti_battery_voltage': 'TI_BATTERY_VOLTAGE', 'fastcharge_mode': 'FASTCHARGE_MODE',
        'vbatt_full_vol': 'VBATT_FULL_VOL', 'fcc_vbatt_full_vol': 'FFC_VBATT_FULL_VOL',
        'ffc_iterm': 'FFC_ITERM', 'ki_coeff_current': 'KI_COEFF_CURRENT',
    }
    assertions = '\n'.join(f'assert(index_of("{n}") == POWER_SUPPLY_PROP_{p});'
                           for n, p in cases.items())
    code = '''#include <assert.h>
#include <stdio.h>
#include <string.h>
struct device_attribute { const char *name; };
#define POWER_SUPPLY_ATTR(n) { #n }
''' + enum + '\n' + table + r'''
static int index_of(const char *name) {
    unsigned int i;
    for (i = 0; i < sizeof(power_supply_attrs)/sizeof(power_supply_attrs[0]); ++i)
        if (!strcmp(name, power_supply_attrs[i].name)) return (int)i;
    return -1;
}
/* The real sysfs show/store callbacks both dispatch using this array index. */
static int fastcharge, cc_cv_mv = 4440;
static int get(int p) {
    if (p == POWER_SUPPLY_PROP_FASTCHARGE_MODE) return fastcharge;
    if (p == POWER_SUPPLY_PROP_VBATT_FULL_VOL) return cc_cv_mv;
    return -1;
}
static void set(int p, int value) {
    if (p == POWER_SUPPLY_PROP_FASTCHARGE_MODE) fastcharge = !!value;
    if (p == POWER_SUPPLY_PROP_VBATT_FULL_VOL) cc_cv_mv = value;
}
int main(void) {
    assert(sizeof(power_supply_attrs)/sizeof(power_supply_attrs[0]) ==
           POWER_SUPPLY_PROP_TIME_OT + 1);
''' + assertions + r'''
    assert(get(index_of("fastcharge_mode")) == 0);
    assert(get(index_of("vbatt_full_vol")) == 4440);
    set(index_of("fastcharge_mode"), 1);
    assert(fastcharge == 1 && cc_cv_mv == 4440);
    assert(get(index_of("fastcharge_mode")) == 1);
    puts("PASS: actual sysfs table indices and read/write dispatch");
    return 0;
}
'''
    for defs in ((), ('-DCONFIG_BATT_VERIFY_BY_DS28E16=1',)):
        compile_run(code, 'sysfs', defs)


def check_voltage(root):
    fg = (root / 'drivers/power/supply/qcom/qpnp-fg-gen4.c').read_text()
    pd = (root / 'drivers/power/supply/ti/pd_policy_manager.c').read_text()
    dts = (root / 'arch/arm64/boot/dts/vendor/qcom/fg-gen4-batterydata-alioth-custom.dtsi').read_text()
    def dt(name):
        m = re.search(re.escape(name) + r'\s*=\s*<(\d+)>;', dts)
        assert m, name
        return int(m.group(1))
    maximum = dt('qcom,max-voltage-uv')
    normal = dt('qcom,fg-cc-cv-threshold-mv')
    ffc = dt('qcom,fg-ffc-cc-cv-threshold-mv')
    assert maximum == 4450000
    assert normal * 1000 + 10000 <= maximum
    assert ffc * 1000 + 10000 <= maximum

    getter_case = fg.split('case POWER_SUPPLY_PROP_VOLTAGE_MAX_DESIGN:', 1)[1].split('break;', 1)[0]
    assert 'fg_gen4_get_profile_voltage(chip, &pval->intval)' in getter_case
    assert 'soc_reporting_ready' in getter_case and 'battery_missing' in getter_case
    assert 'WRITE_ONCE(chip->profile_max_voltage_uv, fg->bp.float_volt_uv)' in fg
    setter = function(fg, 'fg_gen4_set_vbatt_full_vol')
    assert not re.search(r'(?:WRITE_ONCE\(chip->profile_max_voltage_uv|profile_max_voltage_uv\s*=)', setter)

    algo = function(pd, 'usbpd_pm_fc2_charge_algo')
    sm = function(pd, 'usbpd_pm_sm')
    assert 'respect_bms_voltage_limit && control_vbat_mv <= 0' in algo
    assert sm.index('control_vbat_mv <= 0') < sm.index('switch (pdpm->state)')
    assert 'control_vbat_mv >= pm_config.bat_volt_lp_lmt' in sm
    assert 'control_vbat_mv > pm_config.bat_volt_lp_lmt - TAPER_VOL_HYS' in algo
    voltage_loop = algo.split('/* battery voltage loop*/', 1)[1].split('/* battery charge current loop*/', 1)[0]
    guard = sm.split('/* Check before entering/enabling the pump, not only after tuning starts. */', 1)[1].split('switch (pdpm->state)', 1)[0]
    bodies = '\n\n'.join(function(fg, n) for n in (
        'fg_gen4_get_profile_voltage', 'fg_gen4_replacement_cc_cv_mv', 'fg_gen4_set_vbatt_full_vol'))
    pd_bodies = '\n\n'.join(function(pd, n) for n in (
        'pd_get_fastcharge_mode_enabled', 'usbpd_pm_select_control_voltage',
        'usbpd_pm_limit_battery_voltage', 'usbpd_pm_update_bat_volt_limit',
        'usbpd_pm_update_cp_status', 'pd_disable_cp_by_jeita_status'))
    props = sorted(set(re.findall(r'POWER_SUPPLY_PROP_[A-Z_0-9]+', pd_bodies)) |
                   {'POWER_SUPPLY_PROP_VOLTAGE_NOW', 'POWER_SUPPLY_PROP_VOLTAGE_MAX_DESIGN',
                    'POWER_SUPPLY_PROP_TEMP', 'POWER_SUPPLY_PROP_INPUT_SUSPEND'})
    masks = sorted(set(re.findall(r'\b[A-Z_0-9]+_MASK\b', pd_bodies)))
    cp_fields = sorted(set(re.findall(r'pdpm->cp\.([a-z_0-9]+)', pd_bodies + voltage_loop)))
    code = r'''#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <limits.h>
#include <errno.h>
#include <stdio.h>
#define min(a,b) ((a)<(b)?(a):(b))
#define max(a,b) ((a)>(b)?(a):(b))
#define DIV_ROUND_UP(a,b) (((a)+(b)-1)/(b))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define pr_err(...) ((void)0)
#define pr_info(...) ((void)0)
#define pr_err_ratelimited(...) ((void)0)
#define fg_dbg(...) ((void)0)
#define FG_STATUS 0
#define JEITA_COOL_NOT_ALLOW_CP_THR 100
#define JEITA_HYSTERESIS 20
struct fg_dev { struct { int ffc_vbatt_full_mv, vbatt_full_mv, float_volt_uv; } bp; };
struct fg_gen4_chip { struct fg_dev fg; int profile_max_voltage_uv;
                     bool replacement_profile_fallback; };
static struct fg_gen4_chip chip;
static int write_error, programmed_uv, writes;
static int fg_set_constant_chg_voltage(struct fg_dev *f, int uv) {
    if (write_error) return write_error;
    programmed_uv = uv; ++writes; return 0;
}
''' + bodies + '\nenum {\n' + ',\n'.join(props) + '\n};\n'
    code += '\n'.join(f'#define {name} (1u << {i})' for i, name in enumerate(masks)) + '\n'
    code += 'struct cp { int ' + ','.join(cp_fields) + '; };\n'
    code += r'''
struct power_supply { int id; };
union power_supply_propval { int intval; };
struct usbpd_pm {
    struct power_supply *bms_psy, *cp_psy, *cp_sec_psy, *sw_psy;
    struct cp cp, cp_sec;
    bool respect_bms_voltage_limit, use_qcom_gauge, chg_enable_k81, jeita_triggered;
    int bat_volt_max, non_ffc_bat_volt_max, vbat_control_mv, battery_warm_th, state;
};
static struct { int cp_sec_enable, bat_volt_lp_lmt, fc2_steps; } pm_config;
enum { PD_PM_STATE_ENTRY, PD_PM_STATE_FC2_ENTRY_3, PD_PM_STATE_FC2_TUNE, PD_PM_STATE_FC2_EXIT };
static struct power_supply cp_supply = {1}, bms_supply = {2}, sw_supply = {3};
static int cp_mv, bms_uv, cp_error, bms_error, limit_error, mode;
static int temp_error, suspend_error, temperature = 250, suspend_input;
static int power_supply_get_property(struct power_supply *p, int prop, union power_supply_propval *v) {
    if (!p) return -ENODEV;
    v->intval = 0;
    if (p == &cp_supply && prop == POWER_SUPPLY_PROP_TI_BATTERY_VOLTAGE) {
        if (cp_error) return cp_error;
        v->intval = cp_mv;
    } else if (p == &bms_supply && prop == POWER_SUPPLY_PROP_VOLTAGE_NOW) {
        if (bms_error) return bms_error;
        v->intval = bms_uv;
    } else if (p == &bms_supply && prop == POWER_SUPPLY_PROP_VOLTAGE_MAX_DESIGN) {
        if (limit_error) return limit_error;
        return fg_gen4_get_profile_voltage(&chip, &v->intval);
    } else if (prop == POWER_SUPPLY_PROP_FASTCHARGE_MODE) {
        v->intval = mode;
    } else if (prop == POWER_SUPPLY_PROP_TEMP) {
        if (temp_error) return temp_error;
        v->intval = temperature;
    } else if (prop == POWER_SUPPLY_PROP_INPUT_SUSPEND) {
        if (suspend_error) return suspend_error;
        v->intval = suspend_input;
    }
    return 0;
}
static void usbpd_check_cp_psy(struct usbpd_pm *p) { (void)p; }
static void usbpd_check_bms_psy(struct usbpd_pm *p) { (void)p; }
static void usbpd_pm_move_state(struct usbpd_pm *p, int state) { p->state = state; }
''' + pd_bodies
    code += r'''
static int actual_voltage_step(struct usbpd_pm *pdpm) {
    int step_vbat = 0;
    int control_vbat_mv = pdpm->respect_bms_voltage_limit ? pdpm->vbat_control_mv : pdpm->cp.vbat_volt;
''' + voltage_loop + '\nreturn step_vbat;\n}\n'
    code += r'''
static int actual_entry_guard(struct usbpd_pm *pdpm) {
    int ret;
    bool stop_sw = false, recover = false;
    int control_vbat_mv = pdpm->respect_bms_voltage_limit ? pdpm->vbat_control_mv : pdpm->cp.vbat_volt;
''' + guard + '\n(void)stop_sw; (void)recover; return pdpm->state;\n}\n'
    code += f'\n#define PROFILE_MAX {maximum}\n#define NORMAL_MV {normal}\n#define FFC_MV {ffc}\n'
    code += r'''
int main(void) {
    struct usbpd_pm p = { .bms_psy=&bms_supply, .cp_psy=&cp_supply, .sw_psy=&sw_supply,
        .respect_bms_voltage_limit=true, .use_qcom_gauge=true,
        .bat_volt_max=4460, .non_ffc_bat_volt_max=4450, .battery_warm_th=480 };
    int i, count, old;
    chip.profile_max_voltage_uv=PROFILE_MAX;
    chip.replacement_profile_fallback=true;
    chip.fg.bp.vbatt_full_mv=NORMAL_MV;
    /* Also cover a stale/older DT with the former 4450 mV FFC threshold. */
    for (i=0; i<40; ++i) {
        mode=i%2; chip.fg.bp.ffc_vbatt_full_mv=(i%3 ? FFC_MV : 4450);
        assert(fg_gen4_set_vbatt_full_vol(&chip.fg, mode)==0);
        assert(programmed_uv==4440000);
        assert(chip.fg.bp.float_volt_uv<=PROFILE_MAX);
        assert(chip.profile_max_voltage_uv==PROFILE_MAX);
        assert(usbpd_pm_update_bat_volt_limit(&p)==0);
        assert(pm_config.bat_volt_lp_lmt==4450);
    }
    count=writes; old=chip.fg.bp.float_volt_uv;
    write_error=-EIO;
    assert(fg_gen4_set_vbatt_full_vol(&chip.fg, true)==-EIO);
    assert(writes==count && chip.fg.bp.float_volt_uv==old);
    write_error=0;
    chip.fg.bp.vbatt_full_mv=-EINVAL;
    assert(fg_gen4_set_vbatt_full_vol(&chip.fg, false)==-EINVAL && writes==count);
    chip.fg.bp.vbatt_full_mv=INT_MAX;
    assert(fg_gen4_set_vbatt_full_vol(&chip.fg, false)==-EINVAL && writes==count);
    chip.fg.bp.vbatt_full_mv=NORMAL_MV;
    chip.profile_max_voltage_uv=0;
    assert(fg_gen4_set_vbatt_full_vol(&chip.fg, true)==-ENODATA && writes==count);
    chip.profile_max_voltage_uv=PROFILE_MAX;
    assert(fg_gen4_replacement_cc_cv_mv(4450, 4450999)==4440);
    assert(fg_gen4_replacement_cc_cv_mv(4450, -EINVAL)==-EINVAL);
    /* Positively identified OEM runtime CC/CV behavior remains unchanged. */
    chip.replacement_profile_fallback=false;
    chip.profile_max_voltage_uv=4460000; chip.fg.bp.ffc_vbatt_full_mv=4470;
    assert(fg_gen4_set_vbatt_full_vol(&chip.fg, true)==0 && programmed_uv==4470000);
    assert(chip.fg.bp.float_volt_uv==4480000 && chip.profile_max_voltage_uv==4460000);
    assert(usbpd_pm_limit_battery_voltage(4460,4480000)==4460);
    assert(usbpd_pm_limit_battery_voltage(4460,4450999)==4450);
    chip.replacement_profile_fallback=true; chip.profile_max_voltage_uv=PROFILE_MAX;

    cp_mv=4452; bms_uv=4516000; pm_config.fc2_steps=1;
    usbpd_pm_update_cp_status(&p);
    assert(p.cp.vbat_volt==4452 && p.cp.bms_vbat_mv==4516 && p.vbat_control_mv==4516);
    assert(usbpd_pm_update_bat_volt_limit(&p)==0);
    assert(actual_voltage_step(&p)==-1);
    p.state=PD_PM_STATE_FC2_ENTRY_3;
    assert(actual_entry_guard(&p)==PD_PM_STATE_FC2_EXIT);
    cp_mv=4430; bms_uv=4450001;
    usbpd_pm_update_cp_status(&p);
    assert(p.vbat_control_mv==4451 && actual_voltage_step(&p)==-1);
    cp_mv=4430; bms_uv=4420000;
    usbpd_pm_update_cp_status(&p);
    assert(p.vbat_control_mv==4430 && actual_voltage_step(&p)==1);
    p.state=PD_PM_STATE_ENTRY;
    assert(actual_entry_guard(&p)==PD_PM_STATE_ENTRY);

    /* Read failures invalidate the poll even though raw telemetry is cached. */
    cp_error=-EIO; usbpd_pm_update_cp_status(&p);
    assert(p.vbat_control_mv<0); p.state=PD_PM_STATE_FC2_TUNE;
    assert(actual_entry_guard(&p)==PD_PM_STATE_FC2_EXIT);
    cp_error=0; bms_error=-EIO; usbpd_pm_update_cp_status(&p);
    assert(p.vbat_control_mv<0);
    bms_error=0; p.bms_psy=NULL; usbpd_pm_update_cp_status(&p);
    assert(p.vbat_control_mv<0); p.bms_psy=&bms_supply;
    p.cp_psy=NULL; usbpd_pm_update_cp_status(&p);
    assert(p.vbat_control_mv<0); p.cp_psy=&cp_supply;
    p.vbat_control_mv=0; p.state=PD_PM_STATE_FC2_ENTRY_3;
    assert(actual_entry_guard(&p)==PD_PM_STATE_FC2_EXIT);
    usbpd_pm_update_cp_status(&p);
    limit_error=-ENODATA; p.state=PD_PM_STATE_FC2_TUNE;
    assert(actual_entry_guard(&p)==PD_PM_STATE_FC2_EXIT);
    limit_error=0;
    assert(usbpd_pm_select_control_voltage(2499,4400000)<0);
    assert(usbpd_pm_select_control_voltage(4400,INT_MAX)<0);
    assert(usbpd_pm_select_control_voltage(5000,5000000)==5000);

    assert(!pd_disable_cp_by_jeita_status(&p));
    temp_error=-EIO; assert(pd_disable_cp_by_jeita_status(&p)); temp_error=0;
    suspend_error=-EIO; assert(pd_disable_cp_by_jeita_status(&p)); suspend_error=0;
    suspend_input=1; assert(pd_disable_cp_by_jeita_status(&p)); suspend_input=0;
    temperature=500; assert(pd_disable_cp_by_jeita_status(&p));
    temperature=250; assert(!pd_disable_cp_by_jeita_status(&p));
    p.respect_bms_voltage_limit=false; cp_error=-EIO; bms_error=-EIO;
    mode=1; limit_error=-EIO; usbpd_pm_update_cp_status(&p);
    assert(usbpd_pm_update_bat_volt_limit(&p)==0 && pm_config.bat_volt_lp_lmt==4460);
    puts("PASS: production FFC/profile/PPS chain, dual-voltage samples, entry guard and I/O faults");
    return 0;
}
'''
    compile_run(code, 'voltage')
    print('PASS: profile/getter/control-loop source wiring and replacement DTS limits')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path)
    args=ap.parse_args()
    root = args.root if args.root is not None else Path(__file__).resolve().parents[4]
    check_sysfs(root)
    check_voltage(root)

if __name__=='__main__':
    main()
