# SPDX-License-Identifier: GPL-2.0-only
"""Run the real AN8855 mode/VLAN routines against a fault-injecting regmap.

No router or kernel build is needed. The driver routines are compiled from the
generic AN8855 driver with this target's an8855 patches applied, as the build
applies them; the regmap is a model that can fail any single access. The DSA
core around them (tag_8021q registration, bridge join and leave, CPU-port VLAN
references) is not compiled but modelled below, after net/dsa/tag_8021q.c with
patch 0959a: no retries, failures logged through dev_err, and a failed
registration tears down only the ports it had set up. The test checks register programming, VLAN membership,
PVID transitions and recovery from each individual I/O error. It cannot
validate the switch's on-wire interpretation of those registers.
"""

from pathlib import Path
import os
import re
import shlex
import subprocess
import tempfile

target = Path(__file__).resolve().parents[1]


def patched_driver():
    """The generic AN8855 driver with this target's an8855 patches applied."""
    generic = target.parent / "generic/files-6.18"
    with tempfile.TemporaryDirectory() as tree:
        dsa = Path(tree) / "drivers/net/dsa"
        dsa.mkdir(parents=True)
        for name in ("an8855.c", "an8855.h"):
            (dsa / name).write_text(
                (generic / "drivers/net/dsa" / name).read_text())
        for patch in sorted((target / "patches-6.18").glob("*-net-dsa-an8855-*.patch")):
            subprocess.run(["patch", "-p1", "-s", "-d", tree, "-i", str(patch)],
                           check=True)
        return (dsa / "an8855.c").read_text(), (dsa / "an8855.h").read_text()


source, header = patched_driver()
header = re.sub(r"^#include.*$", "", header, flags=re.MULTILINE)


def function(name):
    match = re.search(
        rf"^static [^;{{}}]*?\b{name}\s*\([^;{{}}]*\)"
        r"\s*(?:__must_hold\([^)]*\))?\s*\{", source, re.MULTILINE
    )
    if not match:
        raise AssertionError(f"Function not found: {name}")
    start = match.start()
    depth = 1
    end = match.end()
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


stubs = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
#define BIT(n) (1U << (n))
#define GENMASK(h, l) ((~0U >> (31 - (h))) & (~0U << (l)))
#define FIELD_PREP(m, v) (((u32)(v) << __builtin_ctz(m)) & (m))
#define FIELD_PREP_CONST(m, v) FIELD_PREP(m, v)
#define FIELD_GET(m, v) (((v) & (m)) >> __builtin_ctz(m))
#define FIELD_MAX(m) FIELD_GET(m, m)
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define VLAN_N_VID 4096
#define BITS_TO_LONGS(n) (((n) + 63) / 64)
#define DECLARE_BITMAP(name, n) unsigned long name[BITS_TO_LONGS(n)]
#define GFP_KERNEL 0
#define struct_size(p, member, n) (sizeof(*(p)) + sizeof((p)->member[0]) * (n))
static void *kzalloc(size_t n, int flags) { (void)flags; return calloc(1, n); }
#define kfree(p) free(p)
static bool test_bit(unsigned n, const unsigned long *p) { return (p[n / 64] >> (n % 64)) & 1; }
static void set_bit(unsigned n, unsigned long *p) { p[n / 64] |= 1UL << (n % 64); }
static void clear_bit(unsigned n, unsigned long *p) { p[n / 64] &= ~(1UL << (n % 64)); }
static bool test_and_clear_bit(unsigned n, unsigned long *p) { bool old = test_bit(n, p); clear_bit(n, p); return old; }
static void __assign_bit(unsigned n, unsigned long *p, bool on) { if (on) set_bit(n, p); else clear_bit(n, p); }
static void bitmap_zero(unsigned long *p, unsigned n) { memset(p, 0, BITS_TO_LONGS(n) * sizeof(*p)); }
static void bitmap_or(unsigned long *p, const unsigned long *a, const unsigned long *b, unsigned n) {
    for (unsigned i = 0; i < BITS_TO_LONGS(n); i++) p[i] = a[i] | b[i];
}
static unsigned find_first_bit(const unsigned long *p, unsigned n) {
    for (unsigned i = 0; i < n; i++) if (test_bit(i, p)) return i;
    return n;
}
static bool bitmap_empty(const unsigned long *p, unsigned n) {
    for (unsigned i = 0; i < BITS_TO_LONGS(n); i++) if (p[i]) return false;
    return true;
}
static unsigned bitmap_weight(const unsigned long *p, unsigned n) {
    unsigned result = 0;
    for (unsigned i = 0; i < n; i++) result += test_bit(i, p);
    return result;
}
#define for_each_set_bit(i, p, n) for ((i) = 0; (i) < (n); (i)++) if (test_bit(i, p))
#define __must_hold(m)
#define BRIDGE_VLAN_INFO_PVID BIT(1)
#define BRIDGE_VLAN_INFO_UNTAGGED BIT(2)
#define NETIF_F_HW_VLAN_CTAG_FILTER BIT(0)
#define ETH_P_8021Q 0x8100
#define htons(x) (x)
#define NL_SET_ERR_MSG_MOD(a, b) ((void)(a))
static int dev_errors;
#define dev_err(...) ((void)dev_errors++)
struct mutex { int held; };
struct phylink_pcs { int dummy; };
struct netlink_ext_ack { int dummy; };
struct net_device { u64 features; bool upper, vlan, filtering, running; u16 proto, vid; };
struct switchdev_obj { struct net_device *orig_dev; };
struct switchdev_obj_port_vlan { struct switchdev_obj obj; u16 vid, flags; };
struct netdev_notifier_info { struct netlink_ext_ack *extack; };
struct netdev_notifier_changeupper_info {
    struct netdev_notifier_info info;
    bool linking;
    struct net_device *upper_dev;
};
enum dsa_tag_protocol { DSA_TAG_PROTO_MTK = 1, DSA_TAG_PROTO_AN8855_8021Q = 35 };
struct dsa_switch;
struct dsa_port {
    int index;
    struct dsa_switch *ds;
    struct net_device *user, *bridge;
    bool filtering;
};
struct dsa_bridge { struct net_device *dev; unsigned num; };
struct dsa_switch {
    void *priv, *tag_8021q_ctx;
    struct dsa_port ports[6];
    u32 users, cpus;
    bool vlan_upper_filtering_always;
};
#define dsa_switch_for_each_port(dp, ds) \
    for ((dp) = (ds)->ports; (dp) < (ds)->ports + 6; (dp)++)
