#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Host regressions for the actual FG cycle-counter functions (hardware stubbed)."""
from pathlib import Path
import os
import re
import subprocess
import tempfile


def function(s, name):
    m = re.search(r'^(?:static\s+)?(?:int|void|bool)\s+' + re.escape(name)
                  + r'\([^;]*?\)\n\{.*?^\}', s, re.M | re.S)
    if not m:
        raise AssertionError('function missing: ' + name)
    return m.group(0)


PRE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <errno.h>
#include <limits.h>
#include <pthread.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
struct mutex { pthread_mutex_t m; };
static void mutex_init(struct mutex *m) { assert(!pthread_mutex_init(&m->m, NULL)); }
static void mutex_lock(struct mutex *m) { assert(!pthread_mutex_lock(&m->m)); }
static void mutex_unlock(struct mutex *m) { assert(!pthread_mutex_unlock(&m->m)); }
#define pr_err(...) ((void)0)
#define pr_debug(...) ((void)0)
#define fg_dbg(...) ((void)0)
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define BUCKET_COUNT 8
#define BUCKET_SOC_PCT 32
#define POWER_SUPPLY_STATUS_CHARGING 1
#define POWER_SUPPLY_STATUS_DISCHARGING 2
static int scnprintf(char *b, size_t n, const char *fmt, ...) {
    int r; va_list a;
    va_start(a,fmt); r=vsnprintf(b,n,fmt,a); va_end(a);
    return n && r >= (int)n ? (int)n-1 : r;
}
'''

COMMON_TEST = r'''
static u16 history[8];
static int fail_read, fail_write, writes;
static int fake_restore(void *d, u16 *b, int n) {
    (void)d; assert(n==8);
    if (fail_read) { b[0]=12345; return -EIO; }
    memcpy(b,history,sizeof(history)); return 0;
}
static int fake_store(void *d, u16 *b, int id, int n) {
    (void)d; writes++;
    if (fail_write) return -EIO;
    memcpy(history+id,b,n); return 0;
}
static void *reader(void *d) {
    struct cycle_counter *c=d;
    for (int i=0;i<10000;i++) {
        int v=-1; assert(get_cycle_count(c,&v)==0); assert(v==72);
    }
    return NULL;
}
static void full_session(struct cycle_counter *c) {
    for(int i=0;i<256;i++) cycle_count_update(c,i,1,false,true);
    cycle_count_update(c,255,1,true,true);
}
int main(void) {
    struct cycle_counter c={0}; int value=999; const char *text;
    c.data=&c; c.restore_count=fake_restore; c.store_count=fake_store;
    assert(cycle_count_init(&c)==0);
    assert(get_cycle_count(&c,&value)==-ENODATA && value==999);
    for(int i=0;i<8;i++) history[i]=(i+1)*16;
    assert(restore_cycle_count(&c)==0);
    assert(get_cycle_count(&c,&value)==0 && value==72);
    assert(get_cycle_counts(&c,&text)==0);
    assert(!strcmp(text,"16 32 48 64 80 96 112 128 "));
    pthread_t threads[4];
    for(int i=0;i<4;i++) assert(!pthread_create(&threads[i],NULL,reader,&c));
    for(int i=0;i<4;i++) assert(!pthread_join(threads[i],NULL));
    fail_read=1; assert(restore_cycle_count(&c)==-EIO);
    assert(!c.initialized && c.count[0]==16 && c.count[7]==128);
    assert(get_cycle_count(&c,&value)==-ENODATA);
    fail_read=0; assert(restore_cycle_count(&c)==0);
    clear_cycle_count(&c); assert(c.initialized && c.last_bucket==-1);
    full_session(&c); assert(get_cycle_count(&c,&value)==0 && value==1);
    int old_writes=writes;
    for(int i=0;i<25;i++) cycle_count_update(&c,255,1,true,true);
    assert(writes==old_writes);
    full_session(&c); assert(get_cycle_count(&c,&value)==0 && value==2);
    cycle_count_update(&c,10,1,false,true);
    cycle_count_update(&c,12,2,false,false);
    assert(!c.started[0]);
    cycle_count_update(&c,28,1,false,true);
    cycle_count_update(&c,30,2,false,false);
    assert(c.count[0]==2);
    old_writes=writes;
    cycle_count_update(&c,-1,1,false,true);
    cycle_count_update(&c,256,1,false,true);
    assert(writes==old_writes);
    assert(get_cycle_count(NULL,&value)==-EINVAL);
    assert(get_cycle_count(&c,NULL)==-EINVAL);
    c.count[0]=65535; history[0]=65535;
    cycle_count_update(&c,0,1,false,true);
    cycle_count_update(&c,31,2,false,false);
    assert(c.count[0]==65535 && writes==old_writes);
    fail_write=1;
    cycle_count_update(&c,32,1,false,true);
    cycle_count_update(&c,63,2,false,false);
    assert(!c.initialized && c.last_error==-EIO && c.count[1]==2);
    value=-1; assert(get_cycle_count(&c,&value)==-ENODATA);
    fail_write=0; assert(restore_cycle_count(&c)==0);
    fail_write=1; clear_cycle_count(&c);
    assert(!c.initialized && c.count[0]==65535);
    fail_write=0; assert(restore_cycle_count(&c)==0);
    clear_cycle_count(&c); assert(get_cycle_count(&c,&value)==0 && value==0);
    puts("PASS: cycle snapshots, 40000 concurrent reads, session boundaries, errors, bounds, saturation");
    return 0;
}
'''

HW = r'''
#define SDAM_CYCLE_COUNT_OFFSET 0x81
#define SDAM_CAP_LEARN_OFFSET 0x91
#define SDAM_COOKIE_OFFSET_4BYTE 0x95
#define SDAM_COOKIE_4BYTE 0x12345678
#define CYCLE_COUNT_WORD 291
#define CYCLE_COUNT_OFFSET 0
#define FG_IMA_DEFAULT 0
#define FG_SRAM_ACT_BATT_CAP 1
#define DC_INIT_VALUE 0x1ffff
#define POWER_SUPPLY_PROP_MAXIM_BATT_CYCLE_COUNT 1
struct power_supply { int dummy; };
union power_supply_propval { int intval; };
struct fg_dev {
    bool soc_reporting_ready, profile_available;
    int cycle_count, maxim_cycle_count;
    struct power_supply *max_verify_psy;
};
struct fg_gen4_chip {
    struct fg_dev fg;
    struct { const char *replacement_battery_type; } dt;
    bool replacement_profile_fallback;
    void *fg_nvmem;
    struct cycle_counter *counter;
};
static u8 nvm[256], ram[16];
static int nr, nw, nr_fail, nw_fail, nr_short, nw_short, ram_fail, cap_fail;
static int actual_cap=4513;
static int nvmem_device_read(void *p, int off, int len, void *buf) {
    assert(p); nr++;
    if(nr==nr_fail) return -EIO;
    if(nr==nr_short) { memcpy(buf,nvm+off,len-1); return len-1; }
    memcpy(buf,nvm+off,len); return len;
}
static int nvmem_device_write(void *p, int off, int len, void *buf) {
    assert(p); nw++;
    if(nw==nw_fail) return -EIO;
    if(nw==nw_short) { memcpy(nvm+off,buf,len-1); return len-1; }
    memcpy(nvm+off,buf,len); return len;
}
static int fg_sram_read(struct fg_dev *f,int word,int off,u8 *buf,int len,int mode) {
    (void)f; (void)off; (void)mode;
    if(ram_fail) return -EIO;
    int pos=(word-CYCLE_COUNT_WORD)*2;
    assert(pos>=0 && pos+len<=16); memcpy(buf,ram+pos,len); return 0;
}
static int fg_sram_write(struct fg_dev *f,int word,int off,u8 *buf,int len,int mode) {
    (void)f; (void)off; (void)mode;
    if(ram_fail) return -EIO;
    int pos=(word-CYCLE_COUNT_WORD)*2;
    assert(pos>=0 && pos+len<=16); memcpy(ram+pos,buf,len); return 0;
}
static int fg_get_sram_prop(struct fg_dev *f,int prop,int *v) {
    (void)f; assert(prop==FG_SRAM_ACT_BATT_CAP);
    if(cap_fail) return -EIO; *v=actual_cap; return 0;
}
static int hw_cycles=3492, hw_reads, hw_writes, hw_read_fail, hw_write_fail;
static bool burn_on_failure;
static struct power_supply psy;
static struct power_supply *power_supply_get_by_name(const char *n) { (void)n; return &psy; }
static int power_supply_get_property(struct power_supply *p,int prop,union power_supply_propval *v) {
    (void)p; assert(prop==1); hw_reads++;
    if(hw_read_fail) return -EIO; v->intval=hw_cycles; return 0;
}
static int power_supply_set_property(struct power_supply *p,int prop,const union power_supply_propval *v) {
    (void)p; assert(prop==1 && v->intval==1); hw_writes++;
    if(!hw_write_fail || burn_on_failure) hw_cycles++;
    return hw_write_fail ? -EIO : 0;
}
static void reset_hw(void) {
    memset(nvm,0,sizeof(nvm)); memset(ram,0,sizeof(ram));
    nr=nw=nr_fail=nw_fail=nr_short=nw_short=ram_fail=cap_fail=0;
    actual_cap=4513;
}
static void seed_ram(void) {
    for(int i=0;i<8;i++) { int x=1000+i; ram[2*i]=x&255; ram[2*i+1]=x>>8; }
}
static void seed_cookie(void) {
    nvm[0x95]=0x78; nvm[0x96]=0x56; nvm[0x97]=0x34; nvm[0x98]=0x12;
}
'''

FG_TEST = r'''
int main(void) {
    struct cycle_counter c={0};
    struct fg_gen4_chip chip={.fg_nvmem=nvm,.counter=&c};
    u16 out[8], input=0x1234; int v; int64_t capacity;
    c.data=&chip; c.restore_count=fg_gen4_restore_count; c.store_count=fg_gen4_store_count;
    assert(cycle_count_init(&c)==0);
    reset_hw(); seed_ram();
    assert(fg_gen4_restore_count(&chip,out,8)==0);
    assert(out[0]==1000 && out[7]==1007 && nw==0);
    assert(fg_gen4_get_learned_capacity(&chip,&capacity)==0 && capacity==4513000);
    assert(fg_gen4_migrate_sdam(&chip)==0 && nw==3);
    assert(!memcmp(nvm+0x81,ram,16));
    assert(fg_gen4_sdam_cookie_status(&chip)==1);
    assert(fg_gen4_migrate_sdam(&chip)==0 && nw==3);
    memset(ram,0,sizeof(ram));
    assert(restore_cycle_count(&c)==0);
    assert(get_cycle_count(&c,&v)==0 && v==1003);
    assert(fg_gen4_store_count(&chip,&input,3,2)==0);
    assert(ram[6]==0x34 && ram[7]==0x12 && nvm[0x87]==0x34 && nvm[0x88]==0x12);
    assert(fg_gen4_store_count(&chip,&input,7,4)==-EINVAL);
    assert(fg_gen4_store_count(&chip,&input,0,1)==-EINVAL);
    assert(fg_gen4_store_count(&chip,&input,0,0)==-EINVAL);
    assert(fg_gen4_restore_count(&chip,out,-1)==-EINVAL);
    for(int fail=1;fail<=3;fail++) {
        reset_hw(); seed_ram(); nw_fail=fail;
        assert(fg_gen4_migrate_sdam(&chip)==-EIO);
        assert(fg_gen4_sdam_cookie_status(&chip)==0);
        assert(nw==fail);
    }
    for(int fail=1;fail<=3;fail++) {
        reset_hw(); seed_ram(); nw_short=fail;
        assert(fg_gen4_migrate_sdam(&chip)==-EIO);
        assert(fg_gen4_sdam_cookie_status(&chip)==0);
    }
    reset_hw(); seed_ram(); nr_fail=1;
    assert(fg_gen4_migrate_sdam(&chip)==-EIO && nw==0);
    reset_hw(); seed_ram(); ram_fail=1;
    assert(fg_gen4_migrate_sdam(&chip)==-EIO && nw==0);
    reset_hw(); seed_ram(); cap_fail=1;
    assert(fg_gen4_migrate_sdam(&chip)==-EIO && nw==0);
    reset_hw(); seed_cookie(); nr_fail=2;
    memset(out,0x55,sizeof(out));
    assert(fg_gen4_restore_count(&chip,out,8)==-EIO);
    for(int i=0;i<8;i++) assert(out[i]==0x5555);
    reset_hw(); seed_cookie(); nr_short=2;
    assert(fg_gen4_restore_count(&chip,out,8)==-EIO);
    reset_hw(); seed_cookie(); nr_short=2;
    capacity=123; assert(fg_gen4_get_learned_capacity(&chip,&capacity)==-EIO && capacity==123);
    reset_hw(); seed_ram(); chip.fg_nvmem=NULL;
    assert(fg_gen4_migrate_sdam(&chip)==0 && nw==0);
    assert(fg_gen4_restore_count(&chip,out,8)==0 && out[7]==1007);
    chip.fg.soc_reporting_ready=chip.fg.profile_available=true;
    chip.dt.replacement_battery_type="K11A_REPLACEMENT_SAFE";
    chip.replacement_profile_fallback=true;
    assert(restore_cycle_count(&c)==0);
    assert(fg_gen4_get_cycle_count(&chip,&v)==0 && v==1003);
#ifdef CONFIG_BATT_VERIFY_BY_DS28E16
    chip.fg.maxim_cycle_count=3492;
    chip.replacement_profile_fallback=false;
    assert(fg_gen4_get_cycle_count(&chip,&v)==0 && v==3492);
    chip.fg.maxim_cycle_count=INT_MIN;
    assert(fg_gen4_get_cycle_count(&chip,&v)==-ENODATA);
    chip.replacement_profile_fallback=true;
    assert(sync_cycle_count(&chip)==0 && hw_reads==0 && hw_writes==0);
    chip.replacement_profile_fallback=false;
    chip.fg.cycle_count=chip.fg.maxim_cycle_count=INT_MIN;
    for(int i=0;i<8;i++) c.count[i]=0;
    hw_cycles=100; assert(sync_cycle_count(&chip)==0);
    assert(chip.fg.cycle_count==0 && chip.fg.maxim_cycle_count==100);
    assert(hw_writes==0);
    for(int i=0;i<8;i++) c.count[i]=1;
    assert(sync_cycle_count(&chip)==0 && hw_writes==1 && hw_cycles==101);
    assert(sync_cycle_count(&chip)==0 && hw_writes==1);
    for(int i=0;i<8;i++) c.count[i]=2;
    hw_write_fail=1; burn_on_failure=true;
    assert(sync_cycle_count(&chip)==-EIO && hw_writes==2 && hw_cycles==102);
    hw_write_fail=0; burn_on_failure=false;
    assert(sync_cycle_count(&chip)==0 && hw_writes==2);
    for(int i=0;i<8;i++) c.count[i]=500;
    assert(sync_cycle_count(&chip)==0 && hw_writes==2);
    chip.fg.maxim_cycle_count=INT_MIN; hw_read_fail=1;
    assert(sync_cycle_count(&chip)==-EIO && chip.fg.maxim_cycle_count==INT_MIN);
    hw_read_fail=0;
#endif
    chip.fg.profile_available=false;
    assert(fg_gen4_get_cycle_count(&chip,&v)==-ENODATA);
    u8 raw[3]={0xff,0xff,1}; v=-1;
    assert(ds28e16_decode_cycle_count(raw,&v)==0 && v==0);
    int r=DC_INIT_VALUE-3492; raw[0]=r; raw[1]=r>>8; raw[2]=r>>16;
    assert(ds28e16_decode_cycle_count(raw,&v)==0 && v==3492);
    raw[2]=2; v=42;
    assert(ds28e16_decode_cycle_count(raw,&v)==-ERANGE && v==42);
    puts("PASS: FG source selection, DS counter range, exact reads/writes, migration and failure ordering");
    return 0;
}
'''


def compile_run(code, label, defines=()):
    with tempfile.TemporaryDirectory(prefix='alioth-cycle-test-') as td:
        src=Path(td)/'test.c'; exe=Path(td)/'test'
        src.write_text(code)
        subprocess.run([os.environ.get('CC','cc'), '-std=gnu11', '-Wall','-Wextra','-Werror',
                        '-Wno-unused-function','-Wno-unused-parameter','-Wno-sign-compare',
                        '-Wno-misleading-indentation', '-fsanitize=undefined',
                        '-fno-sanitize-recover=all','-pthread',*defines,str(src),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
    print('Compiled actual source functions:',label,flush=True)


def main():
    h=Path('drivers/power/supply/qcom/fg-alg.h').read_text()
    a=Path('drivers/power/supply/qcom/fg-alg.c').read_text()
    struct=re.search(r'^struct cycle_counter \{.*?^\};',h,re.S|re.M).group(0)
    funcs='\n\n'.join(function(a,n) for n in ('restore_cycle_count','clear_cycle_count',
        'store_cycle_count','cycle_count_update','get_cycle_count','get_cycle_counts','cycle_count_init'))
    common=PRE+struct+'\n'+funcs+'\n'
    compile_run(common+COMMON_TEST,'cycle counter')
    f=Path('drivers/power/supply/qcom/qpnp-fg-gen4.c').read_text()
    if 'fg_gen4_sdam_cookie_status' not in f:
        print('FG follow-up not applied yet; FG/DS tests skipped')
        return
    d=Path('drivers/power/supply/maxim/ds28e16.c').read_text()
    names=('fg_gen4_sdam_cookie_status','fg_gen4_restore_count','fg_gen4_store_count',
           'fg_gen4_migrate_sdam','fg_gen4_get_learned_capacity',
           'fg_gen4_uses_fg_cycle_count','fg_gen4_get_cycle_count')
    fgfuncs='\n\n'.join(function(f,n) for n in names)
    sync=function(f,'sync_cycle_count')
    decoder=function(d,'ds28e16_decode_cycle_count')
    for ds in (False,True):
        code=common+HW+fgfuncs+'\n'+decoder+'\n'
        if ds: code+=sync+'\n'
        code+=FG_TEST
        compile_run(code,'FG persistence/source selection '+('DS enabled' if ds else 'DS disabled'),
                    ['-DCONFIG_BATT_VERIFY_BY_DS28E16'] if ds else [])
    assert 'clear_cycle_count' not in function(f,'profile_load_work')
    assert 'fg_gen4_clear_sdam' not in f
    get=function(d,'verify_get_property')
    block=get.split('case POWER_SUPPLY_PROP_MAXIM_BATT_CYCLE_COUNT:',1)[1].split('default:',1)[0]
    assert 'if (ret != DS_TRUE)' in block and 'return -EIO' in block
    assert 'ds28e16_decode_cycle_count' in block
    assert 'counter->id' not in function(a,'get_cycle_count')
    assert 'counter->id' not in function(a,'get_cycle_counts')
    print('PASS: cycle source/migration integration invariants')

if __name__=='__main__':
    main()
