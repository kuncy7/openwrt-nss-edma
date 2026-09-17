// SPDX-License-Identifier: GPL-2.0-only
/*
 * rtl8367s_nss.c - re-arm an RTL8367S-VB (family D) after rtl8365mb teardown.
 *
 * The Qualcomm NSS firmware cannot parse the Realtek CPU tag, so a board
 * that wants NSS offload has to give up DSA and drive the switch as a plain
 * 802.1q trunk. Unbinding rtl8365mb-mdio is the easy half; this module is
 * the other half, putting back everything the driver takes with it. Written
 * against a TP-Link Archer AX55 v1 (IPQ5018 + RTL8367S-VB, WAN on switch
 * port 0, LAN1-4 on ports 1-4, trunk on port 6 at 2500base-x).
 *
 * What teardown leaves alone, measured: the SerDes stays fully configured
 * (SDS_MISC 0x1D11 keeps 0x0E12, the SDS13 status window keeps reporting
 * link, FORCE_EN stays 0xFFFF), so the family D re-latch dance does not have
 * to be repeated.
 *
 * What it breaks, and what this restores:
 *
 *   force:     0x12C0+port. Family D encodes speed in three bits - bits[1:0]
 *              in FORCE_SPEED and bit[2] in FORCE_SPEED2 (bit 12) - so 2.5G
 *              (speed 5), duplex and forced link read back as 0x1015. The
 *              default here adds the pause bits: without flow control the
 *              switch drops frames on the 2.5G to 1G step and TCP sees
 *              thousands of retransmits.
 *   cpu tag:   tag insertion on the trunk has to go, same reason
 *              qca8337_nss turns the Atheros header off.
 *   stp/isol:  forwarding state and the port isolation masks.
 *   vlan:      the CVLAN table and the per-port PVIDs, both wiped on the way
 *              out. Without them the trunk carries no tags at all.
 *   egress:    per-port VLAN egress mode. DSA leaves the ports in REAL_KEEP,
 *              where a frame egresses in whatever tag format it ingressed
 *              with, and the VLAN table is then ignored - the single least
 *              obvious thing on this list.
 *   learning:  the per-port learning limit, which DSA zeroes on setup
 *              because under DSA the bridge does the switching. Left at zero
 *              the L2 database stays empty and every frame is flooded to all
 *              members of its VLAN, so the WAN jack transmits each frame
 *              twice and saturates at half of line rate.
 *   phys:      the front PHYs sit behind the indirect OCP window and
 *              phy_detach leaves them in BMCR power-down.
 *
 * It does all of that in one pass and then refuses to load, so a rerun needs
 * no rmmod first and nothing is left holding the bus. What the switch keeps
 * is what was written to it.
 *
 * Register reads on this chip only return the addressed register's contents
 * shortly after a write; otherwise the data register keeps its previous
 * value. Every read here is therefore preceded by a harmless write.
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/delay.h>
#include <linux/phy.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/workqueue.h>
#include <linux/fs.h>
#include <linux/array_size.h>

#define REALTEK_MDIO_CTRL0_REG		31
#define REALTEK_MDIO_CTRL1_REG		21
#define REALTEK_MDIO_ADDRESS_REG	23
#define REALTEK_MDIO_DATA_WRITE_REG	24
#define REALTEK_MDIO_DATA_READ_REG	25

#define REALTEK_MDIO_ADDR_OP		0x000E
#define REALTEK_MDIO_READ_OP		0x0001
#define REALTEK_MDIO_WRITE_OP		0x0003

#define RTL_MAGIC_REG			0x13C2	/* 0 is its idle value; see rtl_chip_id() */
#define RTL_MAGIC_VALUE			0x0249
#define RTL_CHIP_ID_REG			0x1300
#define RTL_CHIP_VER_REG		0x1301
#define RTL_CHIP_ID_RTL8367S_VB		0x6642

#define RTL_D_FORCE_BASE		0x12C0
#define RTL_D_FORCE_EN_BASE		0x12C8
#define RTL_D_FORCE_EN_ALL		0xFFFF

/* DSA has no port_enable/port_disable op here, so teardown does not turn
 * ports off in hardware. What it does do is drop every port to STP
 * "disabled" and strip the isolation masks, which is enough to keep the
 * link down and stop all forwarding.
 */
#define RTL_MSTI_CTRL_REG(_p)		(0x0A00 + ((_p) >> 3))
#define   RTL_MSTI_STATE_MASK(_p)	(0x3 << (((_p) & 7) << 1))
#define   RTL_MSTI_STATE_FORWARDING	3

#define RTL_PORT_ISOLATION_REG(_p)	(0x08A2 + (_p))
/* Writing 0x07FF reads back as 0x00FF: the field is eight ports wide on
 * this chip, not eleven.
 */
#define   RTL_PORT_ISOLATION_MASK	0x00FF

