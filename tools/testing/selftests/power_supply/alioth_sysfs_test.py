#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Host tests for production optional-property discovery; no hardware I/O."""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[4]

def function(source, name):
    pattern = re.compile(r'^static (?:int|bool) ' + re.escape(name) + r'\([^;]*?\n\{', re.M)
    found = list(pattern.finditer(source))
    if len(found) != 1:
        raise AssertionError(f'{name}: expected one definition, got {len(found)}')
    start = found[0].start()
    opening = source.index('{', start)
    depth = 0
    for pos in range(opening, len(source)):
        if source[pos] == '{':
            depth += 1
        elif source[pos] == '}':
            depth -= 1
            if depth == 0:
                return source[start:pos + 1]
    raise AssertionError(f'{name}: unterminated function')

PRELUDE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <errno.h>
enum power_supply_property { POWER_SUPPLY_PROP_STATUS = 0,
    POWER_SUPPLY_PROP_ONLINE = 4, POWER_SUPPLY_PROP_SCOPE = 67 };
enum { POWER_SUPPLY_TYPE_BATTERY = 1, POWER_SUPPLY_TYPE_BMS,
    POWER_SUPPLY_TYPE_USB, POWER_SUPPLY_TYPE_MAIN };
enum { POWER_SUPPLY_SCOPE_UNKNOWN, POWER_SUPPLY_SCOPE_SYSTEM,
    POWER_SUPPLY_SCOPE_DEVICE };
