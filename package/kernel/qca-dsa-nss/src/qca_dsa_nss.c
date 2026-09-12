// SPDX-License-Identifier: GPL-2.0-only
/*
 * qca-dsa-nss: NSS firmware VLAN interfaces for the user ports of a DSA
 * switch driven by the qca-8021q tagger.
 *
 * With tag_8021q the switch talks to its CPU port in plain 802.1Q: a
 * standalone port carries its own VID (3072 + port), the ports of a
 * VLAN-unaware bridge share the bridge's VID (3088 + n) and the switch
 * forwards between them on its own. The NSS firmware parses 802.1Q
 * natively, so each of those VIDs can be a firmware VLAN interface hanging
 * off the conduit's phys_if - what qca-nss-vlan does for netifd's
 * eth0.<vid>, except that the netdev bound to the node is the DSA user
 * port itself. ECM treats a DSA user port as plain ethernet and only
 * needs an NSS interface number for it to write rules; this is where the
 * number comes from. RX and TX are untouched: exceptions come back on the
 * conduit with the tag on and go through the tagger, host TX goes
 * lan1 -> tagger -> conduit -> phys_if.
 *
 * A bridge's node is bound to the port the tagger's imprecise RX delivers
 * to (dsa_tag_8021q_find_user), so the host bridge's FDB and ECM's
 * interface hierarchy resolve to the one port that has the number. The set
 * of nodes is recomputed from the live topology after every relevant
 * netdev event; a node whose VID, port or phys_if no longer matches is
 * torn down and rebuilt.
 *
 * The firmware hands out dynamic interface numbers next-fit and does not
 * reuse a freed slot until the cursor wraps, which takes a moment: after
 * ~118 allocations since boot an allocation can fail for a few seconds
 * (measured on a GL-B3000, 2026-09-11). A failed allocation is retried
 * with a delay instead of being given up on.
 */
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/etherdevice.h>
#include <linux/if_vlan.h>
#include <linux/debugfs.h>
#include <linux/seq_file.h>
#include <linux/workqueue.h>
#include <linux/rtnetlink.h>
#include <linux/dsa/8021q.h>
#include <net/dsa.h>
#include <nss_api_if.h>

/* exported by net/dsa/tag_8021q.c but declared only in its private header */
struct net_device *dsa_tag_8021q_find_user(struct net_device *conduit,
					   int source_port, int switch_id,
					   int vid, int vbid);

#define DSA_NSS_MAX_NODES	16
#define DSA_NSS_COALESCE_MS	100
#define DSA_NSS_RETRY_MS	2000
#define DSA_NSS_MAX_RETRIES	10

struct dsa_nss_node {
	u16 vid;
	int if_num;			/* NSS dynamic interface, -1 = none */
	int phys_if;			/* conduit's NSS physical interface */
	struct net_device *ndev;	/* bound DSA user port, held */
	u8 mac[ETH_ALEN];
	unsigned int mtu;
};

static struct dsa_nss_node dsa_nss_nodes[DSA_NSS_MAX_NODES];
static DEFINE_MUTEX(dsa_nss_lock);	/* nodes[] */
static struct delayed_work dsa_nss_work;
static unsigned int dsa_nss_retries;
static atomic_t dsa_nss_resyncs = ATOMIC_INIT(0);
static atomic_t dsa_nss_alloc_failures = ATOMIC_INIT(0);
static atomic_t dsa_nss_creates = ATOMIC_INIT(0);
static atomic_t dsa_nss_destroys = ATOMIC_INIT(0);
static struct dentry *dsa_nss_dentry;

/*
 * ===== firmware node create / destroy (dsa_nss_lock held) =====
 */

static void dsa_nss_node_destroy(struct dsa_nss_node *n, const char *why)
{
	if (n->if_num < 0)
		return;

	pr_info("qca-dsa-nss: %s: drop vlan node if_num %d vid %u (%s)\n",
		netdev_name(n->ndev), n->if_num, n->vid, why);
	nss_unregister_vlan_if(n->if_num);
	if (nss_dynamic_interface_dealloc_node(n->if_num,
			NSS_DYNAMIC_INTERFACE_TYPE_VLAN) != NSS_TX_SUCCESS)
		pr_warn("qca-dsa-nss: %s: dealloc of if_num %d failed\n",
			netdev_name(n->ndev), n->if_num);
	dev_put(n->ndev);
	n->ndev = NULL;
	n->if_num = -1;
	atomic_inc(&dsa_nss_destroys);
}