/* VLAN. DSA programs the CVLAN table itself, but tears it down again on
 * unbind (measured: eth0 keeps receiving, eth0.<vid> see nothing), so the
 * entries have to be rewritten here.
 *
 * The 4k table is reached through the look-up engine at 0x0500: entry data
 * goes to 0x0510.., the VID is the table address, and the command selects
 * table 3 (CVLAN) with the write op. A CVLAN entry is three 16-bit words.
 */
#define RTL_TABLE_CTRL_REG		0x0500
#define   RTL_TABLE_CTRL_TABLE_CVLAN	0x0003	/* bits 2:0 */
#define   RTL_TABLE_CTRL_OP_WRITE	0x0008	/* bit 3 */
#define   RTL_TABLE_CTRL_OP_READ	0x0000
#define RTL_TABLE_ADDR_REG		0x0501
#define RTL_TABLE_STATUS_REG		0x0502
#define   RTL_TABLE_STATUS_BUSY		0x2000	/* bit 13 */
#define RTL_TABLE_WRITE_BASE		0x0510
#define RTL_TABLE_READ_BASE		0x0520

#define RTL_CVLAN_ENTRY_SIZE		3

/* L2 forwarding database. Read with ADDR_NEXT_UC: the engine takes an
 * index, returns the next occupied unicast entry at or after it, and
 * reports where it found it in the status register.
 */
#define   RTL_TABLE_CTRL_TABLE_L2	0x0004	/* bits 2:0 */
#define   RTL_TABLE_CTRL_METHOD_NEXT_UC	0x0030	/* method 3 in bits 6:4 */
#define   RTL_TABLE_STATUS_ADDRESS_MASK	0x07FF
#define   RTL_TABLE_STATUS_HIT		0x1000

#define RTL_L2_ENTRY_SIZE		6

/* Per-port learning limit. DSA sets this to zero on setup - under DSA the
 * switch is not supposed to switch, the bridge does - and nothing puts it
 * back once the driver is unbound. With learning off the L2 database stays
 * empty and every frame is flooded to all members of its VLAN, so the WAN
 * jack ends up transmitting each frame twice and saturates at ~460 Mbps.
 */
#define RTL_LEARN_LIMIT_REG(_p)		(0x0A20 + (_p))
#define   RTL_LEARN_LIMIT_MAX		2112

/* Family D packs ivl_svl and svlan_check_ivl_svl into the FID nibble; the
 * FID itself is two bits wide. See patch 930-5.
 */
#define RTL_D_FID_BITS(_fid)		(((_fid) & 0x3) | BIT(3) | BIT(2))

#define RTL_VLAN_CTRL_REG		0x07A8
#define   RTL_VLAN_CTRL_EN		0x0001

/* Per-port egress mode. The VLAN4k table only decides tagging when the
 * port is in ORIGINAL mode: teardown leaves ports in REAL_KEEP, where a
 * frame egresses in whatever tag format it ingressed with, so the trunk
 * never adds a tag no matter what the table says.
 */
/* MIB counters live in an SRAM window: write the address, poll the
 * control register, then read the value out of four 16-bit registers.
 * Per-port blocks are RTL_MIB_PORT_OFFSET apart.
 */
#define RTL_MIB_COUNTER_REG(_x)		(0x1000 + (_x))
#define RTL_MIB_ADDRESS_REG		0x1004
#define   RTL_MIB_PORT_OFFSET		0x007C
#define   RTL_MIB_ADDRESS(_p, _x)	(((_p) * RTL_MIB_PORT_OFFSET + (_x)) >> 2)
#define RTL_MIB_CTRL0_REG		0x1005
#define   RTL_MIB_CTRL0_RESET_MASK	0x0002
#define   RTL_MIB_CTRL0_BUSY_MASK	0x0001

#define RTL_MIB_IF_IN_OCTETS		0
#define RTL_MIB_IF_OUT_OCTETS		60
#define RTL_MIB_IF_IN_UCAST		16
#define RTL_MIB_IF_OUT_UCAST		82
#define RTL_MIB_IF_OUT_MCAST		84
#define RTL_MIB_IF_OUT_BCAST		86
#define RTL_MIB_IF_IN_MCAST		20
#define RTL_MIB_IF_IN_BCAST		22
#define RTL_MIB_IN_PAUSE		8
#define RTL_MIB_OUT_PAUSE		76

#define RTL_PORT_MISC_CFG_REG(_p)	(0x000E + ((_p) << 5))
#define   RTL_VLAN_EGRESS_MODE_MASK	0x0030
#define   RTL_VLAN_EGRESS_MODE_ORIGINAL	0x0000

#define RTL_D_PVID_REG(_p)		(0x0700 + (_p))
#define   RTL_D_PVID_MASK		0x0FFF

#define RTL_ACCEPT_FRAME_REG(_p)	(0x07AA + ((_p) >> 3))
#define   RTL_ACCEPT_FRAME_MASK(_p)	(0x3 << (((_p) & 0x7) << 1))

