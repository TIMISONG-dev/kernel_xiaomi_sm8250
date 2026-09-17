#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Host tests of actual FG capacity functions and PPS voltage helpers.

Run from the kernel root: python3 tools/testing/selftests/power_supply/alioth_capacity_test.py
This does not emulate PMIC hardware, compile the kernel, or validate a battery.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile


def extract(text, pattern):
    match = re.search(pattern, text, re.M)
    if not match:
        raise AssertionError('Missing C definition: ' + pattern)
    start = text.index('{', match.end())
    masked = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                    lambda m: ' ' * len(m.group()), text[start:], flags=re.S)
    depth = 0
    for i, char in enumerate(masked):
        depth += (char == '{') - (char == '}')
        if depth == 0:
            return text[match.start():start + i + 1]
    raise AssertionError('Unbalanced C definition')


def func(text, name):
    return extract(text, r'^(?:static\s+)?(?:int|void|bool)\s+' + name + r'\s*\(')


PRELUDE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <limits.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
struct mutex { int unused; };
static void mutex_lock(struct mutex *p) { (void)p; }
static void mutex_unlock(struct mutex *p) { (void)p; }
#define pr_err(...) ((void)0)
#define pr_debug(...) ((void)0)
#define min(a,b) ((a) < (b) ? (a) : (b))
#define CAPACITY_DELTA_DECIPCT 500
static int64_t div64_s64(int64_t n, int64_t d) { return n / d; }
'''

CAP_TEST = r'''
struct storage { int64_t value; int read_error; int write_error; int writes; };
static int get_value(void *data, int64_t *value)
{
    struct storage *s = data;
    if (s->read_error) return s->read_error;
    *value = s->value;
    return 0;
}
static int set_value(void *data, int64_t value)
{
    struct storage *s = data;
    s->writes++;
    if (s->write_error) return s->write_error;
    s->value = value;
    return 0;
}
static struct cap_learning sample(struct storage *s)
{
    struct cap_learning cl = {0};
    cl.data = s;
    cl.get_learned_capacity = get_value;
    cl.store_learned_capacity = set_value;
    cl.nom_cap_uah = 4520000;
    cl.learned_cap_uah = 4513000;
    cl.final_cap_uah = 5060000;
    cl.dt.max_cap_inc = 20;
    cl.dt.max_cap_dec = 100;
    cl.dt.max_cap_uah = 5300000;
    cl.initialized = true;
    return cl;
}
int main(void)
{
    struct storage s = {0};
    struct cap_learning cl;
    int64_t old;
    int i;

    cl = sample(&s);
    assert(cap_learning_post_process(&cl) == 0);
    assert(cl.learned_cap_uah == 4603260);
    assert(s.value == cl.learned_cap_uah && cl.successful_updates == 1);

    cl = sample(&s); cl.dt.max_cap_inc = 5; cl.dt.max_cap_uah = 0;
    assert(cap_learning_post_process(&cl) == 0);
    assert(cl.learned_cap_uah == 4535565);

    cl = sample(&s); cl.final_cap_uah = 3000000;
    assert(cap_learning_post_process(&cl) == 0);
    assert(cl.learned_cap_uah == 4061700);

    /* A raw outlier must not undo the tighter +2% update limit. */
    cl = sample(&s); cl.learned_cap_uah = 4500000;
    cl.final_cap_uah = 6000000; cl.dt.max_cap_limit = 172;
    assert(cap_learning_post_process(&cl) == 0);
    assert(cl.learned_cap_uah == 4590000);

    cl = sample(&s); cl.learned_cap_uah = 4500000;
    cl.final_cap_uah = 2000000; cl.dt.min_cap_limit = 100;
    assert(cap_learning_post_process(&cl) == 0);
    assert(cl.learned_cap_uah == 4068000);

    cl = sample(&s); cl.learned_cap_uah = 5290000; cl.final_cap_uah = 5400000;
    assert(cap_learning_post_process(&cl) == 0 && cl.learned_cap_uah == 5300000);

    cl = sample(&s); cl.final_cap_uah = cl.learned_cap_uah;
    assert(cap_learning_post_process(&cl) == 0 && cl.learned_cap_uah == 4513000);

    cl = sample(&s); old = cl.learned_cap_uah; s.write_error = -EIO;
    assert(cap_learning_post_process(&cl) == -EIO);
    assert(cl.learned_cap_uah == old && cl.successful_updates == 0);
    assert(cl.last_error == -EIO); s.write_error = 0;

    cl = sample(&s); cl.dt.max_cap_inc = -1;
    assert(cap_learning_post_process(&cl) == -EINVAL);
    cl = sample(&s); cl.dt.max_cap_dec = 1001;
    assert(cap_learning_post_process(&cl) == -EINVAL);
    cl = sample(&s); cl.dt.skew_decipct = -1000;
    assert(cap_learning_post_process(&cl) == -EINVAL);
    cl = sample(&s); cl.dt.max_cap_uah = -1;
    assert(cap_learning_post_process(&cl) == -EINVAL);
    cl = sample(&s); cl.dt.min_cap_limit = 100; cl.dt.max_cap_uah = 3000000;
    assert(cap_learning_post_process(&cl) == -EINVAL);
    cl = sample(&s); cl.final_cap_uah = 0;
    assert(cap_learning_post_process(&cl) == -ERANGE);

    s.value = 5400000; cl = sample(&s); cl.successful_updates = 8;
    assert(cap_learning_post_profile_init(&cl, 4520000) == 0);
    assert(cl.learned_cap_uah == 5300000 && s.value == 5300000);
    assert(cl.initialized && cl.successful_updates == 0);

    s.value = 6000000; cl = sample(&s);
    assert(cap_learning_post_profile_init(&cl, 6000000) == 0);
    assert(cl.learned_cap_uah == 5300000 && s.value == 5300000);

    s.value = 0; cl = sample(&s);
    assert(cap_learning_post_profile_init(&cl, 4520000) == 0);
    assert(cl.learned_cap_uah == 4520000);
    s.value = 9000000; cl = sample(&s);
    assert(cap_learning_post_profile_init(&cl, 4520000) == 0);
    assert(cl.learned_cap_uah == 4520000);
    s.value = 5060000; cl = sample(&s);
    assert(cap_learning_post_profile_init(&cl, 4520000) == 0);
    assert(cl.learned_cap_uah == 5060000 && cl.successful_updates == 0);

    s.value = 5400000; s.write_error = -EIO; cl = sample(&s);
    assert(cap_learning_post_profile_init(&cl, 4520000) == -EIO);
    assert(!cl.initialized && cl.last_error == -EIO);
    s.write_error = 0; s.read_error = -EIO; cl = sample(&s);
    assert(cap_learning_post_profile_init(&cl, 4520000) == -EIO);
    assert(!cl.initialized && cl.successful_updates == 0); s.read_error = 0;
    cl = sample(&s);
    assert(cap_learning_post_profile_init(&cl, 0) == -EINVAL);

    cl = sample(&s); cl.successful_updates = UINT_MAX;
    assert(cap_learning_post_process(&cl) == 0 && cl.successful_updates == UINT_MAX);
    cl = sample(&s); cl.nom_cap_uah = INT_MAX; cl.learned_cap_uah = INT_MAX;
    cl.final_cap_uah = INT_MAX; cl.dt.max_cap_uah = 0;
    cl.dt.max_cap_inc = 1000; cl.dt.max_cap_limit = 1000; cl.dt.skew_decipct = 1000;
    assert(cap_learning_post_process(&cl) == 0 && cl.learned_cap_uah == INT_MAX);

    for (i = 1; i <= 1000; i++) {
        cl = sample(&s); cl.learned_cap_uah = 3000000 + i * 2000;
        old = cl.learned_cap_uah; cl.final_cap_uah = 2000000 + i * 4000;
        assert(cap_learning_post_process(&cl) == 0);
        assert(cl.learned_cap_uah >= old * 900 / 1000);
        assert(cl.learned_cap_uah <= old * 1020 / 1000);
        assert(cl.learned_cap_uah <= 5300000);
    }
    puts("PASS: capacity bounds, restoration, failure accounting, 1000 sample cases");
    return 0;
}
'''

PD_STUBS = r'''
struct power_supply { int value; int error; };
union power_supply_propval { int intval; const char *strval; };
struct usbpd_pm {
    bool respect_bms_voltage_limit;
    bool fast;
    int bat_volt_max;
    int non_ffc_bat_volt_max;
    struct power_supply *bms_psy;
};
static struct { int bat_volt_lp_lmt; } pm_config;
#define POWER_SUPPLY_PROP_VOLTAGE_MAX_DESIGN 1
static bool pd_get_fastcharge_mode_enabled(struct usbpd_pm *p) { return p->fast; }
static void usbpd_check_bms_psy(struct usbpd_pm *p) { (void)p; }
static int power_supply_get_property(struct power_supply *p, int prop,
                                    union power_supply_propval *v)
{
    (void)prop;
    if (p->error) return p->error;
    v->intval = p->value;
    return 0;
}
'''

PD_TEST = r'''
int main(void)
{
    struct power_supply bms = {4450000, 0};
    struct usbpd_pm p = {true, true, 4460, 4450, &bms};
    assert(usbpd_pm_update_bat_volt_limit(&p) == 0);
    assert(pm_config.bat_volt_lp_lmt == 4450);
    bms.value = 4480000;
    assert(usbpd_pm_update_bat_volt_limit(&p) == 0);
    assert(pm_config.bat_volt_lp_lmt == 4460);
    p.fast = false;
    assert(usbpd_pm_update_bat_volt_limit(&p) == 0);
    assert(pm_config.bat_volt_lp_lmt == 4450);
    assert(usbpd_pm_limit_battery_voltage(4460, 4450999) == 4450);
    assert(usbpd_pm_limit_battery_voltage(4460, 0) == -EINVAL);
    assert(usbpd_pm_limit_battery_voltage(4460, 5000001) == -EINVAL);
    assert(usbpd_pm_limit_battery_voltage(0, 4450000) == -EINVAL);
    pm_config.bat_volt_lp_lmt = 4321;
    bms.error = -ENODATA;
    assert(usbpd_pm_update_bat_volt_limit(&p) == -ENODATA);
    assert(pm_config.bat_volt_lp_lmt == 4321);
    bms.error = 0; bms.value = -EINVAL;
    assert(usbpd_pm_update_bat_volt_limit(&p) == -EINVAL);
    p.bms_psy = NULL;
    assert(usbpd_pm_update_bat_volt_limit(&p) == -ENODEV);
    p.respect_bms_voltage_limit = false; p.fast = true;
    assert(usbpd_pm_update_bat_volt_limit(&p) == 0);
    assert(pm_config.bat_volt_lp_lmt == 4460);
    puts("PASS: PPS profile/board bounds, rounding, missing/invalid BMS, opt-out");
    return 0;
}
'''


def compile_run(code, label):
    with tempfile.TemporaryDirectory(prefix='alioth-cl-test-') as td:
        src = Path(td) / 'test.c'
        exe = Path(td) / 'test'
        src.write_text(code)
        subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra',
                        '-Werror', '-Wno-unused-function', '-fsanitize=undefined',
                        '-fno-sanitize-recover=all', str(src), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
    print('Compiled actual source functions:', label)


def main():
    hdr = Path('drivers/power/supply/qcom/fg-alg.h').read_text()
    alg = Path('drivers/power/supply/qcom/fg-alg.c').read_text()
    definitions = '\n'.join(extract(hdr, '^struct ' + name + r'\s*') + ';'
                            for name in ('cl_params', 'cap_learning'))
    functions = '\n'.join(func(alg, name) for name in (
        'cap_learning_capacity_limits', 'cap_learning_post_process',
        'cap_learning_post_profile_init'))
    compile_run(PRELUDE + definitions + functions + CAP_TEST, 'capacity learning')
    pd = Path('drivers/power/supply/ti/pd_policy_manager.c')
    if pd.exists() and 'usbpd_pm_limit_battery_voltage' in pd.read_text():
        source = pd.read_text()
        functions = '\n'.join(func(source, name) for name in (
            'usbpd_pm_limit_battery_voltage', 'usbpd_pm_update_bat_volt_limit'))
        compile_run(PRELUDE + PD_STUBS + functions + PD_TEST, 'PPS voltage limits')
        sm = func(source, 'usbpd_pm_sm')
        assert sm.index('usbpd_pm_update_bat_volt_limit') < sm.index('switch (pdpm->state)')
        assert 'usbpd_pm_move_state(pdpm, PD_PM_STATE_FC2_EXIT)' in sm
        assert 'usbpd_pm_update_bat_volt_limit' in func(source, 'usbpd_pm_fc2_charge_algo')
    else:
        print('PPS follow-up not applied yet; PPS tests skipped')

    fg = Path('drivers/power/supply/qcom/qpnp-fg-gen4.c')
    if fg.exists() and 'replacement_battery_type' in fg.read_text():
        source = fg.read_text()
        assert 'replacement_capacity_high_mah' not in source
        design = source.split('case POWER_SUPPLY_PROP_CHARGE_FULL_DESIGN:', 1)[1]
        design = design.split('case POWER_SUPPLY_PROP_CHARGE_COUNTER:', 1)[0]
        assert 'get_learned_capacity' not in design
        store = func(source, 'fg_gen4_store_learned_capacity')
        assert 'SDAM_COOKIE_OFFSET_4BYTE' not in store
        assert 'learned_cap_uah > 32767000' in store
        profile = func(source, 'fg_gen4_get_batt_profile')
        assert profile.index('chip->replacement_profile_fallback =') > profile.index('fg_gen4_get_batt_profile_dt_props')
        assert '"K11A_FMT_4520mah"' in profile
        assert 'fallback_type = chip->dt.replacement_battery_type' in profile
        assert 'mutex_lock(&chip->cl->lock)' in profile
        dts = Path('arch/arm64/boot/dts/vendor/qcom/alioth-sm8250.dtsi').read_text()
        assert 'qcom,replacement-capacity-high-threshold-mah' not in dts
        assert 'qcom,replacement-battery-type = "K11A_custom"' in dts
        print('PASS: FG/DTS source invariants')


if __name__ == '__main__':
    main()