/* -EAGAIN: no interface number right now, worth a retry; -EIO: hard fail */
static int dsa_nss_node_create(struct dsa_nss_node *n, u16 vid, int phys_if,
			       struct net_device *ndev)
{
	int if_num;

	if_num = nss_dynamic_interface_alloc_node(NSS_DYNAMIC_INTERFACE_TYPE_VLAN);
	if (if_num < 0) {
		atomic_inc(&dsa_nss_alloc_failures);
		return -EAGAIN;
	}

	n->vid = vid;
	n->phys_if = phys_if;
	n->ndev = ndev;
	dev_hold(ndev);
	ether_addr_copy(n->mac, ndev->dev_addr);
	n->mtu = ndev->mtu;

	if (!nss_register_vlan_if(if_num, NULL, ndev, 0, n)) {
		pr_warn("qca-dsa-nss: %s: register of if_num %d failed\n",
			netdev_name(ndev), if_num);
		nss_dynamic_interface_dealloc_node(if_num,
					NSS_DYNAMIC_INTERFACE_TYPE_VLAN);
		dev_put(ndev);
		n->ndev = NULL;
		return -EIO;
	}
	n->if_num = if_num;

	if (nss_vlan_tx_set_mac_addr_msg(if_num, n->mac) != NSS_TX_SUCCESS ||
	    nss_vlan_tx_set_mtu_msg(if_num, n->mtu) != NSS_TX_SUCCESS ||
	    nss_vlan_tx_add_tag_msg(if_num, (ETH_P_8021Q << 16) | vid,
				    phys_if, phys_if) != NSS_TX_SUCCESS) {
		pr_warn("qca-dsa-nss: %s: firmware refused vlan node if_num %d vid %u on phys_if %d\n",
			netdev_name(ndev), if_num, vid, phys_if);
		dsa_nss_node_destroy(n, "setup failed");
		return -EIO;
	}

	pr_info("qca-dsa-nss: %s: vlan node if_num %d vid %u on phys_if %d\n",
		netdev_name(ndev), if_num, vid, phys_if);
	atomic_inc(&dsa_nss_creates);
	return 0;
}

/*
 * ===== desired topology (rtnl held) =====
 */

struct dsa_nss_want {
	u16 vid;
	int phys_if;
	struct net_device *ndev;
};

static bool dsa_nss_port_is_8021q(const struct dsa_port *dp)
{
	return dp->cpu_dp && dp->cpu_dp->tag_ops &&
	       dp->cpu_dp->tag_ops->proto == DSA_TAG_PROTO_QCA_8021Q;
}

/*
 * One entry per standalone qca-8021q port and one per VLAN-unaware
 * bridge on such ports. Returns the count.
 */
static int dsa_nss_collect(struct dsa_nss_want *want, int max)
{
	struct net_device *dev;
	int n = 0;

	for_each_netdev(&init_net, dev) {
		struct net_device *conduit, *bound;
		struct dsa_port *dp;
		int phys_if, i;
		u16 vid;

		if (!dsa_user_dev_check(dev))
			continue;
		dp = dsa_port_from_netdev(dev);
		if (IS_ERR_OR_NULL(dp) || !dsa_nss_port_is_8021q(dp))
			continue;

		conduit = dp->cpu_dp->conduit;
		phys_if = nss_cmn_get_interface_number_by_dev(conduit);
		if (phys_if < 0 || phys_if >= NSS_MAX_PHYSICAL_INTERFACES)
			continue;	/* conduit not armed as a firmware port */

		if (dp->bridge && dp->bridge->tx_fwd_offload) {
			unsigned int vbid = dsa_port_bridge_num_get(dp);

			vid = dsa_tag_8021q_bridge_vid(vbid);
			/* the port imprecise RX delivers to, if any is live */
			bound = dsa_tag_8021q_find_user(conduit, -1, -1, vid, vbid);
			if (!bound)
				bound = dev;
		} else {
			vid = dsa_tag_8021q_standalone_vid(dp);
			bound = dev;
		}

		for (i = 0; i < n; i++)
			if (want[i].vid == vid && want[i].phys_if == phys_if)
				break;
		if (i < n)
			continue;	/* the bridge already has its entry */
		if (n == max) {
			pr_warn_once("qca-dsa-nss: more than %d nodes wanted, rest ignored\n", max);
			break;
		}
		want[n].vid = vid;
		want[n].phys_if = phys_if;
		want[n].ndev = bound;
		n++;
	}
	return n;
}