#define RTL_CPU_CTRL_REG		0x121A
#define   RTL_CPU_CTRL_EN_MASK		0x0001
#define   RTL_CPU_CTRL_INSERTMODE_MASK	0x0006

#define RTL_IA_CTRL_REG			0x1F00
#define   RTL_IA_CTRL_RW_WRITE		0x0002
#define   RTL_IA_CTRL_CMD		0x0001
#define RTL_IA_STATUS_REG		0x1F01
#define RTL_IA_ADDRESS_REG		0x1F02
#define RTL_IA_WRITE_DATA_REG		0x1F03
#define RTL_IA_READ_DATA_REG		0x1F04

#define RTL_PHY_BASE			0x2000
#define RTL_GPHY_OCP_MSB_0_REG		0x1D15
#define   RTL_GPHY_OCP_MSB_0_MASK	0x0FC0
#define RTL_PHY_OCP_ADDR_PHYREG_BASE	0xA400

static char *bus_id = "90000.mdio-1";
module_param(bus_id, charp, 0444);
MODULE_PARM_DESC(bus_id, "MDIO bus holding the switch");

static int sw_addr = 0x1d;
module_param(sw_addr, int, 0444);
MODULE_PARM_DESC(sw_addr, "switch MDIO address");

static int trunk_port = 6;
module_param(trunk_port, int, 0444);
MODULE_PARM_DESC(trunk_port, "switch port carrying the trunk (default 6)");

static int force_val = 0x1075;
module_param(force_val, int, 0444);
MODULE_PARM_DESC(force_val,
		 "force word: 0x1075 is 2500/full/link plus rx+tx pause; without\n"
		 "the pause bits the switch drops frames on the 2.5G to 1G step");

static char *ports = "0,1,2,3,4,6";
module_param(ports, charp, 0444);
MODULE_PARM_DESC(ports,
		 "switch ports to put back into forwarding, trunk included");

static char *phys = "0,1,2,3,4";
module_param(phys, charp, 0444);
MODULE_PARM_DESC(phys, "front PHY numbers to wake, comma separated");

static bool cpu_tag_off = true;
module_param(cpu_tag_off, bool, 0444);
MODULE_PARM_DESC(cpu_tag_off, "disable CPU tag insertion on the trunk");

static bool mib_dump;
module_param(mib_dump, bool, 0444);
MODULE_PARM_DESC(mib_dump,
		 "print the per-port octet counters and change nothing");

static bool l2_dump;
module_param(l2_dump, bool, 0444);
MODULE_PARM_DESC(l2_dump,
		 "walk the L2 forwarding database and change nothing");

/* "vid:portspec,...;vid:..." with u = untagged member, t = tagged member.
 * The AX55 wiring, read back from what DSA had programmed: the WAN jack
 * is switch port 0 and LAN1-4 are ports 1-4, so VLAN 1 holds the four LAN
 * ports untagged and VLAN 2 the WAN port, with the trunk tagged in both.
 */
static char *vlans = "1:1u,2u,3u,4u,6t;2:0u,6t";
module_param(vlans, charp, 0444);
MODULE_PARM_DESC(vlans, "VLAN program, e.g. 1:0u,1u,2u,3u,6t;2:4u,6t");

/* PVID per front port, "port:vid,..." - what an untagged ingress frame
 * gets tagged with.
 */
static char *pvids = "1:1,2:1,3:1,4:1,0:2";
module_param(pvids, charp, 0444);
MODULE_PARM_DESC(pvids, "per-port PVID, e.g. 0:1,1:1,2:1,3:1,4:2");

static bool dry_run;
module_param(dry_run, bool, 0444);
MODULE_PARM_DESC(dry_run, "read and report, change nothing");

/* Every register touch used to be logged, which is how the switch was
 * reverse engineered but is far too loud for normal boots.
 */
static bool verbose;
module_param(verbose, bool, 0644);
MODULE_PARM_DESC(verbose, "log every register access, not just the changes");

static struct mii_bus *rbus;

static void rtl_write(u16 reg, u16 val)
{
	mutex_lock(&rbus->mdio_lock);
	rbus->write(rbus, sw_addr, REALTEK_MDIO_CTRL0_REG, REALTEK_MDIO_ADDR_OP);
	rbus->write(rbus, sw_addr, REALTEK_MDIO_ADDRESS_REG, reg);
	rbus->write(rbus, sw_addr, REALTEK_MDIO_DATA_WRITE_REG, val);
	rbus->write(rbus, sw_addr, REALTEK_MDIO_CTRL1_REG, REALTEK_MDIO_WRITE_OP);
	mutex_unlock(&rbus->mdio_lock);
}

