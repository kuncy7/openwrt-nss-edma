# SPDX-License-Identifier: GPL-2.0-only
"""Compile the real driver against an MDIO/PHY simulator; no hardware access."""
from pathlib import Path
import subprocess
import tempfile

shim = r'''

#define _GNU_SOURCE
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint32_t u32;
typedef uint16_t u16;
typedef uint8_t u8;
#define BIT(n) (1U << (n))
#define GENMASK(h,l) ((~0U << (l)) & (~0U >> (31-(h))))
#define FIELD_PREP(mask,val) (((u32)(val) << __builtin_ctz(mask)) & (mask))
#define module_param(...)
#define MODULE_PARM_DESC(...)
#define MODULE_LICENSE(...)
#define MODULE_DESCRIPTION(...)
#define module_init(...)
#define module_exit(...)
#define __init
#define __exit
#define GFP_KERNEL 0
#define pr_info(...) ((void)0)
#define pr_warn(...) ((void)0)
#define pr_err(...) ((void)0)
#define usleep_range(...) ((void)0)
#define MII_BMCR 0
#define BMCR_PDOWN BIT(11)
#define BMCR_ANENABLE BIT(12)
#define BMCR_ANRESTART BIT(9)
struct mii_bus { int unused; };
struct device { int unused; };
struct phy_device { struct { struct mii_bus *bus; } mdio; };
static struct mii_bus fake_bus;
static struct device fake_device;
static struct phy_device fake_phy = { .mdio = { .bus = &fake_bus } };
static int mdio_bus_type;
static int fail_at, operations, alloc_fail, allocations, unsafe_enable;
static int stuck_vtu, full_vtu, stuck_atu, drop_lookup, drop_vtu;
static u16 words[4096], page;
static u32 vtu[4096];
static u32 reg32(unsigned reg) { return words[reg/2] | ((u32)words[reg/2+1]<<16); }
static void set32(unsigned reg,u32 value) { words[reg/2]=value; words[reg/2+1]=value>>16; }
static int fault(void) { return ++operations == fail_at; }
static bool policy_ready(void);
static int missing_device;
static int mdiobus_write(struct mii_bus *bus,int phy,int r,u16 value) {
    (void)bus;
    if(fault()) return -EIO;
    if(phy==0x18 && r==0) { page=value; return 0; }
    unsigned addr=((unsigned)page<<9)|((phy&7)<<6)|(r<<1);
    if(drop_lookup && addr==0x66c && (value&0x7f)) return 0;
    assert(addr/2<4096); words[addr/2]=value;
    if(addr>=0x7c && addr<=0x94 && (addr-0x7c)%4==0 && (value&12))
        if(!policy_ready()) unsafe_enable++;
    /* Automatic link state can clear RX/TX on an unplugged external port. */
    if(addr>=0x7c && addr<=0x94 && (addr-0x7c)%4==0 && (value&BIT(9)))
        words[addr/2]&=~12;
    if(addr==0x616 && (reg32(0x614)&BIT(31))) {
        u32 command=reg32(0x614);
        unsigned vid=(command>>16)&4095;
        if((command&7)==2) {
            if(!drop_vtu) vtu[vid]=reg32(0x610);
            set32(0x610,0);
        } else if((command&7)==6) set32(0x610,vtu[vid]);
        if(!stuck_vtu) set32(0x614,(command&~BIT(31))|(full_vtu?BIT(4):0));
    }
    if(addr==0x60e && !stuck_atu) set32(0x60c,reg32(0x60c)&~BIT(31));
    return 0;
}
static int mdiobus_read(struct mii_bus *bus,int phy,int r) {
    (void)bus;
    if(fault()) return -EIO;
    unsigned addr=((unsigned)page<<9)|((phy&7)<<6)|(r<<1);
    assert(addr/2<4096); return words[addr/2];
}
static struct device *bus_find_device_by_name(void *a,void *b,const char *c) {
    (void)a;(void)b;return missing_device && !strcmp(c,"missing")?NULL:&fake_device;
}
static struct phy_device *to_phy_device(struct device *d) { (void)d;return &fake_phy; }
static void put_device(struct device *d) { (void)d; }
static int phy_read(struct phy_device *p,int reg) { (void)p;(void)reg;return fault()?-EIO:0; }
static int phy_write(struct phy_device *p,int reg,int val) { (void)p;(void)reg;(void)val;return fault()?-EIO:0; }
static char *kstrdup(const char *s,int flags) { (void)flags;return ++allocations==alloc_fail?NULL:strdup(s); }
#define kfree free
static int kstrtouint(const char *s,unsigned base,unsigned *result) {
    char *end;errno=0;unsigned long value=strtoul(s,&end,base);
    if(errno || end==s || *end || value>UINT32_MAX) return -EINVAL;
    *result=value;return 0;
}

'''