static bool dsa_nss_node_matches(const struct dsa_nss_node *n,
				 const struct dsa_nss_want *w)
{
	return n->if_num >= 0 && n->vid == w->vid && n->phys_if == w->phys_if &&
	       n->ndev == w->ndev && n->mtu == w->ndev->mtu &&
	       ether_addr_equal(n->mac, w->ndev->dev_addr);
}

/* returns true when something could not be allocated and a retry is due */
static bool dsa_nss_resync(void)
{
	struct dsa_nss_want want[DSA_NSS_MAX_NODES];
	bool again = false;
	int nwant, i, j;

	atomic_inc(&dsa_nss_resyncs);
	rtnl_lock();
	mutex_lock(&dsa_nss_lock);

	nwant = dsa_nss_collect(want, DSA_NSS_MAX_NODES);

	/* tear down what no longer matches */
	for (i = 0; i < DSA_NSS_MAX_NODES; i++) {
		struct dsa_nss_node *n = &dsa_nss_nodes[i];

		if (n->if_num < 0)
			continue;
		for (j = 0; j < nwant; j++)
			if (dsa_nss_node_matches(n, &want[j]))
				break;
		if (j == nwant)
			dsa_nss_node_destroy(n, "topology changed");
	}

	/* build what is missing */
	for (j = 0; j < nwant; j++) {
		struct dsa_nss_node *free = NULL;
		int ret;

		for (i = 0; i < DSA_NSS_MAX_NODES; i++) {
			struct dsa_nss_node *n = &dsa_nss_nodes[i];

			if (n->if_num >= 0) {
				if (dsa_nss_node_matches(n, &want[j]))
					break;
				continue;
			}
			if (!free)
				free = n;
		}
		if (i < DSA_NSS_MAX_NODES)
			continue;	/* already there */
		if (!free)
			break;
		ret = dsa_nss_node_create(free, want[j].vid, want[j].phys_if,
					  want[j].ndev);
		if (ret == -EAGAIN)
			again = true;
	}

	mutex_unlock(&dsa_nss_lock);
	rtnl_unlock();
	return again;
}

static void dsa_nss_work_fn(struct work_struct *work)
{
	if (dsa_nss_resync()) {
		if (dsa_nss_retries++ < DSA_NSS_MAX_RETRIES) {
			pr_info("qca-dsa-nss: no free firmware interface number, retry %u in %d ms\n",
				dsa_nss_retries, DSA_NSS_RETRY_MS);
			schedule_delayed_work(&dsa_nss_work,
					      msecs_to_jiffies(DSA_NSS_RETRY_MS));
		} else {
			pr_warn("qca-dsa-nss: giving up after %u retries; write 1 to debugfs 'resync' to try again\n",
				dsa_nss_retries);
		}
		return;
	}
	dsa_nss_retries = 0;
}

static void dsa_nss_schedule(void)
{
	dsa_nss_retries = 0;
	mod_delayed_work(system_wq, &dsa_nss_work,
			 msecs_to_jiffies(DSA_NSS_COALESCE_MS));
}

/*
 * ===== netdev events =====
 */