static int rtl_read(u16 reg, u16 *val)
{
	int ret;

	/* See the file header: a read only returns the addressed register
	 * shortly after a write, so prime the interface with one.
	 */
	rtl_write(RTL_MAGIC_REG, 0);

	mutex_lock(&rbus->mdio_lock);
	rbus->write(rbus, sw_addr, REALTEK_MDIO_CTRL0_REG, REALTEK_MDIO_ADDR_OP);
	rbus->write(rbus, sw_addr, REALTEK_MDIO_ADDRESS_REG, reg);
	rbus->write(rbus, sw_addr, REALTEK_MDIO_CTRL1_REG, REALTEK_MDIO_READ_OP);
	ret = rbus->read(rbus, sw_addr, REALTEK_MDIO_DATA_READ_REG);
	mutex_unlock(&rbus->mdio_lock);

	if (ret < 0)
		return ret;

	*val = ret;
	return 0;
}

/* The chip ID and version registers only answer while the magic register
 * holds 0x0249, which is also the register rtl_read() primes with - so the
 * ordinary read path cannot see them, and this has to drive the bus itself.
 * Same sequence as rtl8365mb_read_chip_id_and_ver().
 */
static int rtl_chip_id(u16 *id, u16 *ver)
{
	int ret;

	*id = 0;
	*ver = 0;

	rtl_write(RTL_MAGIC_REG, RTL_MAGIC_VALUE);

	mutex_lock(&rbus->mdio_lock);
	rbus->write(rbus, sw_addr, REALTEK_MDIO_CTRL0_REG, REALTEK_MDIO_ADDR_OP);
	rbus->write(rbus, sw_addr, REALTEK_MDIO_ADDRESS_REG, RTL_CHIP_ID_REG);
	rbus->write(rbus, sw_addr, REALTEK_MDIO_CTRL1_REG, REALTEK_MDIO_READ_OP);
	ret = rbus->read(rbus, sw_addr, REALTEK_MDIO_DATA_READ_REG);
	if (ret >= 0) {
		*id = ret;
		rbus->write(rbus, sw_addr, REALTEK_MDIO_CTRL0_REG, REALTEK_MDIO_ADDR_OP);
		rbus->write(rbus, sw_addr, REALTEK_MDIO_ADDRESS_REG, RTL_CHIP_VER_REG);
		rbus->write(rbus, sw_addr, REALTEK_MDIO_CTRL1_REG, REALTEK_MDIO_READ_OP);
		ret = rbus->read(rbus, sw_addr, REALTEK_MDIO_DATA_READ_REG);
		if (ret >= 0)
			*ver = ret;
	}
	mutex_unlock(&rbus->mdio_lock);

	rtl_write(RTL_MAGIC_REG, 0);

	return ret < 0 ? ret : 0;
}

/* Read with no priming write in front. Only valid immediately after a
 * write - which is exactly the case for the indirect window, where the
 * command register write is itself the priming write, and where anything
 * inserted between the command and 0x1F04 destroys the result.
 */
static int rtl_read_raw(u16 reg, u16 *val)
{
	int ret;

	mutex_lock(&rbus->mdio_lock);
	rbus->write(rbus, sw_addr, REALTEK_MDIO_CTRL0_REG, REALTEK_MDIO_ADDR_OP);
	rbus->write(rbus, sw_addr, REALTEK_MDIO_ADDRESS_REG, reg);
	rbus->write(rbus, sw_addr, REALTEK_MDIO_CTRL1_REG, REALTEK_MDIO_READ_OP);
	ret = rbus->read(rbus, sw_addr, REALTEK_MDIO_DATA_READ_REG);
	mutex_unlock(&rbus->mdio_lock);

	if (ret < 0)
		return ret;

	*val = ret;
	return 0;
}

/* Set while the LED poll runs: it touches registers four times a second
 * and must not narrate.
 */
static bool rtl_quiet;

static int rtl_update(u16 reg, u16 mask, u16 val)
{
	u16 old, new;
	int ret;

	ret = rtl_read(reg, &old);
	if (ret)
		return ret;

	new = (old & ~mask) | (val & mask);
	if (new == old) {
		if (verbose && !rtl_quiet)
			pr_info("rtl8367s-nss: 0x%04X already 0x%04X\n",
				reg, old);
		return 0;
	}

	if (dry_run) {
		pr_info("rtl8367s-nss: 0x%04X would go 0x%04X -> 0x%04X\n",
			reg, old, new);
		return 0;
	}

	rtl_write(reg, new);
	ret = rtl_read(reg, &old);
	if (!rtl_quiet && (verbose || old != new))
		pr_info("rtl8367s-nss: 0x%04X set to 0x%04X, reads back 0x%04X\n",
			reg, new, old);

	return ret;
}

/* ===== indirect access to the front PHYs ===== */

static int rtl_ia_wait(void)
{
	u16 status;
	int i;

	for (i = 0; i < 20; i++) {
		if (rtl_read(RTL_IA_STATUS_REG, &status))
			return -EIO;
		if (!status)
			return 0;
		usleep_range(10, 20);
	}

	return -ETIMEDOUT;
}