#define dsa_switch_for_each_user_port(dp, ds) \
    dsa_switch_for_each_port(dp, ds) if ((ds)->users & BIT((dp)->index))
#define dsa_switch_for_each_cpu_port(dp, ds) \
    dsa_switch_for_each_port(dp, ds) if ((ds)->cpus & BIT((dp)->index))
static struct dsa_port *dsa_to_port(struct dsa_switch *ds, int port) { return &ds->ports[port]; }
static u32 dsa_user_ports(struct dsa_switch *ds) { return ds->users; }
static bool dsa_is_user_port(struct dsa_switch *ds, int port) { return ds->users & BIT(port); }
static bool dsa_is_cpu_port(struct dsa_switch *ds, int port) { return ds->cpus & BIT(port); }
static bool dsa_port_is_vlan_filtering(struct dsa_port *dp) { return dp->filtering; }
static struct net_device *dsa_port_bridge_dev_get(struct dsa_port *dp) { return dp->bridge; }
static bool dsa_port_offloads_bridge_dev(struct dsa_port *dp, const struct net_device *dev) { return dp->bridge == dev; }
static bool br_vlan_enabled(struct net_device *dev) { return dev->filtering; }
static int br_vlan_get_proto(struct net_device *dev, u16 *proto) { *proto = dev->proto ?: ETH_P_8021Q; return 0; }
static u16 dsa_tag_8021q_standalone_vid(struct dsa_port *dp) { return 3072 + dp->index; }
static u16 dsa_tag_8021q_bridge_vid(unsigned num) { return 3072 | ((num & 3) << 4) | ((num & 4) << 7); }
static bool netif_running(const struct net_device *dev) { return dev->running; }
static bool netdev_has_any_upper_dev(struct net_device *dev) { return dev->upper; }
static bool is_vlan_dev(struct net_device *dev) { return dev->vlan; }
static u16 vlan_dev_vlan_proto(struct net_device *dev) { return dev->proto; }
static u16 vlan_dev_vlan_id(struct net_device *dev) { return dev->vid; }
static struct netlink_ext_ack *netdev_notifier_info_to_extack(struct netdev_notifier_info *info) { return info->extack; }
static bool vid_is_dsa_8021q(u16 vid) { return (vid & 0xc00) == 0xc00; }
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = 1; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = 0; }
struct regmap {
    struct { u32 address, value; } regs[128];
    int count, operations, fail_at;
    u32 vlans[4096];
};
static u32 *reg(struct regmap *m, u32 address) {
    for (int i = 0; i < m->count; i++)
        if (m->regs[i].address == address) return &m->regs[i].value;
    assert(m->count < 128);
    m->regs[m->count].address = address;
    return &m->regs[m->count++].value;
}
static int io(struct regmap *m) { return ++m->operations == m->fail_at ? -EIO : 0; }
static int regmap_read(struct regmap *m, u32 address, u32 *value) {
    if (io(m)) return -EIO;
    *value = *reg(m, address);
    return 0;
}
static int regmap_write(struct regmap *m, u32 address, u32 value);
static int regmap_update_bits(struct regmap *m, u32 address, u32 mask, u32 value) {
    if (io(m)) return -EIO;
    u32 *v = reg(m, address);
    *v = (*v & ~mask) | (value & mask);
    return 0;
}
#define regmap_set_bits(m, r, b) regmap_update_bits(m, r, b, b)
#define regmap_clear_bits(m, r, b) regmap_update_bits(m, r, b, 0)
#define regmap_read_poll_timeout(m, r, v, condition, delay, timeout) \
    (regmap_read(m, r, &(v)) ?: ((condition) ? 0 : -ETIMEDOUT))
static int dsa_tag_8021q_register(struct dsa_switch *ds, u16 proto);
static void dsa_tag_8021q_unregister(struct dsa_switch *ds);
static int dsa_tag_8021q_bridge_join(struct dsa_switch *ds, int port,
    struct dsa_bridge bridge, bool *tx, struct netlink_ext_ack *extack);
