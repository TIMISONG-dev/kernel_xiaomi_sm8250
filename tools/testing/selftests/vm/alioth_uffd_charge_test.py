#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile actual UFFD functions with a deterministic immediate-LRU-drain model.
This is a host ordering/ownership regression, not a hardware kernel stress test.
"""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[4]

def function(text, name):
    import re
    m = re.search(r'^(?:static )?(?:int|void|bool) ' + name + r'\([^;]*?\n\{', text, re.M)
    if not m:
        raise AssertionError('Function not found: ' + name)
    start = m.start(); begin = text.index('{', start); depth = 0
    for i in range(begin, len(text)):
        if text[i] == '{': depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0: return text[start:i + 1]
    raise AssertionError('Unterminated function: ' + name)

PRELUDE = r'''
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#define __user
#define PAGE_SIZE 4096
#define VM_WRITE 1
#define VM_SHARED 2
#define GFP_HIGHUSER_MOVABLE 0
#define GFP_KERNEL 0
#define DIV_ROUND_UP(a,b) (((a)+(b)-1)/(b))
#define unlikely(x) (x)
typedef int pte_t;
typedef int pmd_t;
typedef int spinlock_t;
typedef unsigned long pgoff_t;
struct mem_cgroup { int id; } cg = {1};
struct page { bool cached, anon, lru, released; int owner, refs; } page0;
struct inode { unsigned long size; } inode0;
struct file { struct inode *f_inode; } file0 = {&inode0};
struct mm_struct { int unused; } mm0;
struct vm_area_struct { int vm_flags, vm_page_prot; bool shmem; struct file *vm_file; } vma;
static pte_t pte;
static bool lock_held, disabled, copy_fails, charge_fails, alloc_fails, publication_uncharge;
static int reserved, commits, cancels, freed, violations, lru_count[2];
static char contents[PAGE_SIZE];
static struct page *alloc_page_vma(int flags, struct vm_area_struct *v, unsigned long addr)
{ (void)flags;(void)v;(void)addr; if (alloc_fails) return NULL; page0=(struct page){.refs=1}; return &page0; }
static void *kmap_atomic(struct page *p) { (void)p;return contents; }
static void kunmap_atomic(void *p) { (void)p; }
static int copy_from_user(void *dst, const void *src, size_t n)
{ (void)dst;(void)src;(void)n;return copy_fails; }
static void flush_dcache_page(struct page *p) { (void)p; }
static void __SetPageUptodate(struct page *p) { (void)p; }
static int mem_cgroup_try_charge(struct page *p, struct mm_struct *m, int flags,
                                struct mem_cgroup **c, bool compound)
{ (void)p;(void)m;(void)flags;(void)compound;if(charge_fails)return -ENOMEM;
  *c=disabled?NULL:&cg;reserved+=!disabled;return 0; }
static void mem_cgroup_cancel_charge(struct page *p, struct mem_cgroup *c, bool huge)
{ (void)p;(void)huge;if(c){reserved--;cancels++;} }
static void mem_cgroup_commit_charge(struct page *p, struct mem_cgroup *c, bool lrucare, bool huge)
{ (void)lrucare;(void)huge;if(!c)return;
  if(!lock_held || !p->anon || p->lru || p->released)violations++;
  p->owner=c->id;reserved--;commits++; }
static void put_page(struct page *p) { if(--p->refs==0){ p->released=true;freed++; } }
static pte_t mk_pte(struct page *p, int prot) { (void)p;(void)prot;return 1; }
static pte_t pte_mkdirty(pte_t p) { return p; }
static pte_t pte_mkwrite(pte_t p) { return p; }
static pte_t *pte_offset_map_lock(struct mm_struct *m,pmd_t *d,unsigned long a,spinlock_t **l)
{ (void)m;(void)d;(void)a;static int unused;*l=&unused;lock_held=true;return &pte; }
static void release_mapped_page(struct page *p);
static void pte_unmap_unlock(pte_t *p,spinlock_t *l)
{ (void)p;(void)l;lock_held=false;
  /* Model another CPU unmapping immediately after the PTE lock is dropped. */
  if(publication_uncharge && pte)release_mapped_page(&page0); }
static bool vma_is_shmem(struct vm_area_struct *v) {return v->shmem;}
static unsigned long linear_page_index(struct vm_area_struct *v,unsigned long a) { (void)v;return a/PAGE_SIZE; }
static unsigned long i_size_read(struct inode *i) {return i->size;}
static bool pte_none(pte_t p) {return !p;}
static bool page_mapping(struct page *p) {return p->cached;}
static void page_add_file_rmap(struct page *p,bool h) {(void)p;(void)h;}
static void page_add_new_anon_rmap(struct page *p,struct vm_area_struct *v,unsigned long a,bool h)
{(void)v;(void)a;(void)h;p->anon=true;}
static int mm_counter(struct page *p) {(void)p;return 0;}
static void inc_mm_counter(struct mm_struct *m,int c) {(void)m;(void)c;}
static void lru_cache_add_active_or_unevictable(struct page *p,struct vm_area_struct *v)
{ (void)v; /* Force the legal full-pagevec/drain case synchronously. */
  p->lru=true;lru_count[p->owner]++; }
static void release_mapped_page(struct page *p)
{ if(p->lru){lru_count[p->owner]--;p->lru=false;}put_page(p); }
static void set_pte_at(struct mm_struct *m,unsigned long a,pte_t *p,pte_t val)
{(void)m;(void)a;*p=val;}
static void update_mmu_cache(struct vm_area_struct *v,unsigned long a,pte_t *p)
{(void)v;(void)a;(void)p;}
static void reset(void)
{page0=(struct page){.refs=1};vma=(struct vm_area_struct){.vm_flags=VM_WRITE,.vm_file=&file0};
 inode0.size=PAGE_SIZE*2;pte=0;lock_held=disabled=copy_fails=charge_fails=alloc_fails=publication_uncharge=false;
 reserved=commits=cancels=freed=violations=lru_count[0]=lru_count[1]=0;}
#define CHECK(x) do{if(!(x)){fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x);return 1;}}while(0)
'''
TEST = r'''
int main(void) {
    struct page *retry = NULL;
    pmd_t pmd = 0;
    reset();
    CHECK(!mcopy_atomic_pte(&mm0,&pmd,&vma,0,0,&retry));
    release_mapped_page(&page0);
    CHECK(!violations && !lru_count[0] && !lru_count[1] && !reserved && commits==1);
    reset(); publication_uncharge=true;
    CHECK(!mcopy_atomic_pte(&mm0,&pmd,&vma,0,0,&retry));
    CHECK(!violations && commits==1 && freed==1 && !reserved && !lru_count[1]);
    reset(); pte=1;
    CHECK(mcopy_atomic_pte(&mm0,&pmd,&vma,0,0,&retry)==-EEXIST);
    CHECK(!commits && cancels==1 && !reserved && freed==1 && !lock_held);
    reset(); charge_fails=true;
    CHECK(mcopy_atomic_pte(&mm0,&pmd,&vma,0,0,&retry)==-ENOMEM);
    CHECK(!commits && !cancels && freed==1 && !reserved);
    reset(); alloc_fails=true;
    CHECK(mcopy_atomic_pte(&mm0,&pmd,&vma,0,0,&retry)==-ENOMEM);
    reset(); copy_fails=true;
    CHECK(mcopy_atomic_pte(&mm0,&pmd,&vma,0,0,&retry)==-ENOENT && retry);
    CHECK(!commits && !cancels && !freed);
    copy_fails=false;
    CHECK(!mcopy_atomic_pte(&mm0,&pmd,&vma,0,0,&retry) && !retry);
    release_mapped_page(&page0);
    CHECK(!violations && !reserved && commits==1 && !lru_count[1]);
    reset(); disabled=true;
    CHECK(!mcopy_atomic_pte(&mm0,&pmd,&vma,0,0,&retry));
    release_mapped_page(&page0);
    CHECK(!commits && !cancels && !reserved && !lru_count[0]);
    reset();page0.cached=true;page0.owner=1;vma.shmem=true;
    CHECK(!mfill_atomic_install_pte(&mm0,&pmd,&vma,0,&page0,true,NULL));
    CHECK(!commits && lru_count[1]==1);release_mapped_page(&page0);
    reset();page0.cached=true;page0.owner=1;vma.shmem=true;inode0.size=0;
    CHECK(mfill_atomic_install_pte(&mm0,&pmd,&vma,0,&page0,false,NULL)==-EFAULT);
    CHECK(!commits && !lru_count[1] && !lock_held);
    puts("PASS: UFFD ownership, immediate drain, PTE visibility, rollback, retry, shmem, !MEMCG");
    return 0;
}
'''

def main():
    s = (ROOT/'mm/userfaultfd.c').read_text()
    sh = (ROOT/'mm/shmem.c').read_text()
    # Check committed shmem failures skip reservation cancellation.
    cleanup=sh.split('out_delete_from_cache:',1)[1].split('out_release:',1)[0]
    assert cleanup.index('goto out_release;') < cleanup.index('mem_cgroup_cancel_charge')
    src=PRELUDE+'\n'+function(s,'mfill_atomic_install_pte')+'\n'+function(s,'mcopy_atomic_pte')+'\n'+TEST
    with tempfile.TemporaryDirectory() as td:
        p=Path(td);(p/'test.c').write_text(src)
        subprocess.run([os.environ.get('CC','cc'),'-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=undefined','-o',str(p/'test'),str(p/'test.c')],check=True)
        subprocess.run([str(p/'test')],check=True)
if __name__=='__main__':main()