static int rtl_ocp_prepare(int phy, u32 ocp_addr)
{
	u16 val;

	if (rtl_update(RTL_GPHY_OCP_MSB_0_REG, RTL_GPHY_OCP_MSB_0_MASK,
		       (ocp_addr >> 4) & RTL_GPHY_OCP_MSB_0_MASK))
		return -EIO;

	val = RTL_PHY_BASE;
	val |= (phy << 5) & 0x00E0;		/* PHYNUM, bits 7:5 */
	val |= (ocp_addr >> 1) & 0x001F;	/* OCPADR 5:1 */
	val |= ((ocp_addr >> 6) << 8) & 0x0F00;	/* OCPADR 9:6 */

	rtl_write(RTL_IA_ADDRESS_REG, val);
	return 0;
}

static int rtl_phy_read(int phy, int regnum, u16 *out)
{
	u32 ocp_addr = RTL_PHY_OCP_ADDR_PHYREG_BASE + regnum * 2;
	int ret;

	ret = rtl_ia_wait();
	if (ret)
		return ret;

	ret = rtl_ocp_prepare(phy, ocp_addr);
	if (ret)
		return ret;

	/* Command, then straight to the data register. No poll, no priming
	 * write: either would clobber the result.
	 */
	rtl_write(RTL_IA_CTRL_REG, RTL_IA_CTRL_CMD);

	return rtl_read_raw(RTL_IA_READ_DATA_REG, out);
}

static int rtl_phy_write(int phy, int regnum, u16 val)
{
	u32 ocp_addr = RTL_PHY_OCP_ADDR_PHYREG_BASE + regnum * 2;
	int ret;

	ret = rtl_ia_wait();
	if (ret)
		return ret;

	ret = rtl_ocp_prepare(phy, ocp_addr);
	if (ret)
		return ret;

	rtl_write(RTL_IA_WRITE_DATA_REG, val);
	rtl_write(RTL_IA_CTRL_REG, RTL_IA_CTRL_RW_WRITE | RTL_IA_CTRL_CMD);

	return rtl_ia_wait();
}

static void rtl_wake_phy(int phy)
{
	u16 bmcr, id1 = 0;
	int ret;

	/* A PHY that is not there leaves the data register holding whatever
	 * the last read put in it, so check the OUI before believing BMCR.
	 */
	if (rtl_phy_read(phy, MII_PHYSID1, &id1) || id1 != 0x001C) {
		pr_warn("rtl8367s-nss: PHY%d PHYSID1=0x%04X, not a Realtek PHY - skipping\n",
			phy, id1);
		return;
	}

	ret = rtl_phy_read(phy, MII_BMCR, &bmcr);
	if (ret) {
		pr_err("rtl8367s-nss: PHY%d BMCR read failed: %d\n", phy, ret);
		return;
	}

	if (!(bmcr & BMCR_PDOWN)) {
		if (verbose)
			pr_info("rtl8367s-nss: PHY%d BMCR=0x%04X, already awake\n",
				phy, bmcr);
		return;
	}

	if (dry_run) {
		pr_info("rtl8367s-nss: PHY%d BMCR=0x%04X, would wake\n",
			phy, bmcr);
		return;
	}

	bmcr &= ~BMCR_PDOWN;
	bmcr |= BMCR_ANENABLE | BMCR_ANRESTART;

	ret = rtl_phy_write(phy, MII_BMCR, bmcr);
	if (ret) {
		pr_err("rtl8367s-nss: PHY%d BMCR write failed: %d\n", phy, ret);
		return;
	}

	if (!rtl_phy_read(phy, MII_BMCR, &bmcr))
		pr_info("rtl8367s-nss: PHY%d woken, BMCR=0x%04X\n", phy, bmcr);
}

/* Walk a comma separated list, calling fn for each entry. */
static void rtl_for_each(const char *spec, int max, void (*fn)(int))
{
	char *list, *tok, *p;
	int n;

	list = kstrdup(spec, GFP_KERNEL);
	if (!list)
		return;

	p = list;
	while ((tok = strsep(&p, ",")) != NULL) {
		if (!*tok)
			continue;
		if (kstrtoint(tok, 0, &n) || n < 0 || n > max) {
			pr_warn("rtl8367s-nss: bad entry '%s'\n", tok);
			continue;
		}
		fn(n);
	}

	kfree(list);
}

static void rtl_port_learning(int port)
{
	rtl_update(RTL_LEARN_LIMIT_REG(port), 0xFFFF, RTL_LEARN_LIMIT_MAX);
}

static void rtl_egress_original(int port)
{
	rtl_update(RTL_PORT_MISC_CFG_REG(port), RTL_VLAN_EGRESS_MODE_MASK,
		   RTL_VLAN_EGRESS_MODE_ORIGINAL);
}

