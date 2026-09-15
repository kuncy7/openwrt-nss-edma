// SPDX-License-Identifier: GPL-2.0-only
/*
 * qca8337_nss.c - re-arm the QCA8337 fabric after qca8k teardown.
 *
 * The VLAN-separated NSS data path ("road 2") needs the switch CPU port
 * to carry plain 802.1q frames: the firmware parses VLAN tags natively
 * (dynamic interface type 17) but cannot parse the 2-byte Atheros header
 * that DSA's tag_qca puts in front of the ethertype. So qca8k is unbound
 * at runtime - after it has done the hard bring-up work (SerDes, clocks,
 * uniphy) - and this module undoes just what its teardown broke:
 *
 * Keep MACs disabled while restoring headerless forwarding and optional
 * VLAN policy. Read back the configuration before enabling traffic. Bus
 * and setup errors fail module init and trigger best-effort port blocking.
 * An empty VLAN map retains the flat-fabric mode; PHY-linked CPU ports
 * can still autonegotiate with cpu_port=255.
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/bitfield.h>
#include <linux/delay.h>
#include <linux/mdio.h>
#include <linux/phy.h>

static bool switch_fixup = true;
module_param(switch_fixup, bool, 0444);
MODULE_PARM_DESC(switch_fixup, "Re-enable QCA8337 fabric, Atheros header off (default on)");

static char *wake_phys = "90000.mdio-1:00,90000.mdio-1:01,90000.mdio-1:02";
module_param(wake_phys, charp, 0444);

/*
 * VLAN separation of the front ports (the VTU follow-up the header above
 * promised). Format: one block per VLAN, semicolon separated:
 *
 *     vlans=<vid>:<port><t|u>[,<port><t|u>]...[;...]
 *
 * e.g. the GL-B3000 router split - lan1+lan2 on VLAN 1, wan on VLAN 2,
 * the CPU port carrying both tagged:
 *
 *     vlans=1:0t,2u,3u;2:0t,1u
 *
 * A port listed 'u' (untagged egress) also gets the vid as its PVID for
 * ingress. Port isolation follows from the VLAN table: each port's
 * lookup member mask is narrowed to the ports it shares a VLAN with,
 * and the lookup VLAN mode is set to SECURE so ingress is checked
 * against the VTU. Empty string disables the whole feature.
 */
static char *vlans = "";
module_param(vlans, charp, 0444);
MODULE_PARM_DESC(vlans, "VTU program, e.g. '1:0t,2u,3u;2:0t,1u' (empty = off)");
MODULE_PARM_DESC(wake_phys, "Comma list of mdio device names to power up (BMCR)");

static char *bus_via = "90000.mdio-1:01";
module_param(bus_via, charp, 0444);
MODULE_PARM_DESC(bus_via, "Any mdio device on the switch bus, used to reach it");

/*
 * Which switch ports exist on this board and which of them is the CPU
 * port. The QCA8337 has seven (0-6); the CPU link to the SoC's GMAC is
 * on port 0 on some boards (GL-B3000, Exigo D50) and on port 6 on others
 * (Linksys MX2000, MR5500). Defaults are the B3000 wiring: CPU on 0,
 * front ports 1-3.
 */
static unsigned int cpu_port;
module_param(cpu_port, uint, 0444);
MODULE_PARM_DESC(cpu_port, "Switch port wired to the SoC GMAC, forced 1G FD (default 0; 255 = none, for a CPU link that goes through a PHY and autonegotiates)");

static unsigned int ports = 0x0f;
module_param(ports, uint, 0444);
MODULE_PARM_DESC(ports, "Bitmask of switch ports to enable, CPU port included (default 0x0f = ports 0-3)");