static void dsa_tag_8021q_bridge_leave(struct dsa_switch *ds, int port, struct dsa_bridge bridge);
'''

model = r'''
static int regmap_write(struct regmap *m, u32 address, u32 value) {
    if (io(m)) return -EIO;
    *reg(m, address) = value;
    if (address == AN8855_VTCR) {
        unsigned vid = FIELD_GET(AN8855_VTCR_VID, value);
        unsigned command = FIELD_GET(AN8855_VTCR_FUNC, value);
        if (command == AN8855_VTCR_RD_VID)
            *reg(m, AN8855_VARD0) = m->vlans[vid];
        else if (command == AN8855_VTCR_WR_VID)
            m->vlans[vid] = *reg(m, AN8855_VAWD0);
        else assert(false);
        *reg(m, address) &= ~AN8855_VTCR_BUSY;
    }
    return 0;
}
'''

arrays = source[source.index("static const u32 an8855_8021q_flood_regs"):
                source.index("static int an8855_8021q_save_state")]
names = ["an8855_is_8021q", "an8855_vlan_cmd", "an8855_vlan_add",
         "an8855_vlan_del", "an8855_port_set_vlan_mode", "an8855_port_set_pid",
         "an8855_port_commit_vlan",
         "an8855_port_set_status", "an8855_vlan_transaction_capture_port",
         "an8855_vlan_transaction_begin", "an8855_vlan_transaction_restore",
         "an8855_8021q_bridge_dev",
         "an8855_8021q_is_filtering", "an8855_8021q_commit_pvid",
         "an8855_8021q_vlan_owner", "an8855_8021q_sync_vlan",
         "an8855_8021q_tag_membership", "an8855_8021q_finish_leave",
         "an8855_8021q_set_filtering",
         "an8855_8021q_customer_vlan",
         "an8855_port_vlan_filtering", "an8855_port_vlan_add", "an8855_port_vlan_del",
         "an8855_8021q_save_state", "an8855_8021q_restore_port",
         "an8855_8021q_cpu_port_mode", "an8855_8021q_port_mode",
         "an8855_tag_8021q_vlan_add", "an8855_tag_8021q_vlan_del",
         "an8855_8021q_port_tx_vlan",
         "an8855_user_vlan_filter_feature", "an8855_change_tag_protocol",
         "an8855_port_prechangeupper", "an8855_trap_special_frames",
         "an8855_update_port_member", "an8855_port_bridge_join", "an8855_port_bridge_leave"]

checks = r'''
static int added[5];
static int cpu_refs[4096];
/* net/dsa/tag_8021q.c with 0959a: the user port first, then the CPU port,
 * whose membership is reference counted; a failed CPU add unwinds the user
 * port. */
static int core_add(struct dsa_switch *ds, int port, u16 vid) {
    int ret = an8855_tag_8021q_vlan_add(ds, port, vid, 6);
    if (ret) return ret;
    if (!cpu_refs[vid]) {
        ret = an8855_tag_8021q_vlan_add(ds, 5, vid, 0);
        if (ret) { assert(!an8855_tag_8021q_vlan_del(ds, port, vid)); return ret; }
    }
    cpu_refs[vid]++;
    return 0;
}
static int core_del(struct dsa_switch *ds, int port, u16 vid) {
    int ret = an8855_tag_8021q_vlan_del(ds, port, vid);
    if (ret) return ret;
    if (!cpu_refs[vid]) return -ENOENT;
    if (!--cpu_refs[vid]) {
        ret = an8855_tag_8021q_vlan_del(ds, 5, vid);
        if (ret) { cpu_refs[vid] = 1; return ret; }
    }
    return 0;
}
/* dsa_tag_8021q_teardown(): every user port's standalone VID, whether it
 * is still there or not; dsa_port_tag_8021q_vlan_del() only logs a failure. */