union power_supply_propval { int intval; };
struct power_supply;
struct power_supply_desc {
    int type;
    const enum power_supply_property *properties;
    size_t num_properties;
    int (*get_property)(struct power_supply *, enum power_supply_property,
        union power_supply_propval *);
};
struct power_supply {
    const struct power_supply_desc *desc;
    int scope, online, scope_error, online_error, scope_calls, online_calls;
};
struct device { struct power_supply *data; };
static void *dev_get_drvdata(struct device *dev) { return dev->data; }
static int fake_get(struct power_supply *psy, enum power_supply_property prop,
    union power_supply_propval *val)
{
    bool declared = false;
    for (size_t i = 0; i < psy->desc->num_properties; ++i)
        declared |= psy->desc->properties[i] == prop;
    assert(declared); /* Regression: unadvertised 67/4 must never reach the driver. */
    if (prop == POWER_SUPPLY_PROP_SCOPE) {
        ++psy->scope_calls;
        val->intval = psy->scope;
        return psy->scope_error;
    }
    assert(prop == POWER_SUPPLY_PROP_ONLINE);
    ++psy->online_calls;
    val->intval = psy->online;
    return psy->online_error;
}
'''
MAIN = r'''
static int supplied(struct power_supply *list, size_t nr)
{
    unsigned int count = 0;
    int rc = 0;
    for (size_t i = 0; i < nr; ++i) {
        struct device dev = { .data = &list[i] };
        rc = __power_supply_is_system_supplied(&dev, &count);
        if (rc) break;
    }
    return count ? rc : 1;
}
int main(void)
{
    static const enum power_supply_property status[] = { POWER_SUPPLY_PROP_STATUS };
    static const enum power_supply_property online[] = { POWER_SUPPLY_PROP_ONLINE };
    static const enum power_supply_property scoped[] = {
        POWER_SUPPLY_PROP_SCOPE, POWER_SUPPLY_PROP_ONLINE };
    const struct power_supply_desc battery = {
        POWER_SUPPLY_TYPE_BATTERY, status, 1, fake_get };
    const struct power_supply_desc bms = {
        POWER_SUPPLY_TYPE_BMS, status, 1, fake_get };
    const struct power_supply_desc pc = {
        POWER_SUPPLY_TYPE_USB, online, 1, fake_get };
    const struct power_supply_desc usb = {
        POWER_SUPPLY_TYPE_USB, scoped, 2, fake_get };
    const struct power_supply_desc empty = {
        POWER_SUPPLY_TYPE_MAIN, NULL, 0, fake_get };
    struct power_supply p[] = {
        { .desc = &bms }, { .desc = &pc }, { .desc = &battery }, { .desc = &empty }
    };
    assert(supplied(NULL, 0) == 1); /* Preserve desktop/no-supply convention. */
    assert(supplied(p, 4) == 0); /* Battery/BMS/empty never fabricate AC online. */
    assert(p[0].scope_calls == 0 && p[0].online_calls == 0);
    assert(p[1].scope_calls == 0 && p[1].online_calls == 1);
    assert(p[2].scope_calls == 0 && p[2].online_calls == 0);
    assert(p[3].scope_calls == 0 && p[3].online_calls == 0);
    p[1].online = 1;
    assert(supplied(p, 4) == 1);
    p[1].online_error = -EIO;
    assert(supplied(p, 4) == 0); /* A failed advertised read is not AC present. */
    p[1].online_error = 0;
    p[1].online = 0;
    struct power_supply q[] = {
        { .desc = &usb, .scope = POWER_SUPPLY_SCOPE_DEVICE, .online = 1 },
        { .desc = &battery }
    };
    assert(supplied(q, 2) == 0);
    assert(q[0].scope_calls == 1 && q[0].online_calls == 0);
    assert(supplied(q, 1) == 1); /* Only device-scoped supplies: desktop fallback. */
    q[0].scope = POWER_SUPPLY_SCOPE_SYSTEM;
    assert(supplied(q, 2) == 1);
    q[0].scope = POWER_SUPPLY_SCOPE_UNKNOWN;
    assert(supplied(q, 2) == 1);
    q[0].scope = POWER_SUPPLY_SCOPE_DEVICE;
    q[0].scope_error = -EIO;
    assert(supplied(q, 2) == 1); /* Preserve prior unknown-scope policy on errors. */
    q[0].online_error = -EIO;
    assert(supplied(q, 2) == 0);
    assert(!power_supply_declares_property(&p[3], POWER_SUPPLY_PROP_ONLINE));
    puts("PASS: advertised-property probes, BMS/PC/USB, errors and scope policy");
    return 0;
}
'''

def main():
    core = (ROOT / 'drivers/power/supply/power_supply_core.c').read_text()
    code = PRELUDE + '\n' + function(core, 'power_supply_declares_property') + '\n'
    code += function(core, '__power_supply_is_system_supplied') + '\n' + MAIN
    with tempfile.TemporaryDirectory(prefix='alioth-sysfs-') as directory:
        src = Path(directory) / 'test.c'
        exe = Path(directory) / 'test'
        src.write_text(code)
        subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra',
                        '-Werror', '-fsanitize=undefined', '-fno-sanitize-recover=all',
                        str(src), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
    smb = (ROOT / 'drivers/power/supply/qcom/qpnp-smb5.c').read_text()
    props = smb.split('static enum power_supply_property smb5_batt_props[] = {', 1)[1].split('};', 1)[0]
    assert props.count('POWER_SUPPLY_PROP_CURRENT_AVG,') == 1
    get = function(smb, 'smb5_batt_get_prop')
    avg = get.split('case POWER_SUPPLY_PROP_CURRENT_AVG:', 1)[1].split('break;', 1)[0]
    assert re.search(r'rc\s*=\s*smblib_get_prop_from_bms\(chg,\s*POWER_SUPPLY_PROP_CURRENT_AVG,\s*val\);', avg)
    writable = function(smb, 'smb5_batt_prop_is_writeable')
    assert 'POWER_SUPPLY_PROP_CURRENT_AVG' not in writable
    fg = (ROOT / 'drivers/power/supply/qcom/qpnp-fg-gen4.c').read_text()
    assert 'POWER_SUPPLY_PROP_CURRENT_AVG,' in fg
    assert 'FG_SRAM_IBAT_FLT' in function(fg, 'fg_psy_get_property')
    enum = (ROOT / 'include/linux/power_supply.h').read_text().split('enum power_supply_property {', 1)[1]
    names = re.findall(r'\bPOWER_SUPPLY_PROP_[A-Z0-9_]+\b', enum.split('POWER_SUPPLY_PROP_PRECHARGE_CURRENT')[0])
    assert names.index('POWER_SUPPLY_PROP_ONLINE') == 4
    assert names.index('POWER_SUPPLY_PROP_SCOPE') == 67
    print('PASS: current_avg descriptor/forwarding/read-only ABI and enum identities')

if __name__ == '__main__':
    main()