#define G8_MASK_CTRL		0x000
#define   G8_DEVICE_ID		GENMASK(15, 8)
#define   G8_ID_QCA8337		0x13
#define G8_PORT_STATUS(i)	(0x07c + (i) * 4)
#define G8_PORT_HDR_CTRL(i)	(0x9c + (i) * 4)
#define G8_PORT_LOOKUP(i)	(0x660 + (i) * 0xc)
#define   G8_LOOKUP_LEARN	BIT(20)
#define G8_GLOBAL_FW_CTRL1	0x624
#define G8_ATU_FUNC		0x60c
#define   G8_ATU_BUSY		BIT(31)
#define   G8_ATU_CMD_FLUSH	1

/* VLAN table and per-port VLAN registers (names as in qca8k.h) */
#define G8_PORT_VLAN_CTRL0(i)	(0x420 + (i) * 8)
#define G8_PORT_VLAN_CTRL1(i)	(0x424 + (i) * 8)
#define   G8_OUT_MODE_MASK	GENMASK(13, 12)
#define   G8_OUT_MODE_UNTOUCH	FIELD_PREP(GENMASK(13, 12), 0x3)
#define G8_EGRESS_VLAN(i)	(0x0c70 + 4 * ((i) / 2))
#define G8_VTU_FUNC0		0x610
#define   G8_VTU_VALID		BIT(20)
#define   G8_VTU_IVL_EN		BIT(19)
#define   G8_VTU_EG_SHIFT(i)	(4 + (i) * 2)
#define   G8_VTU_EG_UNTAG	0x1
#define   G8_VTU_EG_TAG		0x2
#define   G8_VTU_EG_NOT		0x3
#define G8_VTU_FUNC1		0x614
#define   G8_VTU_BUSY		BIT(31)
#define   G8_VTU_FULL		BIT(4)
#define   G8_VTU_CMD_LOAD	2
#define   G8_VTU_CMD_READ	6
#define   G8_VTU_VID(v)		((u32)(v) << 16)
#define G8_LOOKUP_VLAN_SECURE	FIELD_PREP(GENMASK(9, 8), 0x3)

#define G8_NPORTS		7

static void g8_split(u32 reg, u16 *r1, u16 *r2, u16 *page)
{
	reg >>= 1;
	*r1 = reg & 0x1e;
	reg >>= 5;
	*r2 = reg & 0x7;
	reg >>= 3;
	*page = reg & 0x3ff;
}

/* Init is single-threaded. Preserve the first bus failure through helpers. */
static int g8_error;

static u32 g8_read(struct mii_bus *bus, u32 reg)
{
	u16 r1, r2, page;
	int ret, lo, hi;

	g8_split(reg, &r1, &r2, &page);
	ret = mdiobus_write(bus, 0x18, 0, page);
	if (ret < 0)
		goto failed;
	usleep_range(100, 200);
	lo = mdiobus_read(bus, 0x10 | r2, r1);
	if (lo < 0) {
		ret = lo;
		goto failed;
	}
	hi = mdiobus_read(bus, 0x10 | r2, r1 + 1);
	if (hi < 0) {
		ret = hi;
		goto failed;
	}
	return ((u32)hi << 16) | lo;
failed:
	if (!g8_error)
		g8_error = ret;
	return 0;
}

static void g8_write(struct mii_bus *bus, u32 reg, u32 val)
{
	u16 r1, r2, page;
	int ret;

	g8_split(reg, &r1, &r2, &page);
	ret = mdiobus_write(bus, 0x18, 0, page);
	if (ret < 0)
		goto failed;
	usleep_range(100, 200);
	ret = mdiobus_write(bus, 0x10 | r2, r1, val & 0xffff);
	if (ret < 0)
		goto failed;
	ret = mdiobus_write(bus, 0x10 | r2, r1 + 1, val >> 16);
	if (ret >= 0)
		return;
failed:
	if (!g8_error)
		g8_error = ret;
}

