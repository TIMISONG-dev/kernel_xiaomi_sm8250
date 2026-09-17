#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile production sysfs helpers with hardware stubs; not an ARM64 build."""
from pathlib import Path
import re, subprocess, tempfile, shutil, sys
R=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parents[4]
def fn(p,name):
    s=(R/p).read_text(); m=re.search(r'^(?:static (?:inline )?)?(?:void|int) '+name+r'\([^;]+?\n\{',s,re.M)
    assert m,name
    i=s.index('{',m.start()); depth=0
    for j in range(i,len(s)):
        depth+=(s[j]=='{')-(s[j]=='}')
        if not depth: return s[m.start():j+1]
    raise AssertionError(name)
get=fn('drivers/gpu/msm/kgsl.h','kgsl_gpu_sysfs_add_link')
model=fn('drivers/gpu/msm/adreno.c','adreno_gpu_model')
cleanup=fn('drivers/gpu/msm/kgsl_pwrctrl.c','kgsl_pwrctrl_uninit_sysfs')
write=fn('drivers/power/supply/qcom/qpnp-smb5.c','smb5_batt_prop_is_writeable')
props=sorted(set(re.findall(r'POWER_SUPPLY_PROP_\w+',write))|{'POWER_SUPPLY_PROP_CURRENT_AVG'})
# Compile the actual callback, not a rewritten equivalent.
c='''#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <stdlib.h>
#define IS_ERR_OR_NULL(p) (!(p) || (uintptr_t)(p) > (uintptr_t)-4096)
struct kernfs_node { int ref; };
struct kobject { struct kernfs_node *sd; };
struct device { struct kobject kobj; };
struct kgsl_device { struct device *dev; struct kobject *gpu_sysfs_kobj; };
struct power_supply { int unused; };
static struct kernfs_node node;
static int fail_lookup, links, puts, kputs, removes;
static const void *pwrctrl_attr_list;
static struct kernfs_node *sysfs_get_dirent(struct kernfs_node *p, const char *n) {
 (void)p; (void)n; if (fail_lookup) return NULL; node.ref++; return &node;
}
static struct kernfs_node *kernfs_create_link(struct kernfs_node *d, const char*n, struct kernfs_node *s) {
 (void)d; (void)n; assert(s==&node); links++; return NULL;
}
static void sysfs_put(struct kernfs_node *n) { n->ref--; puts++; }
static void kobject_put(struct kobject *k) { if(k) kputs++; }
static void sysfs_remove_files(struct kobject*k, const void*a) {(void)k;(void)a;removes++;}
static const char *model_value;
static const char *adreno_get_gpu_model(struct kgsl_device*d) {(void)d;return model_value;}
static int scnprintf(char *s, size_t z, const char *fmt, ...) __attribute__((format(printf,3,4)));
static int scnprintf(char *s, size_t z, const char *fmt, ...) {
 va_list a; va_start(a,fmt); int r=vsnprintf(s,z,fmt,a); va_end(a); return r;
}
'''.replace('links, puts,','links, put_count,').replace('n->ref--; puts++;','n->ref--; put_count++;')
c+='enum power_supply_property { '+','.join(props)+' };\n'+get+'\n'+model+'\n'+cleanup+'\n'+write
c+='''
int main(void) {
 struct kobject a={&node}, b={&node}; struct device dev={a}; struct kgsl_device d={&dev,&b};
 kgsl_gpu_sysfs_add_link(NULL,&b,"a","b"); assert(!links && !put_count);
 fail_lookup=1; kgsl_gpu_sysfs_add_link(&a,&b,"a","b"); assert(!links && !put_count);
 fail_lookup=0; kgsl_gpu_sysfs_add_link(&a,&b,"a","b"); assert(links==1 && put_count==1 && node.ref==0);
 char out[32]; model_value="Adreno650v1"; adreno_gpu_model(&d,out,sizeof out); assert(!strcmp(out,model_value));
 model_value="model-%s-%n-100%"; adreno_gpu_model(&d,out,sizeof out); assert(!strcmp(out,model_value));
 kgsl_pwrctrl_uninit_sysfs(&d); assert(d.gpu_sysfs_kobj==NULL && kputs==1 && removes==1);
 kgsl_pwrctrl_uninit_sysfs(&d); assert(kputs==1 && removes==2);
 assert(smb5_batt_prop_is_writeable(NULL,POWER_SUPPLY_PROP_CHARGE_CONTROL_LIMIT)==1);
 assert(smb5_batt_prop_is_writeable(NULL,POWER_SUPPLY_PROP_CURRENT_AVG)==0);
 puts("PASS: production model formatting, kernfs references, kobject teardown, writable thermal callback");
}
'''
for cc in ('gcc','clang'):
    if not shutil.which(cc): continue
    with tempfile.TemporaryDirectory() as td:
        p=Path(td); (p/'test.c').write_text(c)
        subprocess.run([cc,'-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wformat-security','-fsanitize=undefined','-fno-sanitize-recover=all',str(p/'test.c'),'-o',str(p/'test')],check=True)
        subprocess.run([str(p/'test')],check=True)
s=(R/'drivers/gpu/msm/kgsl_pwrctrl.c').read_text()
assert '"page_alloc", "gpu_memory"' in s
assert re.search(r'if \(!device->gpu_sysfs_kobj\) \{\s*sysfs_remove_files',s)
assert 'DEVICE_ATTR(page_alloc, 0444, memstat_show, NULL)' in (R/'drivers/gpu/msm/kgsl_sharedmem.c').read_text()
print('PASS: allocation alias, read-only DAC, rollback source checks')