static void rtl_port_forward(int port)
{
	rtl_update(RTL_MSTI_CTRL_REG(port), RTL_MSTI_STATE_MASK(port),
		   RTL_MSTI_STATE_FORWARDING << (((port) & 7) << 1));
	rtl_update(RTL_PORT_ISOLATION_REG(port), RTL_PORT_ISOLATION_MASK,
		   RTL_PORT_ISOLATION_MASK);
}

/* ===== VLAN ===== */

static int rtl_table_wait(void)
{
	u16 st;
	int i;

	for (i = 0; i < 100; i++) {
		if (rtl_read(RTL_TABLE_STATUS_REG, &st))
			return -EIO;
		if (!(st & RTL_TABLE_STATUS_BUSY))
			return 0;
		usleep_range(10, 20);
	}

	return -ETIMEDOUT;
}

static int rtl_cvlan_write(u16 vid, u16 member, u16 untag, u16 fid)
{
	u16 data[RTL_CVLAN_ENTRY_SIZE] = { 0 };
	int i, ret;

	data[0] = (member & 0x00FF) | ((untag & 0x00FF) << 8);
	data[1] = RTL_D_FID_BITS(fid);
	data[2] = ((member >> 8) & 0x7) | (((untag >> 8) & 0x7) << 3);

	if (dry_run) {
		pr_info("rtl8367s-nss: VLAN %u would be member=0x%03X untag=0x%03X (%04X %04X %04X)\n",
			vid, member, untag, data[0], data[1], data[2]);
		return 0;
	}

	ret = rtl_table_wait();
	if (ret)
		return ret;

	for (i = 0; i < RTL_CVLAN_ENTRY_SIZE; i++)
		rtl_write(RTL_TABLE_WRITE_BASE + i, data[i]);

	rtl_write(RTL_TABLE_ADDR_REG, vid);
	rtl_write(RTL_TABLE_CTRL_REG,
		  RTL_TABLE_CTRL_TABLE_CVLAN | RTL_TABLE_CTRL_OP_WRITE);

	ret = rtl_table_wait();
	if (ret) {
		pr_err("rtl8367s-nss: VLAN %u write timed out\n", vid);
		return ret;
	}

	if (verbose)
		pr_info("rtl8367s-nss: VLAN %u member=0x%03X untag=0x%03X\n",
			vid, member, untag);
	return 0;
}

/* Read a CVLAN entry back. The write path reports no error even when the
 * entry does not land, so the only way to tell is to ask for it again.
 */
static void rtl_cvlan_dump(u16 vid)
{
	u16 d[RTL_CVLAN_ENTRY_SIZE] = { 0 };
	u16 member, untag;
	int i, ret;

	ret = rtl_table_wait();
	if (ret) {
		pr_err("rtl8367s-nss: VLAN %u readback: table busy\n", vid);
		return;
	}

	rtl_write(RTL_TABLE_ADDR_REG, vid);
	rtl_write(RTL_TABLE_CTRL_REG,
		  RTL_TABLE_CTRL_TABLE_CVLAN | RTL_TABLE_CTRL_OP_READ);

	ret = rtl_table_wait();
	if (ret) {
		pr_err("rtl8367s-nss: VLAN %u readback timed out\n", vid);
		return;
	}

	for (i = 0; i < RTL_CVLAN_ENTRY_SIZE; i++)
		if (rtl_read(RTL_TABLE_READ_BASE + i, &d[i]))
			return;

	member = (d[0] & 0x00FF) | ((d[2] & 0x7) << 8);
	untag = ((d[0] >> 8) & 0x00FF) | (((d[2] >> 3) & 0x7) << 8);

	pr_info("rtl8367s-nss: VLAN %u readback: %04X %04X %04X -> member=0x%03X untag=0x%03X\n",
		vid, d[0], d[1], d[2], member, untag);
}

/* Parse one "vid:0u,1u,6t" group and program it. */
static void rtl_vlan_one(char *spec)
{
	u16 member = 0, untag = 0;
	char *colon, *tok, *p;
	int vid, port;

	colon = strchr(spec, ':');
	if (!colon) {
		pr_warn("rtl8367s-nss: bad vlan spec '%s'\n", spec);
		return;
	}
	*colon = '\0';

	if (kstrtoint(spec, 0, &vid) || vid < 1 || vid > 4095) {
		pr_warn("rtl8367s-nss: bad vid '%s'\n", spec);
		return;
	}

	p = colon + 1;
	while ((tok = strsep(&p, ",")) != NULL) {
		size_t n = strlen(tok);
		char mode;

		if (n < 2)
			continue;
		mode = tok[n - 1];
		tok[n - 1] = '\0';

		if (kstrtoint(tok, 0, &port) || port < 0 || port > 10) {
			pr_warn("rtl8367s-nss: bad port '%s'\n", tok);
			continue;
		}

		member |= BIT(port);
		if (mode == 'u' || mode == 'U')
			untag |= BIT(port);
		else if (mode != 't' && mode != 'T')
			pr_warn("rtl8367s-nss: bad mode '%c' for port %d\n",
				mode, port);
	}

	rtl_cvlan_write(vid, member, untag, 0);
	if (verbose || dry_run)
		rtl_cvlan_dump(vid);
}