static int g8_verify(struct mii_bus *bus, u32 reg, u32 val, u32 mask)
{
	u32 actual = g8_read(bus, reg);

	if (g8_error)
		return g8_error;
	if ((actual & mask) == (val & mask))
		return 0;
	pr_err("qca8337-nss: verify reg=0x%x actual=0x%x expected=0x%x mask=0x%x\n",
	       reg, actual, val, mask);
	return -EIO;
}

/* Never stop after one error: attempt to block every port on failure too. */
static int g8_block_ports(struct mii_bus *bus)
{
	int p, ret = 0;

	g8_write(bus, G8_GLOBAL_FW_CTRL1, 0);
	for (p = 0; p < G8_NPORTS; p++) {
		g8_write(bus, G8_PORT_STATUS(p), 0);
		g8_write(bus, G8_PORT_LOOKUP(p), 0);
	}
	for (p = 0; p < G8_NPORTS; p++) {
		if (g8_verify(bus, G8_PORT_STATUS(p), 0, BIT(2) | BIT(3) | BIT(9)) ||
		    g8_verify(bus, G8_PORT_LOOKUP(p), 0,
			      GENMASK(6, 0) | GENMASK(18, 16)))
			ret = -EIO;
	}
	return g8_error ? g8_error : ret;
}

static int fixup_vlans(struct mii_bus *bus);
static int fixup_wake_phys(void);

static int fixup_switch(void)
{
	struct device *d;
	struct mii_bus *bus;
	int p, ret;
	u32 status;

	/* Preserve the existing board parameter conventions. */
	ports &= GENMASK(G8_NPORTS - 1, 0);
	if (cpu_port < G8_NPORTS)
		ports |= BIT(cpu_port);
	if (!ports)
		return -EINVAL;

	d = bus_find_device_by_name(&mdio_bus_type, NULL, bus_via);
	if (!d)
		return -ENODEV;
	bus = to_phy_device(d)->mdio.bus;

	/* Identify the chip before any configuration or failure-path writes. */
	status = g8_read(bus, G8_MASK_CTRL);
	if (g8_error) {
		ret = g8_error;
		goto out;
	}
	pr_info("qca8337-nss: chip id reg0=0x%08x\n", status);
	if (FIELD_GET(G8_DEVICE_ID, status) != G8_ID_QCA8337) {
		ret = -ENODEV;
		goto out;
	}

	/* qca8k's isolated topology remains until all ports are disabled. */
	ret = g8_block_ports(bus);
	if (ret)
		goto failed;

	for (p = 0; p < G8_NPORTS; p++) {
		u32 lkp;

		if (!(ports & BIT(p)))
			continue;
		g8_write(bus, G8_PORT_HDR_CTRL(p), 0);
		ret = g8_verify(bus, G8_PORT_HDR_CTRL(p), 0, ~0U);
		if (ret)
			goto failed;

		/* Flat fabric by default; optional VLAN policy narrows it below. */
		lkp = (ports & ~BIT(p)) | G8_LOOKUP_LEARN |
			FIELD_PREP(GENMASK(18, 16), 0x4);
		g8_write(bus, G8_PORT_LOOKUP(p), lkp);
		ret = g8_verify(bus, G8_PORT_LOOKUP(p), lkp, ~0U);
		if (ret)
			goto failed;
	}

	/* No external MAC can transmit/receive while VTU/PVIDs are changed. */
	if (*vlans) {
		ret = fixup_vlans(bus);
		if (ret)
			goto failed;
	}

	g8_write(bus, G8_GLOBAL_FW_CTRL1,
		 FIELD_PREP(GENMASK(30, 24), ports) |
		 FIELD_PREP(GENMASK(22, 16), ports) |
		 FIELD_PREP(GENMASK(14, 8), ports) |
		 FIELD_PREP(GENMASK(6, 0), ports));
	status = g8_read(bus, G8_GLOBAL_FW_CTRL1);
	if (g8_error) {
		ret = g8_error;
		goto failed;
	}
	pr_info("qca8337-nss: fw_ctrl1=0x%08x\n", status);
	g8_write(bus, G8_ATU_FUNC, G8_ATU_BUSY | G8_ATU_CMD_FLUSH);
	for (p = 0; p < 20 && (g8_read(bus, G8_ATU_FUNC) & G8_ATU_BUSY); p++)
		usleep_range(100, 200);
	status = g8_read(bus, G8_ATU_FUNC);
	if (g8_error || (status & G8_ATU_BUSY)) {
		ret = g8_error ? g8_error : -ETIMEDOUT;
		pr_err("qca8337-nss: ARL flush failed (%d)\n", ret);
		goto failed;
	}
	pr_info("qca8337-nss: ARL flushed\n");
	ret = fixup_wake_phys();
	if (ret)
		goto failed;

	/* The requested forwarding policy is installed before any MAC is enabled. */
	for (p = 0; p < G8_NPORTS; p++) {
		u32 lookup, header;

		if (!(ports & BIT(p)))
			continue;
		status = BIT(2) | BIT(3);
		status |= (unsigned int)p == cpu_port ? 0x2 | BIT(6) : BIT(9);
		g8_write(bus, G8_PORT_STATUS(p), status);
		/* LINK_AUTO owns RX/TX state on jacks, including a valid down link. */
		ret = g8_verify(bus, G8_PORT_STATUS(p), status,
				(unsigned int)p == cpu_port ? BIT(2) | BIT(3) : BIT(9));
		if (ret)
			goto failed;
		status = g8_read(bus, G8_PORT_STATUS(p));
		lookup = g8_read(bus, G8_PORT_LOOKUP(p));
		header = g8_read(bus, G8_PORT_HDR_CTRL(p));
		if (g8_error) {
			ret = g8_error;
			goto failed;
		}
		pr_info("qca8337-nss: port%d status=0x%08x lookup=0x%08x hdr=0x%08x\n",
			p, status, lookup, header);
	}
	pr_info("qca8337-nss: verified fabric enabled: cpu=%u ports=0x%02x\n",
		cpu_port, ports);
	put_device(d);
	return 0;
failed:
	/* MDIO failure can prevent blocking too; do not retry with defaults. */
	g8_block_ports(bus);
	pr_err("qca8337-nss: fabric setup failed (%d), port blocking attempted\n", ret);
out:
	put_device(d);
	return ret;
}

