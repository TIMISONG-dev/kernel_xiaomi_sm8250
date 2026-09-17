#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile the production display functions with host stubs; no hardware I/O."""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "drivers/power/supply/qcom/qpnp-fg-gen4.c"

def extract(text, name):
    marker = "static ssize_t " + name + "("
    assert text.count(marker) == 1, name
    start = text.index(marker)
    opening = text.index("{", start)
    depth = 0
    for pos in range(opening, len(text)):
        if text[pos] == "{": depth += 1
        elif text[pos] == "}":
            depth -= 1
            if not depth: return text[start:pos + 1]
    raise AssertionError("Unterminated function: " + name)

PRELUDE = r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <limits.h>
#include <errno.h>
#include <string.h>
#include <sys/types.h>
#define PAGE_SIZE 4096
struct cap_learning {
    bool initialized;
    unsigned int successful_updates;
    int64_t learned_cap_uah;
    struct { int64_t max_cap_uah; } dt;
    int lock;
};
struct fg_gen4_chip {
    struct cap_learning *cl;
    struct {
        bool profile_available, soc_reporting_ready, battery_missing;
        struct { const char *batt_type_str; } bp;
    } fg;
    bool replacement_profile_fallback;
};
struct device { struct fg_gen4_chip *data; };
struct device_attribute { int unused; };
static void *dev_get_drvdata(struct device *dev) { return dev->data; }
static void mutex_lock(int *lock) { assert(*lock == 0); ++*lock; }
static void mutex_unlock(int *lock) { assert(*lock == 1); --*lock; }
static int64_t div64_s64(int64_t a, int64_t b) { return a / b; }
static int scnprintf(char *buf, size_t size, const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    int n = vsnprintf(buf, size, fmt, ap); va_end(ap);
    return n < 0 ? n : size == 0 ? 0 : (size_t)n >= size ? (int)size - 1 : n;
}
"""
MAIN = r"""
int main(void) {
    char buf[PAGE_SIZE];
    struct cap_learning cl = { .initialized = true,
        .learned_cap_uah = 4572000, .dt.max_cap_uah = 5300000 };
    struct fg_gen4_chip chip = { .cl = &cl,
        .fg = { .profile_available = true, .soc_reporting_ready = true,
            .bp.batt_type_str = "K11A_custom" },
        .replacement_profile_fallback = true };
    struct device dev = { .data = &chip };
    assert(battery_model_show(&dev, NULL, buf) > 0);
    assert(!strcmp(buf, "K11A_custom\n")); /* restored is not newly learned */
    cl.successful_updates = 1;
    assert(battery_model_show(&dev, NULL, buf) > 0);
    assert(!strcmp(buf, "K11A_custom_4572mah\n"));
    cl.learned_cap_uah = 5000000;
    assert(battery_model_show(&dev, NULL, buf) > 0);
    assert(!strcmp(buf, "K11A_custom_5000mah\n"));
    cl.learned_cap_uah = 5060123;
    assert(battery_model_show(&dev, NULL, buf) > 0);
    assert(!strcmp(buf, "K11A_custom_5060mah\n"));
    cl.learned_cap_uah = 5300000;
    assert(battery_model_show(&dev, NULL, buf) > 0);
    assert(!strcmp(buf, "K11A_custom_5300mah\n"));
    const int64_t invalid[] = { -1, 0, 999, 5300001, INT64_MAX };
    for (size_t i=0; i<sizeof(invalid)/sizeof(invalid[0]); ++i) {
        cl.learned_cap_uah = invalid[i];
        assert(battery_model_show(&dev, NULL, buf) > 0);
        assert(!strcmp(buf, "K11A_custom\n"));
    }
    cl.learned_cap_uah = 4572000;
    cl.initialized = false;
    assert(battery_model_show(&dev, NULL, buf) > 0);
    assert(!strcmp(buf, "K11A_custom\n"));
    cl.initialized = true;
    chip.replacement_profile_fallback = false;
    const char *known[] = {"K11A_FMT_4520mah", "K11A_GY_4520mah", "j3ssun_5000mah"};
    for (size_t i=0; i<3; ++i) {
        chip.fg.bp.batt_type_str = known[i];
        assert(battery_model_show(&dev, NULL, buf) > 0);
        assert(!strncmp(buf, known[i], strlen(known[i])));
        assert(strlen(buf) == strlen(known[i]) + 1);
    }
    chip.fg.bp.batt_type_str = NULL;
    assert(battery_model_show(&dev, NULL, buf) == -ENODATA);
    assert(cl.lock == 0);
    chip.fg.bp.batt_type_str = "K11A_custom";
    chip.fg.soc_reporting_ready = false;
    assert(battery_model_show(&dev, NULL, buf) == -ENODATA);
    chip.fg.soc_reporting_ready = true;
    chip.fg.battery_missing = true;
    assert(battery_model_show(&dev, NULL, buf) == -ENODATA);
    chip.fg.battery_missing = false;
    chip.fg.profile_available = false;
    assert(battery_model_show(&dev, NULL, buf) == -ENODATA);
    assert(cl.lock == 0);
    chip.cl = NULL;
    assert(battery_model_show(&dev, NULL, buf) == -ENODATA);
    dev.data = NULL;
    assert(battery_model_show(&dev, NULL, buf) == -ENODATA);
    char short_buf[5];
    assert(fg_gen4_format_battery_model(short_buf, sizeof(short_buf),
        "K11A_custom", false, NULL) == 4);
    assert(short_buf[4] == '\0');
    puts("K11A display label tests: PASS");
    return 0;
}
"""

def main():
    text = SOURCE.read_text()
    code = PRELUDE + "\n" + extract(text, "fg_gen4_format_battery_model")
    code += "\n" + extract(text, "battery_model_show") + "\n" + MAIN
    assert "&dev_attr_battery_model.attr," in text
    assert "static DEVICE_ATTR_RO(battery_model);" in text
    with tempfile.TemporaryDirectory(prefix="alioth-label-") as tmp:
        c = Path(tmp) / "label.c"; exe = Path(tmp) / "label"
        c.write_text(code)
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra",
            "-Werror", "-Wno-unused-parameter", "-fsanitize=undefined", "-fno-sanitize-recover=all",
            str(c), "-o", str(exe)], check=True)
        subprocess.run([str(exe)], check=True)

if __name__ == "__main__": main()