static void rtl_vlan_program(void)
{
	char *list, *grp, *p;

	/* The table is useless unless VLAN handling is switched on. */
	rtl_update(RTL_VLAN_CTRL_REG, RTL_VLAN_CTRL_EN, RTL_VLAN_CTRL_EN);

	list = kstrdup(vlans, GFP_KERNEL);
	if (!list)
		return;

	p = list;
	while ((grp = strsep(&p, ";")) != NULL)
		if (*grp)
			rtl_vlan_one(grp);

	kfree(list);
}

static void rtl_pvid_program(void)
{
	char *list, *tok, *p, *colon;
	int port, vid;

	list = kstrdup(pvids, GFP_KERNEL);
	if (!list)
		return;

	p = list;
	while ((tok = strsep(&p, ",")) != NULL) {
		if (!*tok)
			continue;
		colon = strchr(tok, ':');
		if (!colon)
			continue;
		*colon = '\0';
		if (kstrtoint(tok, 0, &port) || kstrtoint(colon + 1, 0, &vid))
			continue;
		if (port < 0 || port > 10 || vid < 1 || vid > 4095)
			continue;

		rtl_update(RTL_D_PVID_REG(port), RTL_D_PVID_MASK, vid);
		/* Accept any frame type: 0 in the two-bit field. Untagged
		 * ingress is what the PVID is for.
		 */
		rtl_update(RTL_ACCEPT_FRAME_REG(port),
			   RTL_ACCEPT_FRAME_MASK(port), 0);
	}

	kfree(list);
}

/* ===== MIB ===== */

static int rtl_mib_read(int port, u32 offset, u32 length, u64 *out)
{
	u16 val = 0;
	u64 v = 0;
	int i, ret;

	rtl_write(RTL_MIB_ADDRESS_REG, RTL_MIB_ADDRESS(port, offset));

	for (i = 0; i < 100; i++) {
		ret = rtl_read(RTL_MIB_CTRL0_REG, &val);
		if (ret)
			return ret;
		if (!(val & RTL_MIB_CTRL0_BUSY_MASK))
			break;
		usleep_range(10, 20);
	}
	if (val & RTL_MIB_CTRL0_BUSY_MASK)
		return -ETIMEDOUT;
	if (val & RTL_MIB_CTRL0_RESET_MASK)
		return -EIO;

	/* Four counter registers hold one counter; a 4-word value starts at
	 * the top, a 2-word value at (offset + 1) % 4.
	 */
	offset = (length == 4) ? 3 : (offset + 1) % 4;

	for (i = 0; i < length; i++) {
		ret = rtl_read(RTL_MIB_COUNTER_REG(offset - i), &val);
		if (ret)
			return ret;
		v = (v << 16) | val;
	}

	*out = v;
	return 0;
}

static void rtl_mib_show(void)
{
	static const int ports[] = { 0, 1, 2, 3, 4, 6 };
	u64 in, out;
	int i;

	pr_info("rtl8367s-nss: MIB port    rx octets      tx octets   rx ucast  tx ucast  tx mcast  tx bcast  pause in  pause out\n");

	for (i = 0; i < (int)ARRAY_SIZE(ports); i++) {
		int port = ports[i];
		u64 rxp, txp, txm, txb, pin, pout;

		if (rtl_mib_read(port, RTL_MIB_IF_IN_OCTETS, 4, &in) ||
		    rtl_mib_read(port, RTL_MIB_IF_OUT_OCTETS, 4, &out) ||
		    rtl_mib_read(port, RTL_MIB_IF_IN_UCAST, 2, &rxp) ||
		    rtl_mib_read(port, RTL_MIB_IF_OUT_UCAST, 2, &txp) ||
		    rtl_mib_read(port, RTL_MIB_IF_OUT_MCAST, 2, &txm) ||
		    rtl_mib_read(port, RTL_MIB_IF_OUT_BCAST, 2, &txb) ||
		    rtl_mib_read(port, RTL_MIB_IN_PAUSE, 2, &pin) ||
		    rtl_mib_read(port, RTL_MIB_OUT_PAUSE, 2, &pout)) {
			pr_warn("rtl8367s-nss: MIB port %d read failed\n",
				port);
			continue;
		}

		pr_info("rtl8367s-nss: MIB %4d %12llu %14llu %10llu %9llu %9llu %9llu %9llu %9llu\n",
			port, in, out, rxp, txp, txm, txb, pin, pout);
	}
}

/* ===== L2 database ===== */