static int g8_vtu_wait(struct mii_bus *bus)
{
	int i;

	for (i = 0; i < 20; i++) {
		if (!(g8_read(bus, G8_VTU_FUNC1) & G8_VTU_BUSY))
			return g8_error;
		usleep_range(100, 200);
	}
	return g8_error ? g8_error : -ETIMEDOUT;
}

static int g8_vtu_load(struct mii_bus *bus, u16 vid, u32 func0)
{
	g8_write(bus, G8_VTU_FUNC0, func0);
	g8_write(bus, G8_VTU_FUNC1, G8_VTU_BUSY | G8_VTU_CMD_LOAD | G8_VTU_VID(vid));
	if (g8_error)
		return g8_error;
	if (g8_vtu_wait(bus))
		return g8_error ? g8_error : -ETIMEDOUT;
	if (g8_read(bus, G8_VTU_FUNC1) & G8_VTU_FULL)
		return -ENOMEM;
	if (g8_error)
		return g8_error;

	/* FUNC0 is a command window, not a persistent table readback. */
	g8_write(bus, G8_VTU_FUNC1, G8_VTU_BUSY | G8_VTU_CMD_READ | G8_VTU_VID(vid));
	if (g8_vtu_wait(bus))
		return g8_error ? g8_error : -ETIMEDOUT;
	return g8_verify(bus, G8_VTU_FUNC0, func0,
			 G8_VTU_VALID | G8_VTU_IVL_EN | GENMASK(17, 4));
}