test = r'''

struct layout {
    const char *name, *map;
    unsigned port_mask, cpu;
    u32 lookup[7];
    u16 pvid[7];
    unsigned vlan_members[2], tagged[2];
};
static const struct layout layouts[] = {
    {"RA74 single", "1:6t,2u,3u,4u;2:6t,1u", 0x5e, 6,
     {0,0x140340,0x140358,0x140354,0x14034c,0,0x14031e},
     {0,2,1,1,1,0,0}, {0x5c,0x42}, {0x40,0x40}},
    {"RA74 dual", "1:6t,2u,3u,4u;2:5t,1u", 0x7e, 6,
     {0,0x140320,0x140358,0x140354,0x14034c,0x140302,0x14031c},
     {0,2,1,1,1,0,0}, {0x5c,0x22}, {0x40,0x20}},
    {"CPU port 0", "1:0t,2u,3u;2:0t,1u", 0xf, 0,
     {0x14030e,0x140301,0x140309,0x140305,0,0,0},
     {0,2,1,1,0,0,0}, {0xd,0x3}, {1,1}},
    {"PHY-linked flat", "", 0x1e, 255,
     {0,0x14001c,0x14001a,0x140016,0x14000e,0,0},
     {0}, {0}, {0}},
};
static const struct layout *current;
static bool policy_ready(void) {
    for(int p=0;p<7;p++) {
        if(reg32(0x660+p*12)!=current->lookup[p]) return false;
        if(current->pvid[p] && (reg32(0x420+p*8)&4095)!=current->pvid[p]) return false;
        if(current->pvid[p] && ((reg32(0xc70+4*(p/2))>>(16*(p%2)))&4095)!=current->pvid[p]) return false;
    }
    for(int v=0;v<2;v++) {
        if(!current->vlan_members[v]) continue;
        if(!(vtu[v+1]&BIT(20))) return false;
        for(int p=0;p<7;p++) {
            unsigned expected=!(current->vlan_members[v]&BIT(p))?3:
                              (current->tagged[v]&BIT(p))?2:1;
            if(((vtu[v+1]>>(4+2*p))&3)!=expected) return false;
        }
    }
    return true;
}
static void reset(void) {
    memset(words,0,sizeof(words));memset(vtu,0,sizeof(vtu));
    fail_at=operations=alloc_fail=allocations=unsafe_enable=g8_error=page=0;
    stuck_vtu=full_vtu=stuck_atu=drop_lookup=drop_vtu=missing_device=0;
    ports=current->port_mask;cpu_port=current->cpu;switch_fixup=true;
    vlans=(char *)current->map;bus_via="bus";
    wake_phys="phy0,phy1,phy2,phy3,phy4";
}
static void assert_blocked(void) {
    for(int p=0;p<7;p++) {
        assert(!(reg32(G8_PORT_STATUS(p))&(12|BIT(9))));
        assert(!(reg32(G8_PORT_LOOKUP(p))&0x7007f));
    }
}
int main(void) {
    for(unsigned layout=0;layout<sizeof(layouts)/sizeof(layouts[0]);layout++) {
        current=&layouts[layout];reset();
        assert(qca8337_nss_init()==0);assert(policy_ready());assert(!unsafe_enable);
        int count=operations;
        for(int p=0;p<7;p++) {
            if(!(ports&BIT(p))) assert(!(reg32(G8_PORT_STATUS(p))&(12|BIT(9))));
            else if((unsigned)p==cpu_port) assert((reg32(G8_PORT_STATUS(p))&0x4f)==0x4e);
            else assert(reg32(G8_PORT_STATUS(p))&BIT(9));
        }
        for(int n=1;n<=count;n++) {
            reset();fail_at=n;
            assert(qca8337_nss_init()<0);assert(!unsafe_enable);assert_blocked();
        }
        for(int n=1;n<=(*current->map?2:1);n++) {
            reset();alloc_fail=n;assert(qca8337_nss_init()<0);assert(!unsafe_enable);assert_blocked();
        }
        printf("PASS: %s, %d individual MDIO/PHY faults, allocation failures, no early MAC enable\n",current->name,count);
    }
    current=&layouts[0];
    int *hardware_faults[]={&stuck_vtu,&full_vtu,&stuck_atu,&drop_lookup,&drop_vtu};
    for(unsigned n=0;n<sizeof(hardware_faults)/sizeof(hardware_faults[0]);n++) {
        reset();*hardware_faults[n]=1;
        assert(qca8337_nss_init()<0);assert(!unsafe_enable);assert_blocked();
    }
    const char *bad[]={";", "1:", "1:6t,2x", "1:6t,2u;2:6t,7u", "4095:6t,1u", "1:6t,5u"};
    for(unsigned n=0;n<sizeof(bad)/sizeof(bad[0]);n++) {
        reset();vlans=(char *)bad[n];assert(qca8337_nss_init()<0);assert(!unsafe_enable);assert_blocked();
    }
    reset();missing_device=1;bus_via="missing";assert(qca8337_nss_init()==-ENODEV);assert(!operations);
    reset();missing_device=1;wake_phys="missing";assert(qca8337_nss_init()==-ENODEV);assert_blocked();
    reset();wake_phys="";assert(qca8337_nss_init()==0);assert(policy_ready());
    reset();ports&=~BIT(6);assert(qca8337_nss_init()==0);assert(policy_ready());
    reset();switch_fixup=false;assert(qca8337_nss_init()==0);assert_blocked();
    int count=operations;
    for(int n=1;n<=count;n++) {
        reset();switch_fixup=false;fail_at=n;assert(qca8337_nss_init()==-EIO);assert_blocked();
    }
    reset();switch_fixup=false;wake_phys="";assert(qca8337_nss_init()==0);assert(!operations);
    /* Partial maps retain flat forwarding on unlisted ports. */
    reset();vlans="1:6t,2u";assert(qca8337_nss_init()==0);
    assert(reg32(G8_PORT_LOOKUP(1))==0x14005c);
    assert(reg32(G8_PORT_LOOKUP(2))==0x140340);
    assert(reg32(G8_PORT_LOOKUP(6))==0x140304);
    puts("PASS: table/readback faults, invalid maps, missing devices, empty wake list, CPU auto-inclusion, wake-only and partial maps");
    return 0;
}

'''

tree = Path(__file__).resolve().parents[4]
source = (tree/'package/kernel/qca8337-nss/src/qca8337_nss.c').read_text(encoding='utf-8')
source = '\n'.join(line for line in source.splitlines() if not line.startswith('#include <linux/'))
with tempfile.TemporaryDirectory(prefix='qca8337-check-') as directory:
    cfile=Path(directory)/'check.c'
    binary=Path(directory)/'check'
    cfile.write_text(shim+'\n'+source+'\n'+test,encoding='utf-8')
    subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-Wno-unused-function','-fsanitize=address,undefined','-g',str(cfile),'-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