static void rtl_l2_show(void)
{
	u16 d[RTL_L2_ENTRY_SIZE];
	u16 addr = 0, st, at;
	int found = 0;
	int i, n;

	pr_info("rtl8367s-nss: L2  idx           mac  vid  raw words\n");

	for (n = 0; n < 2048; n++) {
		u8 mac[6];

		if (rtl_table_wait())
			break;

		rtl_write(RTL_TABLE_ADDR_REG, addr);
		rtl_write(RTL_TABLE_CTRL_REG,
			  RTL_TABLE_CTRL_TABLE_L2 | RTL_TABLE_CTRL_OP_READ |
			  RTL_TABLE_CTRL_METHOD_NEXT_UC);

		if (rtl_table_wait())
			break;

		if (rtl_read(RTL_TABLE_STATUS_REG, &st))
			break;
		if (!(st & RTL_TABLE_STATUS_HIT))
			break;

		for (i = 0; i < RTL_L2_ENTRY_SIZE; i++)
			if (rtl_read(RTL_TABLE_READ_BASE + i, &d[i]))
				return;

		at = st & RTL_TABLE_STATUS_ADDRESS_MASK;

		/* NEXT_UC wraps to the first entry once past the last one,
		 * so a non-advancing index means the walk is done.
		 */
		if (found && at < addr)
			break;

		mac[5] = d[0] & 0xFF;
		mac[4] = d[0] >> 8;
		mac[3] = d[1] & 0xFF;
		mac[2] = d[1] >> 8;
		mac[1] = d[2] & 0xFF;
		mac[0] = d[2] >> 8;

		/* The port field is printed raw: the entry layout is only
		 * half decoded, so show the words and read them by hand.
		 */
		pr_info("rtl8367s-nss: L2 %4u %02X:%02X:%02X:%02X:%02X:%02X %4u  %04X %04X %04X\n",
			at, mac[0], mac[1], mac[2], mac[3], mac[4], mac[5],
			d[3] & 0x0FFF, d[3], d[4], d[5]);
		found++;

		addr = at + 1;
		if (addr >= 2048)
			break;
	}

	pr_info("rtl8367s-nss: L2 %d unicast entries\n", found);
}

static int __init rtl_nss_init(void)
{
	u16 chip_id, chip_ver;

	rbus = mdio_find_bus(bus_id);
	if (!rbus) {
		pr_err("rtl8367s-nss: no MDIO bus '%s'\n", bus_id);
		return -ENODEV;
	}

	pr_info("rtl8367s-nss: bus %s addr 0x%02x trunk port %d%s\n",
		bus_id, sw_addr, trunk_port, dry_run ? " (dry run)" : "");

	if (rtl_chip_id(&chip_id, &chip_ver) || chip_id != RTL_CHIP_ID_RTL8367S_VB) {
		pr_err("rtl8367s-nss: chip id 0x%04X ver 0x%04X is not an RTL8367S-VB, refusing\n",
		       chip_id, chip_ver);
		put_device(&rbus->dev);
		rbus = NULL;
		return -ENODEV;
	}
	pr_info("rtl8367s-nss: RTL8367S-VB, chip id 0x%04X ver 0x%04X\n",
		chip_id, chip_ver);

	if (l2_dump) {
		rtl_l2_show();
		put_device(&rbus->dev);
		rbus = NULL;
		return -EAGAIN;
	}

	if (mib_dump) {
		rtl_mib_show();
		put_device(&rbus->dev);
		rbus = NULL;
		return -EAGAIN;
	}

	/* 1. the trunk's force word - the only thing teardown clears */
	rtl_update(RTL_D_FORCE_BASE + trunk_port, 0xFFFF, force_val);
	rtl_update(RTL_D_FORCE_EN_BASE + trunk_port, 0xFFFF,
		   RTL_D_FORCE_EN_ALL);

	/* 2. the CPU tag the NSS firmware cannot parse */
	if (cpu_tag_off)
		rtl_update(RTL_CPU_CTRL_REG,
			   RTL_CPU_CTRL_EN_MASK | RTL_CPU_CTRL_INSERTMODE_MASK,
			   0);

	/* 3. forwarding state and isolation masks teardown stripped */
	rtl_for_each(ports, 10, rtl_port_forward);

	/* 4. the VLAN table and PVIDs DSA removed on the way out */
	rtl_vlan_program();
	rtl_pvid_program();
	rtl_for_each(ports, 10, rtl_egress_original);
	rtl_for_each(ports, 10, rtl_port_learning);

	/* 5. the front PHYs phy_detach parked */
	rtl_for_each(phys, 7, rtl_wake_phy);

	pr_info("rtl8367s-nss: switch re-armed: trunk port %d forced 0x%04X, VLANs %s\n",
		trunk_port, force_val, vlans);

	put_device(&rbus->dev);
	rbus = NULL;

	/* Nothing is left to hold: the switch keeps what was written to it.
	 * Refusing to load means a rerun needs no rmmod first.
	 */
	return -EAGAIN;
}

module_init(rtl_nss_init);

MODULE_DESCRIPTION("Re-arm RTL8367S-VB after rtl8365mb teardown");
MODULE_LICENSE("GPL");