static int fixup_vlans(struct mii_bus *bus)
{
	u16 pvid[G8_NPORTS] = {};
	u8 member[G8_NPORTS] = {};
	u8 listed = 0;
	char *buf, *blk, *cur;
	int p, ret = 0;

	buf = kstrdup(vlans, GFP_KERNEL);
	if (!buf)
		return -ENOMEM;

	cur = buf;
	while ((blk = strsep(&cur, ";"))) {
		char *portstr, *vidstr, *tok;
		unsigned int vid;
		u8 in_vlan = 0;
		u32 func0;

		if (!*blk)
			continue;
		portstr = blk;
		vidstr = strsep(&portstr, ":");
		if (!portstr || kstrtouint(vidstr, 0, &vid) || !vid || vid > 4094) {
			pr_err("qca8337-nss: vlans: bad block '%s'\n", vidstr);
			ret = -EINVAL;
			goto out;
		}

		/* every port not listed egresses NOT_MEMBER */
		func0 = G8_VTU_VALID | G8_VTU_IVL_EN;
		for (p = 0; p < G8_NPORTS; p++)
			func0 |= G8_VTU_EG_NOT << G8_VTU_EG_SHIFT(p);

		while ((tok = strsep(&portstr, ","))) {
			unsigned int port;
			char mode;
			size_t n;

			if (!*tok)
				continue;
			n = strlen(tok);
			mode = tok[n - 1];
			tok[n - 1] = '\0';
			if (kstrtouint(tok, 0, &port) || port >= G8_NPORTS ||
			    !(ports & BIT(port)) ||
			    (mode != 't' && mode != 'u')) {
				pr_err("qca8337-nss: vlans: bad port spec '%s%c' in vid %u\n",
				       tok, mode, vid);
				ret = -EINVAL;
				goto out;
			}
			func0 &= ~(0x3u << G8_VTU_EG_SHIFT(port));
			func0 |= (mode == 'u' ? G8_VTU_EG_UNTAG : G8_VTU_EG_TAG)
				 << G8_VTU_EG_SHIFT(port);
			in_vlan |= BIT(port);
			listed |= BIT(port);
			if (mode == 'u')
				pvid[port] = vid;
		}

		ret = g8_vtu_load(bus, vid, func0);
		if (ret) {
			pr_err("qca8337-nss: vlans: VTU load failed for vid %u\n", vid);
			goto out;
		}

		/* members of one VLAN may reach each other */
		for (p = 0; p < G8_NPORTS; p++)
			if (in_vlan & BIT(p))
				member[p] |= in_vlan & ~BIT(p);

		pr_info("qca8337-nss: vlans: vid %u func0=0x%08x members=0x%02x\n",
			vid, g8_read(bus, G8_VTU_FUNC0), in_vlan);
	}

	if (!listed) {
		ret = -EINVAL;
		goto out;
	}

	/* PVID for untagged ingress + per-port default egress vid */
	for (p = 0; p < G8_NPORTS; p++) {
		u32 reg;

		if (!(listed & BIT(p)))
			continue;

		/*
		 * Egress tagging must come from the VTU entry, not the
		 * port-global mode - OUT_MODE=UNTOUCH hands the decision
		 * to the per-VLAN eg bits (the reset default, UNMOD,
		 * forwards frames exactly as they came in, so the CPU
		 * port never saw a tag).
		 */
		reg = g8_read(bus, G8_PORT_VLAN_CTRL1(p));
		reg &= ~G8_OUT_MODE_MASK;
		reg |= G8_OUT_MODE_UNTOUCH;
		g8_write(bus, G8_PORT_VLAN_CTRL1(p), reg);
		ret = g8_verify(bus, G8_PORT_VLAN_CTRL1(p), reg, G8_OUT_MODE_MASK);
		if (ret)
			goto out;

		if (!pvid[p])
			continue;
		g8_write(bus, G8_PORT_VLAN_CTRL0(p),
			 ((u32)pvid[p] << 16) | pvid[p]);
		reg = g8_read(bus, G8_EGRESS_VLAN(p));
		reg &= ~(GENMASK(11, 0) << (16 * (p % 2)));
		reg |= (u32)pvid[p] << (16 * (p % 2));
		g8_write(bus, G8_EGRESS_VLAN(p), reg);
		ret = g8_verify(bus, G8_PORT_VLAN_CTRL0(p),
				((u32)pvid[p] << 16) | pvid[p], ~0U);
		if (!ret)
			ret = g8_verify(bus, G8_EGRESS_VLAN(p), reg,
					GENMASK(11, 0) << (16 * (p % 2)));
		if (ret)
			goto out;
	}

	/*
	 * Narrow each port's lookup member mask to its VLAN peers and check
	 * ingress against the VTU. Ports in no VLAN are left as fixup_switch
	 * set them - this runs after it, so only listed ports are touched.
	 */
	for (p = 0; p < G8_NPORTS; p++) {
		u32 lkp;

		if (!(listed & BIT(p)))
			continue;
		lkp = g8_read(bus, G8_PORT_LOOKUP(p));
		lkp &= ~GENMASK(6, 0);
		lkp |= member[p];
		lkp &= ~GENMASK(9, 8);
		lkp |= G8_LOOKUP_VLAN_SECURE | G8_LOOKUP_LEARN;
		lkp &= ~GENMASK(18, 16);
		lkp |= FIELD_PREP(GENMASK(18, 16), 0x4);
		g8_write(bus, G8_PORT_LOOKUP(p), lkp);
		ret = g8_verify(bus, G8_PORT_LOOKUP(p), lkp, ~0U);
		if (ret)
			goto out;
		pr_info("qca8337-nss: vlans: port%d lookup=0x%08x pvid=%u\n",
			p, g8_read(bus, G8_PORT_LOOKUP(p)), pvid[p]);
	}

out:
	kfree(buf);
	return ret ? ret : g8_error;
}