static void dsa_tag_8021q_unregister(struct dsa_switch *ds) {
    for (int p = 0; p < 5; p++) {
        if (core_del(ds, p, 3072 + p) && added[p]) dev_errors++;
        added[p] = 0;
    }
    ds->tag_8021q_ctx = NULL;
}
static int dsa_tag_8021q_register(struct dsa_switch *ds, u16 proto) {
    (void)proto;
    ds->tag_8021q_ctx = ds;
    for (int p = 0; p < 5; p++) {
        int ret = core_add(ds, p, 3072 + p);
        if (ret) {
            /* Only the ports set up so far, newest first. */
            while (p-- > 0) {
                if (core_del(ds, p, 3072 + p)) dev_errors++;
                added[p] = 0;
            }
            ds->tag_8021q_ctx = NULL;
            return ret;
        }
        added[p] = 1;
    }
    return 0;
}
static int dsa_tag_8021q_bridge_join(struct dsa_switch *ds, int port,
    struct dsa_bridge bridge, bool *tx, struct netlink_ext_ack *extack) {
    (void)extack;
    int ret = core_add(ds, port, dsa_tag_8021q_bridge_vid(bridge.num));
    if (ret) return ret;
    /* As upstream: a failed delete of the standalone VID is only logged. */
    if (core_del(ds, port, 3072 + port)) dev_errors++;
    else added[port] = 0;
    *tx = true;
    return 0;
}
/* No retries: a failure is logged and left as it is. */
static void dsa_tag_8021q_bridge_leave(struct dsa_switch *ds, int port, struct dsa_bridge bridge) {
    if (core_add(ds, port, 3072 + port)) dev_errors++;
    else added[port] = 1;
    if (core_del(ds, port, dsa_tag_8021q_bridge_vid(bridge.num))) dev_errors++;
}
static struct regmap map, before;
static struct an8855_priv priv;
static struct dsa_switch ds;
static struct net_device users[5];
static struct net_device bridge_dev;
static void fixture(void) {
    memset(&map, 0, sizeof(map)); memset(&priv, 0, sizeof(priv));
    memset(&ds, 0, sizeof(ds)); memset(users, 0, sizeof(users));
    memset(added, 0, sizeof(added));
    memset(cpu_refs, 0, sizeof(cpu_refs));
    memset(&bridge_dev, 0, sizeof(bridge_dev));
    ds.priv = &priv; ds.users = 0x1f; ds.cpus = BIT(5);
    priv.ds = &ds; priv.regmap = &map;
    for (int p = 0; p < 6; p++) {
        ds.ports[p].index = p; ds.ports[p].ds = &ds;
        if (p < 5) {
            ds.ports[p].user = &users[p]; users[p].running = true;
            *reg(&map, AN8855_PMCR_P(p)) = AN8855_PMCR_TX_EN | AN8855_PMCR_RX_EN;
        }
        *reg(&map, AN8855_PCR_P(p)) = BIT(16) | (p == 5 ? AN8855_PORT_FALLBACK_MODE : 0);
        *reg(&map, AN8855_PVC_P(p)) = FIELD_PREP(AN8855_PVC_EG_TAG, AN8855_VLAN_EG_CONSISTENT);
        if (p == 5) *reg(&map, AN8855_PVC_P(p)) |= AN8855_PORT_SPEC_TAG | AN8855_PORT_SPEC_REPLACE_MODE;
        *reg(&map, AN8855_PPBV1_P(p)) = BIT(20);
        *reg(&map, AN8855_PVID_P(p)) = BIT(20);
        *reg(&map, AN8855_PORTMATRIX_P(p)) = p == 5 ? 0x1f : BIT(5);
    }
    for (int i = 0; i < 4; i++) *reg(&map, an8855_8021q_flood_regs[i]) = BIT(5) | BIT(20);
    assert(!an8855_trap_special_frames(&priv));
    before = map; map.operations = 0;
}
static void unchanged(void) {
    for (int i = 0; i < before.count; i++)
        assert(*reg(&map, before.regs[i].address) == before.regs[i].value);
    for (int v = 0; v < 4096; v++) assert(map.vlans[v] == before.vlans[v]);
    assert(!priv.reg_mutex.held);
}
static u16 pid(int p) { return FIELD_GET(AN8855_G0_PORT_VID, *reg(&map, AN8855_PVID_P(p))); }
static int toggle(int port, bool on) {
    struct net_device *br = ds.ports[port].bridge;
    bool old = br ? br->filtering : false;
    if (br) br->filtering = on;
    int ret = an8855_port_vlan_filtering(&ds, port, on, NULL);
    if (ret) { if (br) br->filtering = old; }
    else ds.ports[port].filtering = on;
    return ret;
}
static void membership(u16 vid, u32 ports) {
    u32 entry = map.vlans[vid];
    assert(FIELD_GET(AN8855_VA0_PORT, entry) == ports);
    if (ports) {
        assert(entry & AN8855_VA0_IVL_MAC); assert(entry & AN8855_VA0_VTAG_EN);
        assert(FIELD_GET(AN8855_VA0_FID, entry) == AN8855_FID_BRIDGED);
        assert(FIELD_GET(AN8855_VA0_ETAG_PORT_MASK(5), entry) == AN8855_VLAN_EGRESS_TAG);
    } else assert(entry == 0);
}
int main(void) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    fixture();
    assert(!an8855_change_tag_protocol(&ds, DSA_TAG_PROTO_AN8855_8021Q));
    int enable_operations = map.operations;
    for (int p = 0; p < 5; p++) {
        membership(3072 + p, BIT(p) | BIT(5)); assert(pid(p) == 3072 + p);
        assert(FIELD_GET(AN8855_PORT_VLAN, *reg(&map, AN8855_PCR_P(p))) == AN8855_PORT_SECURITY_MODE);
        assert(FIELD_GET(AN8855_PVC_EG_TAG, *reg(&map, AN8855_PVC_P(p))) == AN8855_VLAN_EG_DISABLED);
        assert(FIELD_GET(AN8855_VA0_ETAG_PORT_MASK(p), map.vlans[3072 + p]) == AN8855_VLAN_EGRESS_UNTAG);
    }
    assert(!(*reg(&map, AN8855_PVC_P(5)) & (AN8855_PORT_SPEC_TAG | AN8855_PORT_SPEC_REPLACE_MODE)));
    for (int i = 0; i < 4; i++) {
        assert((*reg(&map, an8855_8021q_flood_regs[i]) & 0x3f) == 0x3f);
        assert(!(*reg(&map, an8855_8021q_trap_regs[i].reg) & an8855_8021q_trap_regs[i].mask));
    }
    assert(!an8855_change_tag_protocol(&ds, DSA_TAG_PROTO_MTK)); unchanged();
    puts("PASS: CPU/user modes, VLAN egress, flood/trap masks, exact MTK restoration");
    for (int fail = 1; fail <= enable_operations; fail++) {
        fixture(); map.fail_at = fail;
        assert(an8855_change_tag_protocol(&ds, DSA_TAG_PROTO_AN8855_8021Q) == -EIO);
        assert(!priv.tag_8021q && !ds.tag_8021q_ctx);
        unchanged();
    }
    printf("PASS: mode-enable rollback at all %d I/O failure points\n", enable_operations);
    fixture(); priv.tag_8021q = true;
    assert(!an8855_tag_8021q_vlan_add(&ds, 5, 3088, 0));
    assert(!an8855_tag_8021q_vlan_add(&ds, 0, 3088, 6));
    assert(!an8855_tag_8021q_vlan_add(&ds, 1, 3088, 6));
    membership(3088, BIT(5) | BIT(0) | BIT(1));
    assert(!an8855_tag_8021q_vlan_add(&ds, 0, 3072, 6));
    assert(!an8855_tag_8021q_vlan_del(&ds, 0, 3088));
    assert(pid(0) == 3072); membership(3088, BIT(5) | BIT(1));
    assert(!an8855_tag_8021q_vlan_del(&ds, 1, 3088));
    assert(pid(1) == 0); membership(3088, BIT(5));
    assert(!an8855_tag_8021q_vlan_del(&ds, 5, 3088)); membership(3088, 0);
    assert(!an8855_tag_8021q_vlan_del(&ds, 5, 3088));
    puts("PASS: shared membership, FID preservation, PVID replacement, last-member deletion");
    struct dsa_bridge bridge = { .dev = &bridge_dev, .num = 1 };
    fixture(); assert(!an8855_change_tag_protocol(&ds, DSA_TAG_PROTO_AN8855_8021Q));
    for (int p = 0; p < 2; p++) {
        ds.ports[p].bridge = &bridge_dev; bool tx = false;
        assert(!an8855_port_bridge_join(&ds, p, bridge, &tx, NULL)); assert(tx);
        membership(3072 + p, BIT(p) | BIT(5)); assert(pid(p) == 3088);
        assert(cpu_refs[3072 + p] == 0);
        /* VLAN-unaware bridge: tagged frames are forwarded as they are. */
        assert(FIELD_GET(AN8855_VLAN_ATTR, *reg(&map, AN8855_PVC_P(p))) == AN8855_VLAN_TRANSPARENT);
        assert(FIELD_GET(AN8855_ACC_FRM, *reg(&map, AN8855_PVC_P(p))) == AN8855_VLAN_ACC_ALL);
    }
    membership(3088, BIT(0) | BIT(1) | BIT(5)); assert(cpu_refs[3088] == 2);
    for (int p = 0; p < 2; p++) {
        ds.ports[p].bridge = NULL; an8855_port_bridge_leave(&ds, p, bridge);
        membership(3072 + p, BIT(p) | BIT(5)); assert(pid(p) == 3072 + p);
        assert(FIELD_GET(AN8855_VLAN_ATTR, *reg(&map, AN8855_PVC_P(p))) == AN8855_VLAN_USER);
        assert(cpu_refs[3072 + p] == 1);
        assert(*reg(&map, AN8855_PORTMATRIX_P(p)) == BIT(5));
    }
    membership(3088, 0); assert(cpu_refs[3088] == 0);
    assert(!an8855_change_tag_protocol(&ds, DSA_TAG_PROTO_MTK)); unchanged();
    puts("PASS: bridge join/leave, dedicated per-port TX VLANs, CPU reference lifecycle");
    fixture(); assert(!an8855_change_tag_protocol(&ds, DSA_TAG_PROTO_AN8855_8021Q));
    ds.ports[0].bridge = &bridge_dev; bool tx = false; map.operations = 0;
    assert(!an8855_port_bridge_join(&ds, 0, bridge, &tx, NULL));
    int join_operations = map.operations;
    for (int fail = 1; fail <= join_operations; fail++) {
        fixture(); assert(!an8855_change_tag_protocol(&ds, DSA_TAG_PROTO_AN8855_8021Q));
        u32 old_pvc = *reg(&map, AN8855_PVC_P(0));
        ds.ports[0].bridge = &bridge_dev; map.operations = 0; map.fail_at = fail; tx = false;
        dev_errors = 0;
        int ret = an8855_port_bridge_join(&ds, 0, bridge, &tx, NULL);
        assert(ret == 0 || ret == -EIO);
        if (!ret && dev_errors) {
            /* The core logged a failed standalone delete and kept the port
             * joined; the switch still has the bridge VID. */
            membership(3088, BIT(0) | BIT(5)); membership(3072, BIT(0) | BIT(5));
            assert(cpu_refs[3088] == 1);
        } else if (ret) {
            membership(3088, 0); membership(3072, BIT(0) | BIT(5)); assert(pid(0) == 3072);
            assert(cpu_refs[3072] == 1 && cpu_refs[3088] == 0);
            assert(*reg(&map, AN8855_PORTMATRIX_P(0)) == BIT(5));
            /* The core still has dp->bridge set here: no bridged port mode. */
            assert(*reg(&map, AN8855_PVC_P(0)) == old_pvc);
        } else {
            membership(3072, BIT(0) | BIT(5)); assert(pid(0) == 3088);
        }
        assert(!priv.reg_mutex.held);
    }
    printf("PASS: bridge join recovery across %d I/O failure points\n", join_operations);
    /* The core unwinds a join that the driver accepted (switchdev offload
     * failed) with BRIDGE_LEAVE while dp->bridge is still set.
     */
    fixture(); assert(!an8855_change_tag_protocol(&ds, DSA_TAG_PROTO_AN8855_8021Q));
    u32 standalone_pvc = *reg(&map, AN8855_PVC_P(0));
    ds.ports[0].bridge = &bridge_dev; tx = false;
    assert(!an8855_port_bridge_join(&ds, 0, bridge, &tx, NULL));
    an8855_port_bridge_leave(&ds, 0, bridge); ds.ports[0].bridge = NULL;
    membership(3088, 0); membership(3072, BIT(0) | BIT(5)); assert(pid(0) == 3072);
    assert(*reg(&map, AN8855_PVC_P(0)) == standalone_pvc);
    assert(cpu_refs[3072] == 1 && cpu_refs[3088] == 0);
    puts("PASS: a join the core unwinds with dp->bridge still set");
    for (int deleting = 0; deleting < 2; deleting++) {
        fixture(); priv.tag_8021q = true;
        if (deleting) assert(!an8855_tag_8021q_vlan_add(&ds, 0, 3072, 6));
        map.operations = 0;
        int ret = deleting ? an8855_tag_8021q_vlan_del(&ds, 0, 3072) : an8855_tag_8021q_vlan_add(&ds, 0, 3072, 6);
        assert(!ret); int operations = map.operations;
        for (int fail = 1; fail <= operations; fail++) {
            fixture(); priv.tag_8021q = true;
            if (deleting) assert(!an8855_tag_8021q_vlan_add(&ds, 0, 3072, 6));
            u32 old_vlan = map.vlans[3072]; u16 old_pid = pid(0);
            map.operations = 0; map.fail_at = fail;
            ret = deleting ? an8855_tag_8021q_vlan_del(&ds, 0, 3072) : an8855_tag_8021q_vlan_add(&ds, 0, 3072, 6);
            assert(ret == -EIO); assert(map.vlans[3072] == old_vlan); assert(pid(0) == old_pid);
            assert(FIELD_GET(AN8855_PPBV_G0_PORT_VID, *reg(&map, AN8855_PPBV1_P(0))) == old_pid);
            assert(priv.tag_8021q_pvid[0] == old_pid); assert(!priv.reg_mutex.held);
        }
        printf("PASS: VLAN %s rollback at all %d I/O failure points\n", deleting ? "delete" : "add", operations);
    }
    fixture(); priv.tag_8021q = true;
    assert(!toggle(0, false));
    struct switchdev_obj_port_vlan vlan = { .vid = 3072 };
    assert(an8855_port_vlan_add(&ds, 0, &vlan, NULL) == -EBUSY);
    assert(an8855_port_vlan_del(&ds, 0, &vlan) == -EBUSY);
    /* VID 0 from the 8021q layer is accepted without touching the switch */
    before = map; map.operations = 0; vlan.vid = 0;
    assert(!an8855_port_vlan_add(&ds, 0, &vlan, NULL));
    assert(!an8855_port_vlan_del(&ds, 0, &vlan));
    assert(!map.operations); unchanged();
    struct net_device upper = { .vlan = true, .proto = ETH_P_8021Q, .vid = 35 };
    struct netdev_notifier_changeupper_info info = { .linking = true, .upper_dev = &upper };
    ds.ports[0].bridge = &bridge_dev;
    assert(!an8855_port_prechangeupper(&ds, 0, &info));
    upper.proto = 0x88a8;
    assert(an8855_port_prechangeupper(&ds, 0, &info) == -EOPNOTSUPP);
    unchanged();
    puts("PASS: upper protocol and reserved-VID guards without hardware changes");
    fixture(); users[0].upper = true;
    assert(an8855_change_tag_protocol(&ds, DSA_TAG_PROTO_AN8855_8021Q) == -EBUSY); unchanged();
    puts("PASS: existing uppers reject mode changes before register writes");

    fixture(); assert(!an8855_change_tag_protocol(&ds, DSA_TAG_PROTO_AN8855_8021Q));
    for (int p = 0; p < 2; p++) {
        ds.ports[p].bridge = &bridge_dev; tx = false;
        assert(!an8855_port_bridge_join(&ds, p, bridge, &tx, NULL));
    }
    struct switchdev_obj_port_vlan access = { .obj.orig_dev = &bridge_dev, .vid = 10, .flags = 6 };
    struct switchdev_obj_port_vlan trunk = { .obj.orig_dev = &bridge_dev, .vid = 10, .flags = 0 };
    assert(!an8855_port_vlan_add(&ds, 0, &access, NULL));
    assert(!an8855_port_vlan_add(&ds, 1, &trunk, NULL));
    membership(10, 0); assert(pid(0) == 3088 && priv.bridge_pvid[0] == 10);
    assert(!toggle(0, true)); ds.ports[0].filtering = true;
    assert(!toggle(1, true)); ds.ports[1].filtering = true;
    membership(10, BIT(0) | BIT(1) | BIT(5)); assert(pid(0) == 10 && pid(1) == 0);
    assert(FIELD_GET(AN8855_VA0_ETAG_PORT_MASK(0), map.vlans[10]) == AN8855_VLAN_EGRESS_UNTAG);
    assert(FIELD_GET(AN8855_VA0_ETAG_PORT_MASK(1), map.vlans[10]) == AN8855_VLAN_EGRESS_TAG);
    assert(FIELD_GET(AN8855_ACC_FRM, *reg(&map, AN8855_PVC_P(1))) == AN8855_VLAN_ACC_TAGGED);
    membership(3088, BIT(5)); membership(3072, BIT(0) | BIT(5));
    struct switchdev_obj_port_vlan customer = { .vid = 35 };
    assert(!an8855_port_vlan_add(&ds, 0, &customer, NULL)); membership(35, BIT(0) | BIT(5));
    assert(pid(0) == 10 && FIELD_GET(AN8855_VA0_ETAG_PORT_MASK(0), map.vlans[35]) == AN8855_VLAN_EGRESS_TAG);
    assert(an8855_port_vlan_add(&ds, 1, &customer, NULL) == -EBUSY);
    struct switchdev_obj_port_vlan collision = { .obj.orig_dev = &bridge_dev, .vid = 35 };
    assert(an8855_port_vlan_add(&ds, 1, &collision, NULL) == -EBUSY);
    customer.vid = 10; assert(an8855_port_vlan_add(&ds, 0, &customer, NULL) == -EBUSY); customer.vid = 35;
    for (int repeat = 0; repeat < 2; repeat++) {
        assert(!toggle(0, false)); ds.ports[0].filtering = false;
        assert(!toggle(1, false)); ds.ports[1].filtering = false;
        membership(10, 0); membership(3088, BIT(0) | BIT(1) | BIT(5));
        assert(pid(0) == 3088 && pid(1) == 3088); membership(35, BIT(0) | BIT(5));
        assert(!toggle(0, true)); ds.ports[0].filtering = true;
        assert(!toggle(1, true)); ds.ports[1].filtering = true;
        membership(10, BIT(0) | BIT(1) | BIT(5)); membership(3088, BIT(5));
        assert(pid(0) == 10 && pid(1) == 0);
    }
    access.flags = 0; assert(!an8855_port_vlan_del(&ds, 0, &access));
    assert(pid(0) == 0 && priv.bridge_pvid[0] == 0); membership(10, BIT(1) | BIT(5));
    access.vid = 20; access.flags = 6; assert(!an8855_port_vlan_add(&ds, 0, &access, NULL)); assert(pid(0) == 20);
    access.vid = 21; assert(!an8855_port_vlan_add(&ds, 0, &access, NULL)); assert(pid(0) == 21);
    ds.ports[0].bridge = NULL; an8855_port_bridge_leave(&ds, 0, bridge);
    assert(pid(0) == 3072 && priv.bridge_pvid[0] == 0); membership(20, 0); membership(21, 0);
    membership(35, BIT(0) | BIT(5)); assert(ds.vlan_upper_filtering_always);
    assert(!an8855_port_vlan_del(&ds, 0, &customer)); membership(35, 0);
    puts("PASS: aware access/trunk VLANs, shadow replay, upper coexistence, PVID changes and bridge leave");

    /* Every failed multi-row filtering transition must restore state exactly. */
    fixture(); assert(!an8855_change_tag_protocol(&ds, DSA_TAG_PROTO_AN8855_8021Q));
    ds.ports[0].bridge = &bridge_dev; tx = false;
    assert(!an8855_port_bridge_join(&ds, 0, bridge, &tx, NULL));
    access.vid = 10; access.flags = 6; assert(!an8855_port_vlan_add(&ds, 0, &access, NULL));
    access.vid = 11; access.flags = 0; assert(!an8855_port_vlan_add(&ds, 0, &access, NULL));
    map.operations = 0; assert(!toggle(0, true));
    int filtering_operations = map.operations;
    for (int fail = 1; fail <= filtering_operations; fail++) {
        fixture(); assert(!an8855_change_tag_protocol(&ds, DSA_TAG_PROTO_AN8855_8021Q));
        ds.ports[0].bridge = &bridge_dev; tx = false;
        assert(!an8855_port_bridge_join(&ds, 0, bridge, &tx, NULL));
        access.vid = 10; access.flags = 6; assert(!an8855_port_vlan_add(&ds, 0, &access, NULL));
        access.vid = 11; access.flags = 0; assert(!an8855_port_vlan_add(&ds, 0, &access, NULL));
        before = map; struct an8855_priv old_state = priv;
        map.operations = 0; map.fail_at = fail;
        assert(toggle(0, true) == -EIO);
        for (int v = 0; v < VLAN_N_VID; v++) assert(map.vlans[v] == before.vlans[v]);
        for (int i = 0; i < before.count; i++) {
            u32 address = before.regs[i].address;
            if (address != AN8855_VARD0 && address != AN8855_VAWD0 && address != AN8855_VAWD1 && address != AN8855_VTCR)
                assert(*reg(&map, address) == before.regs[i].value);
        }
        assert(!memcmp(&priv, &old_state, sizeof(priv)));
    }
    printf("PASS: multi-row filtering rollback at all %d I/O failure points\n", filtering_operations);

    fixture(); assert(!an8855_change_tag_protocol(&ds, DSA_TAG_PROTO_AN8855_8021Q));
    for (int p = 0; p < 2; p++) {
        ds.ports[p].bridge = &bridge_dev; tx = false;
        assert(!an8855_port_bridge_join(&ds, p, bridge, &tx, NULL));
        access.vid = p ? 20 : 10; access.flags = 6;
        assert(!an8855_port_vlan_add(&ds, p, &access, NULL));
    }
    customer.vid = 20; assert(!an8855_port_vlan_add(&ds, 2, &customer, NULL));
    before = map; struct an8855_priv old_state = priv;
    assert(toggle(0, true) == -EBUSY); unchanged();
    assert(!memcmp(&priv, &old_state, sizeof(priv)));
    assert(!an8855_port_vlan_del(&ds, 2, &customer));
    map.operations = 0; assert(!toggle(0, true));
    assert(test_bit(0, &priv.vlan_filtering) && test_bit(1, &priv.vlan_filtering));
    assert(pid(0) == 10 && pid(1) == 20);
    int group_operations = map.operations;
    for (int fail = 1; fail <= group_operations; fail++) {
        fixture(); assert(!an8855_change_tag_protocol(&ds, DSA_TAG_PROTO_AN8855_8021Q));
        for (int p = 0; p < 2; p++) {
            ds.ports[p].bridge = &bridge_dev; tx = false;
            assert(!an8855_port_bridge_join(&ds, p, bridge, &tx, NULL));
            access.vid = p ? 20 : 10; access.flags = 6;
            assert(!an8855_port_vlan_add(&ds, p, &access, NULL));
        }
        before = map; old_state = priv; map.operations = 0; map.fail_at = fail;
        assert(toggle(0, true) == -EIO);
        for (int v = 0; v < VLAN_N_VID; v++) assert(map.vlans[v] == before.vlans[v]);
        assert(!memcmp(&priv, &old_state, sizeof(priv))); assert(pid(0) == 3088 && pid(1) == 3088);
    }
    printf("PASS: bridge-wide collision preflight and atomic rollback at %d I/O points\n", group_operations);

    fixture(); assert(!an8855_change_tag_protocol(&ds, DSA_TAG_PROTO_AN8855_8021Q));
    for (int p = 0; p < 2; p++) {
        ds.ports[p].bridge = &bridge_dev; tx = false;
        assert(!an8855_port_bridge_join(&ds, p, bridge, &tx, NULL));
        access.vid = 10; access.flags = 6; assert(!an8855_port_vlan_add(&ds, p, &access, NULL));
    }
    assert(!toggle(0, true)); assert(!toggle(1, true));
    ds.ports[0].bridge = NULL; map.operations = 0; an8855_port_bridge_leave(&ds, 0, bridge);
    int leave_operations = map.operations;
    for (int fail = 1; fail <= leave_operations; fail++) {
        fixture(); assert(!an8855_change_tag_protocol(&ds, DSA_TAG_PROTO_AN8855_8021Q));
        for (int p = 0; p < 2; p++) {
            ds.ports[p].bridge = &bridge_dev; tx = false;
            assert(!an8855_port_bridge_join(&ds, p, bridge, &tx, NULL));
            access.vid = 10; access.flags = 6; assert(!an8855_port_vlan_add(&ds, p, &access, NULL));
        }
        assert(!toggle(0, true)); assert(!toggle(1, true));
        ds.ports[0].bridge = NULL; map.operations = 0; map.fail_at = fail; dev_errors = 0;
        an8855_port_bridge_leave(&ds, 0, bridge);
        /* Bridge leave cannot fail, and nothing retries. Either every
         * step went through and the port is standalone, or a failure was
         * logged and port 0 is cut off from port 1: shut, or sharing no
         * VLAN row with it. */
        bool shut = !(*reg(&map, AN8855_PMCR_P(0)) & (AN8855_PMCR_TX_EN | AN8855_PMCR_RX_EN));
        bool shared = false;
        for (int v = 1; v < VLAN_N_VID; v++) {
            u32 members = FIELD_GET(AN8855_VA0_PORT, map.vlans[v]);
            if ((map.vlans[v] & AN8855_VA0_VLAN_VALID) && (members & BIT(0)) && (members & BIT(1)))
                shared = true;
        }
        assert(test_bit(1, &priv.vlan_filtering));
        if (!dev_errors) {
            membership(3072, BIT(0) | BIT(5)); membership(10, BIT(1) | BIT(5));
            assert(pid(0) == 3072 && !test_bit(0, &priv.vlan_filtering));
            assert(!bitmap_weight(priv.bridge_vlans[0], VLAN_N_VID));
            assert(cpu_refs[3072] == 1 && cpu_refs[3088] == 1);
            assert(*reg(&map, AN8855_PORTMATRIX_P(0)) == BIT(5));
            assert(!shut);
        } else {
            assert(shut || !shared);
            if (test_bit(0, &priv.vlan_filtering))
                assert(shut && bitmap_weight(priv.bridge_vlans[0], VLAN_N_VID));
        }
        /* The core then resets filtering on the port, which was filtering.
         * That finishes a leave that failed in the driver. */
        map.fail_at = 0; assert(!toggle(0, false));
        assert(!test_bit(0, &priv.vlan_filtering) && !test_bit(0, &priv.leave_pending));
        assert(!bitmap_weight(priv.bridge_vlans[0], VLAN_N_VID) && !priv.bridge_pvid[0]);
        assert(*reg(&map, AN8855_PMCR_P(0)) & (AN8855_PMCR_TX_EN | AN8855_PMCR_RX_EN));
        assert(test_bit(1, &priv.vlan_filtering) && !priv.reg_mutex.held);
    }
    printf("PASS: aware bridge leave completes, or logs, cuts the port off and recovers on the filtering reset, at all %d I/O failure points\n", leave_operations);
    return 0;
}
'''

with tempfile.TemporaryDirectory(prefix="an8855-8021q-") as directory:
    unit = Path(directory) / "test.c"
    binary = Path(directory) / "test"
    transaction = source[source.index("struct an8855_vlan_transaction {"):
                         source.index("static int an8855_vlan_transaction_capture_port")]
    unit.write_text(stubs + header + model + transaction + arrays + "\n".join(map(function, names)) + checks)
    subprocess.run(shlex.split(os.environ.get("CC", "cc")) +
                   ["-std=gnu11", "-Wall", "-Wextra", "-Werror",
                    "-Wno-unused-parameter", "-Wno-sign-compare", "-O1", "-g",
                    str(unit), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