static int dsa_nss_netdev_event(struct notifier_block *nb, unsigned long event,
				void *ptr)
{
	struct net_device *dev = netdev_notifier_info_to_dev(ptr);
	int i;

	if (!dsa_user_dev_check(dev))
		return NOTIFY_DONE;

	switch (event) {
	case NETDEV_UNREGISTER:
		/*
		 * The port is going away now; release its node here, with
		 * rtnl held, rather than from the work item, so the netdev
		 * is not kept alive by our reference.
		 */
		mutex_lock(&dsa_nss_lock);
		for (i = 0; i < DSA_NSS_MAX_NODES; i++)
			if (dsa_nss_nodes[i].if_num >= 0 &&
			    dsa_nss_nodes[i].ndev == dev)
				dsa_nss_node_destroy(&dsa_nss_nodes[i],
						     "port unregistered");
		mutex_unlock(&dsa_nss_lock);
		fallthrough;
	case NETDEV_REGISTER:
	case NETDEV_UP:
	case NETDEV_DOWN:
	case NETDEV_CHANGE:
	case NETDEV_CHANGEUPPER:
	case NETDEV_CHANGEADDR:
	case NETDEV_CHANGEMTU:
		dsa_nss_schedule();
		break;
	default:
		break;
	}
	return NOTIFY_DONE;
}

static struct notifier_block dsa_nss_netdev_nb = {
	.notifier_call = dsa_nss_netdev_event,
};

/*
 * ===== debugfs =====
 */

static int dsa_nss_status_show(struct seq_file *m, void *v)
{
	int i, nodes = 0;

	mutex_lock(&dsa_nss_lock);
	for (i = 0; i < DSA_NSS_MAX_NODES; i++) {
		struct dsa_nss_node *n = &dsa_nss_nodes[i];

		if (n->if_num < 0)
			continue;
		seq_printf(m, "%-16s if_num=%d vid=%u phys_if=%d mtu=%u mac=%pM\n",
			   netdev_name(n->ndev), n->if_num, n->vid, n->phys_if,
			   n->mtu, n->mac);
		nodes++;
	}
	mutex_unlock(&dsa_nss_lock);
	seq_printf(m, "nodes=%d resyncs=%d creates=%d destroys=%d alloc_failures=%d retries_pending=%u\n",
		   nodes, atomic_read(&dsa_nss_resyncs),
		   atomic_read(&dsa_nss_creates), atomic_read(&dsa_nss_destroys),
		   atomic_read(&dsa_nss_alloc_failures), dsa_nss_retries);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(dsa_nss_status);

static ssize_t dsa_nss_resync_write(struct file *fp, const char __user *ubuf,
				    size_t sz, loff_t *ppos)
{
	dsa_nss_schedule();
	return sz;
}

static const struct file_operations dsa_nss_resync_fops = {
	.owner = THIS_MODULE,
	.write = dsa_nss_resync_write,
	.llseek = noop_llseek,
};

/*
 * ===== module =====
 */

static int __init qca_dsa_nss_init(void)
{
	int i, ret;

	for (i = 0; i < DSA_NSS_MAX_NODES; i++)
		dsa_nss_nodes[i].if_num = -1;
	INIT_DELAYED_WORK(&dsa_nss_work, dsa_nss_work_fn);

	ret = register_netdevice_notifier(&dsa_nss_netdev_nb);
	if (ret)
		return ret;

	dsa_nss_dentry = debugfs_create_dir("qca-dsa-nss", NULL);
	debugfs_create_file("status", 0444, dsa_nss_dentry, NULL,
			    &dsa_nss_status_fops);
	debugfs_create_file("resync", 0200, dsa_nss_dentry, NULL,
			    &dsa_nss_resync_fops);

	pr_info("qca-dsa-nss: firmware vlan nodes for qca-8021q DSA ports\n");
	dsa_nss_schedule();
	return 0;
}

static void __exit qca_dsa_nss_exit(void)
{
	int i;

	unregister_netdevice_notifier(&dsa_nss_netdev_nb);
	cancel_delayed_work_sync(&dsa_nss_work);
	debugfs_remove_recursive(dsa_nss_dentry);

	mutex_lock(&dsa_nss_lock);
	for (i = 0; i < DSA_NSS_MAX_NODES; i++)
		dsa_nss_node_destroy(&dsa_nss_nodes[i], "module unload");
	mutex_unlock(&dsa_nss_lock);
}

module_init(qca_dsa_nss_init);
module_exit(qca_dsa_nss_exit);

MODULE_LICENSE("GPL v2");
MODULE_DESCRIPTION("NSS firmware VLAN interfaces for qca-8021q DSA user ports");