static int fixup_wake_phys(void)
{
	char *buf, *tok, *cur;
	int ret = 0;

	if (!*wake_phys)
		return 0;

	buf = kstrdup(wake_phys, GFP_KERNEL);
	if (!buf)
		return -ENOMEM;
	cur = buf;
	while ((tok = strsep(&cur, ","))) {
		struct device *d;
		struct phy_device *phydev;
		int bmcr;

		if (!*tok)
			continue;
		d = bus_find_device_by_name(&mdio_bus_type, NULL, tok);
		if (!d) {
			pr_warn("qca8337-nss: wake: no mdio dev %s\n", tok);
			continue;
		}
		phydev = to_phy_device(d);
		bmcr = phy_read(phydev, MII_BMCR);
		if (bmcr < 0) {
			ret = bmcr;
			put_device(d);
			break;
		}
		pr_info("qca8337-nss: %s BMCR=0x%04x%s\n", tok, bmcr,
			(bmcr & BMCR_PDOWN) ? " (POWER-DOWN)" : "");
		ret = phy_write(phydev, MII_BMCR,
			  (bmcr & ~BMCR_PDOWN) | BMCR_ANENABLE | BMCR_ANRESTART);
		if (ret >= 0) {
			bmcr = phy_read(phydev, MII_BMCR);
			if (bmcr < 0)
				ret = bmcr;
			else
				pr_info("qca8337-nss: %s woken, BMCR=0x%04x\n", tok, bmcr);
		}
		put_device(d);
		if (ret < 0)
			break;
	}
	kfree(buf);
	return ret;
}

static int __init qca8337_nss_init(void)
{
	if (switch_fixup)
		return fixup_switch();
	return fixup_wake_phys();
}

static void __exit qca8337_nss_exit(void)
{
}

module_init(qca8337_nss_init);
module_exit(qca8337_nss_exit);

MODULE_LICENSE("GPL v2");
MODULE_DESCRIPTION("QCA8337 fabric re-arm after qca8k teardown (headerless mode)");
