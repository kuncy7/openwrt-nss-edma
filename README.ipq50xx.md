# NSS offload on IPQ5018 with the upstream ethernet stack (branch `c3po-tag-8021q`)

This branch runs the **NSS packet-processing core of the IPQ5018** on kernel
**6.18** with OpenWrt main's **upstream `stmmac` / `dwmac-ipq5018` ethernet
driver** - no `qca-nss-dp`, no `qca-ssdk`. It is the IPQ50xx counterpart of the
[IPQ807x work](/README.md) this tree is layered on: same idea (keep the upstream
driver, attach the firmware through a small glue module), different silicon,
different glue.

Validated on the **GL.iNet GL-B3000** (IPQ5018 + QCA8337 + QCN6122):
routed NAT through the firmware at the ceiling of the single 1 GbE CPU port
(~900 Mbit/s measured, ~911 by arithmetic - see *The single CPU port ceiling*) with the CPU
**above 90 % idle** under load. **Wi-Fi runs on the NSS path too** since the
core-clock fix described below: both radios (internal 2.4 GHz + QCN6122),
734/447 Mbit/s through the router over 5 GHz.

> **Branch note.** `c3po-tag-8021q` is the branch, and the repository's
> default since 2026-09-17. It carries everything `ipq50xx-rebase` had (the
> series was rebased onto it on 2026-09-16, and PR #4 merged after) plus the
> `dsa` topology described under *Topology*. `ipq50xx-rebase` set out to
> answer one question - does the NSS core run on the IPQ5018 with the upstream
> ethernet driver - and it does; that branch is frozen at `29f3da694f` and
> takes no further changes or pull requests. `ipq50xx-nss` is the older
> archive. The feed branch keeps its name: use `ipq50xx-rebase` of
> `kuncy7/nss-packages` with this tree.

Discussion and test reports: the
[forum thread](https://forum.openwrt.org/t/ipq5018-nss-offload-on-kernel-6-18-with-the-upstream-ethernet-stack-gl-b3000/253014).

## What is in the branch

The branch sits on Julius's `nss-edma-rework`, merged with OpenWrt main (last
on 22 September 2026). These are the commits the work started from, in build
order; everything since - the `dsa` topology, the boards, the fixes - builds on
them:

| Commit | What |
|---|---|
| `qualcommax: ipq50xx: keep the CMN PLL bus clocks enabled on 6.18` | Without this, 6.18 does not boot on IPQ5018 at all - the SoC dies within milliseconds of the CMN PLL probe. Merged into openwrt/main as [86b584bd0994](https://github.com/openwrt/openwrt/commit/86b584bd09949f14231d373c46563cc9); the branch now takes it from main. |
| `qualcommax: stmmac: add a data-plane claim API for the NSS firmware` | Patch `0956`: lets a module take the data path of a GMAC away from stmmac (TX drained, NAPI off, DMA stopped, `ndo_start_xmit` redirected) and hand it back. phylink, MDIO and the netdev stay with the host. |
| `qualcommax: ipq5018: add the NSS core node and reserved memory` | `ipq5018-nss.dtsi`: the `nss0` node with its clocks and interrupts, plus the reserved-memory region for the firmware. |
| `package: add qca-dwmac-nss, the NSS data-plane glue for IPQ5018` | `kmod-qca-dwmac-nss`: the counterpart of `qca-ppe-nss` for this SoC. Implements the `nss-dp` API `qca-nss-drv` expects on top of the stmmac claim. Arms at runtime through debugfs, never at probe. |
| `package: add qca8337-nss, the switch fabric driver for the NSS trunk` | `kmod-qca8337-nss`: re-arms the QCA8337 as a plain 802.1Q fabric after `qca8k` is unbound, for the trunk topology (see *Why DSA has to go*). |
| `package: nss-tools: add the ipq50xx bring-up service` | `nss-tools-dwmac`: the `nss` service, the ordering that makes the arm work, the one-shot network-config migration. |
| `qualcommax: ipq5018: enable the NSS core on the GL-B3000` | One line in the board DTS: `#include "ipq5018-nss.dtsi"`. |
| `mac80211: ath11k: NSS fixes found while porting wifili to IPQ5018` | Six real bugs met on the way (REO register layout, ring topology, init flags, L2 update frame padding, non-cacheable rings, and one that matters with the offload *off*: a QCN6122 radio no longer disappears because `ath11k_nss_setup()` returns `-ENOTSUPP` for it). |
| `mac80211: ath11k: wifili investigation tooling (inert by default)` | Twenty-two module parameters and traces, all off by default. Kept for whoever repeats the Wi-Fi investigation. |
| `qualcommax: ipq50xx: debug aids for NSS bring-up work` | `MAGIC_SYSRQ_SERIAL`, `DEVMEM` with `STRICT_DEVMEM` off. **Revert this commit for a build meant to be deployed** - it is one commit precisely so that is easy. |

The companion feed is **[kuncy7/nss-packages](https://github.com/kuncy7/nss-packages/tree/ipq50xx-rebase)**,
branch `ipq50xx-rebase`: Julius's `nss-packages` (last synced on 21 September
2026) with the ipq50xx work on top - the 12.2 firmware line as a selectable
version, the per-target package split that lets the stack build on ipq50xx,
the ECM patch for DSA ports (`0046`), the 256 MB memory profile for the boards
that need it, and the driver fixes met during bring-up (core boot and clocks,
N2H bounds, offloaded-traffic counters, CPU-load reporting; see the feed
README).

## Releases and the kernel module repository

Ready images are on the [Releases page](https://github.com/kuncy7/openwrt-nss-edma/releases),
one release per build, tagged `ipq50xx-YYYY.MM.DD` (a second build on the same day
gets `-2`). The newest two are kept; older ones go away together with their
kernel module repository, see below.

**Sysupgrade images only.** Flash `openwrt-qualcommax-ipq50xx-<device>-squashfs-sysupgrade.bin`
over a running OpenWrt, from LuCI or with `sysupgrade`. There is no factory
image: a board that is not in official OpenWrt needs a plain OpenWrt image
built from this tree first (Quick start below), and a board that is gets the
official one. `sha256sums-<group>.txt` next to the images has the checksums.

Every image is built with the whole plane in: `nss-tools-dwmac`, firmware
12.2-156, VLAN and PPPoE managers, ath11k with the NSS patches, plus `ip-full`
and `iperf3` for checking it. Boards come in two groups, because the ath11k and
NSS memory profiles are a build-time choice for the whole image:

| group | boards | memory profile |
|---|---|---|
| `std` | 512 MB and 1 GB boards (the release notes list them) | ath11k 1G, NSS medium |
| `256m` | Cudy P5, TP-Link EX511 v2 | ath11k 256M, NSS low |

The exact configuration of each group is in `.github/ci/ipq50xx/` (`common.config`
+ `<group>.config` + `kmods-extra.config`) and, for a given release, in the
attached `config-<group>.buildinfo`.

### Kernel modules that are not in the image

Wireguard, tun, SQM/cake, USB storage and USB network adapters, extra
filesystems, GRE/VXLAN/L2TP, bonding, nft extras and the like are built as
packages, not into the image. The image already lists the repository they
live in, so on the router it is just:

```sh
apk update
apk add kmod-wireguard wireguard-tools luci-proto-wireguard
```

The kernel modules come from this build's own repository on GitHub Pages
(`https://kuncy7.github.io/openwrt-nss-edma/<tag>/<group>/`), everything else
from the regular OpenWrt snapshot feeds, which the image lists as well. The
full list of modules is `.github/ci/ipq50xx/kmods-extra.config`; if you need
one that is not there, open an issue or a pull request adding it to that
file, and it is in the next build.

Why a repository per build: a kernel module only installs on the kernel it
was built for - `apk` checks `kernel=<version>~<vermagic>` - and the kernel in
these images is not the official one, so the official `kmods` feed cannot
serve them (`apk` says "no such package"). After a sysupgrade to a newer
release, run `apk update` and install the modules again; they come from the
new release's repository, which the new image already lists. A repository
stays online as long as its release does.

## Quick start

```sh
git clone -b c3po-tag-8021q https://github.com/kuncy7/openwrt-nss-edma.git
cd openwrt-nss-edma

./scripts/feeds update -a && ./scripts/feeds install -a
./scripts/feeds list -r nss | grep -q qca-nss-drv && echo "nss feed OK"

make menuconfig
make -j$(nproc)
```

`feeds.conf.default` already names the companion feed (`nss`, branch
`ipq50xx-rebase`). A `feeds.conf` of your own takes precedence over it, so
if you keep one, it needs that same line. Any other NSS feed builds
`qca-nss-ecm` without `0046`, the patch that lets ECM send a flow through a
DSA port: Julius's `nss-packages` at the very same package version, the
archived `ipq50xx-nss` branch of ours at an older one. That image boots,
answers ping, and passes LAN<->LAN and Wi-Fi<->Wi-Fi traffic; every TCP flow
between a switch port (LAN or WAN) and anything the firmware carries stalls
the moment ECM accelerates it. The version string does not always tell them
apart; this does, on the router:

```sh
grep -c dsa_port_from_netdev /lib/modules/$(uname -r)/ecm.ko   # 1 = patched, 0 = not
```

**Select `nss-tools-dwmac` in menuconfig, as `<*>` and not `<M>`**
(Network -> nss-tools-dwmac, or `CONFIG_PACKAGE_nss-tools-dwmac=y`). It
pulls in every kmod the plane needs, and it is the package that actually
arms the firmware: without it the glue and the driver load, wait for each
other and nothing happens - `fw_mask` stays `0x0` and the log says
"deferring NSS core probe until a port is armed". `=m` builds the package
but leaves it out of the image, which looks exactly the same. Four people
have been caught by one or the other, so the one check worth doing on a
fresh image is:

```sh
ls /etc/rc.d | grep S19nss     # the arming service is installed
logread -e nss                 # ends with "NSS wired plane + ECM up"
```

Note the service installs as `/etc/init.d/nss`, not `nss-dwmac`.

### Rebuilding on top of an existing build directory

Selecting `nss-tools-dwmac` also switches off the boot-time autoload of
`ath11k`, `ath11k_ahb` and `ath11k_pci`: the `nss` service loads them itself,
after the plane is armed, because `ath11k_base` takes `nss.enabled` from the
module parameter at probe time and never retries. A radio probed before the
core is up stays on the host path for the rest of the boot, or wedges its Q6
outright.

That switch lives in a `make` conditional in `package/kernel/mac80211/ath.mk`,
and **OpenWrt does not rebuild a package because a conditional changed**. On a
tree that has already been built once - including one you only pulled new
commits into - the old package is reused and the change never reaches the
image. Three people have been caught by this. So after changing that symbol,
or after pulling:

```sh
make package/kernel/mac80211/clean
make -j$(nproc)
```

The check on the running board is one line - these files must **not** exist:

```sh
ls /etc/modules.d/ath11k*
```

If they do, the gate is not in your image, and the log will show
`nss state in default init state` / `NSS SOC Initialization Failed :-22`
seconds *before* `nss core 0 booted successfully`.

The `.config` the validated image was built from, reduced to what matters
(everything the packages depend on is pulled in by `nss-tools-dwmac`):

```
CONFIG_TARGET_qualcommax=y
CONFIG_TARGET_qualcommax_ipq50xx=y
CONFIG_TARGET_qualcommax_ipq50xx_DEVICE_glinet_gl-b3000=y

# the runtime; depends on kmod-qca-dwmac-nss, kmod-qca-nss-drv,
# kmod-qca-nss-ecm, kmod-qca-nss-drv-vlan-mgr, kmod-qca8337-nss
CONFIG_PACKAGE_nss-tools-dwmac=y

# firmware: 12.2-156, the one validated on IPQ5018 (see below)
CONFIG_PACKAGE_nss-firmware=y
CONFIG_NSS_FIRMWARE_VERSION_12_2=y
CONFIG_NSS_MEM_PROFILE_MEDIUM=y

# qca-nss-drv features the plane uses
CONFIG_NSS_DRV_VLAN_ENABLE=y
CONFIG_NSS_DRV_IPV6_ENABLE=y
CONFIG_NSS_DRV_VIRT_IF_ENABLE=y

# PPPoE: the kernel side builds, but ECM only accelerates PPPoE when this
# connection manager is present - without it flows are tracked and never
# offloaded, with nothing in the log to say so.
CONFIG_PACKAGE_kmod-ppp=y
CONFIG_PACKAGE_kmod-pppoe=y
CONFIG_PACKAGE_kmod-qca-nss-drv-pppoe=y

# ath11k with the NSS patches applied. Needed for the Wi-Fi offload, and
# the validated combination for the wired plane as well.
CONFIG_PACKAGE_MAC80211_NSS_SUPPORT=y
CONFIG_ATH11K_NSS_SUPPORT=y
CONFIG_NSS_DRV_WIFIOFFLOAD_ENABLE=y
CONFIG_NSS_DRV_WIFI_EXT_VDEV_ENABLE=y
```

The two boards here with a USB socket - the TP-Link Archer AX55 v1 and the
Linksys MR5500 - also carry the storage half of USB in their
`DEVICE_PACKAGES` (`kmod-usb-storage`, `-uas`, vfat/exfat/ntfs3 with their
code pages, `block-mount`, `usbutils`). The target's defaults only bring the
host controller, which leaves an image where a stick enumerates and nothing
on it can be read.

Do **not** override the ath11k firmware with files from a stock image. The
package's `WLAN.HK.2.7.0.1` is the one that runs stably here; the stock
`2.9.r4` blob makes the internal 2.4 GHz radio's Q6 assert (`PHY0M3`) about
thirteen minutes after the BSS comes up, on this driver, every time.

### Firmware: why 12.2-156

`NSS_FIRMWARE_VERSION_12_2` selects `NSS.FW.12.2-156-MP.R` from the same
tarball the feed already uses. It is the newest firmware published for IPQ5018
(May 2025, nine months after 12.5) and the one every measurement in this file
was taken on. The feed defaults to 12.5 on every target, so pick 12.2 by hand.

- **12.5-210-MP** runs too: on a GL-B3000 (23 September 2026) it brought both
  radios up on the offload, carried routed and Wi-Fi<->LAN TCP through the
  firmware and came through a cold boot. It ignores the host's TX
  checksum-generation flags, though - with the glue's `fw_csum` on, ICMP works
  and every TCP handshake leaves the wire with a bad checksum - so `fw_csum`
  defaults to off; leave it. An earlier note here that 12.5 refuses VAP
  allocation did not hold up.
- **11.4-6** refuses VAP allocation.

## How the plane comes up

A plain reboot is stock OpenWrt on the host stack. The `nss` service
(`START=19`, before netifd) then does, in this order:

1. **Makes the CPU port speak plain 802.1Q.** On the `dsa` topology (the
   default, see *Topology*) the switch keeps its DSA driver and the service
   switches the conduit's tag protocol to the switch's tag_8021q tagger
   (`qca-8021q`, `rtl8365mb-8021q`). On the trunk topology `qca8k` has done
   the hard bring-up (SerDes, clocks, uniphy) at boot; the service unbinds it
   and loads `qca8337-nss` with the VTU map from `nss.general.vtu`.
2. Loads `qca-dwmac-nss` and `qca-nss-drv`. Both are inert at this point:
   `qca-nss-drv`'s probe defers until a port is armed.
3. With `nss.general.wifi_offload=0`, loads ath11k on the host path. With the
   default `1` it leaves ath11k to `nss-dwmac-up`, which loads it after the arm.
4. Starts `/usr/sbin/nss-dwmac-up` alongside netifd. That script waits until
   the trunk - the DSA conduit on `dsa` - is up and the `lan` interface is up
   *in netifd's own view*, on the trunk topology also until the bridge has set
   the promiscuous flag on the trunk; then it arms the firmware (`fw_mask` in
   debugfs), waits for the port to show `started`, loads `qca-nss-vlan` and,
   on `dsa`, `qca-dsa-nss`, which gives the DSA ports their firmware VLAN
   interfaces; last, ECM with `front_end_selection=1` and
   `accel_delay_pkts=1`.

Three orderings that fail *silently* - each measured, each cost days - are the
reason for the waiting:

- **arming on a down interface**: the firmware starts, TX works, and not one
  ingress frame is ever delivered;
- **arming while netifd is still applying config**: netifd bounces the
  interface mid-takeover, and the promiscuous flag the bridge sets afterwards
  never reaches the hardware (the RX filter only reaches the GMAC while the
  host still owns it);
- **loading `qca-nss-vlan` before the arm**: the VLAN manager resolves the
  trunk's NSS interface number at `NETDEV_REGISTER` time; before the arm that
  number does not exist, registration fails silently (the driver's debug
  macros are compiled out at the default log level), and every tagged frame
  disappears. Loaded *after* the arm, its notifier replays `NETDEV_REGISTER`
  for the existing `eth0.<vid>` netdevs, so ordering against netifd stops
  mattering.

If firmware takeover fails after successful switch setup, the box keeps running
on the host path **with the same topology**. A switch setup failure on the
trunk topology is different:
`qca8337-nss` attempts to block the wired ports to protect VLAN isolation, and the
service loads host Wi-Fi for recovery. Remote access then depends on an already
configured Wi-Fi connection; a freshly flashed device without that fallback may
be unreachable over the network and need local recovery. This blocked state does
not mean the router is bricked. Check `dmesg` for the switch setup error and correct
the board/VLAN settings before retrying.

### Topology

**`nss.general.topology='dsa'`** (C-3PO), the default on this branch, keeps
the switch driver bound and the ports as they are on a stock image -
`lan1`/`lan2`/`wan`, `br-lan` on the lan ports, `wan` (or `wan.35` for a
PPPoE ISP) as the WAN device. The service
switches the conduit's tag protocol to the switch's tag_8021q tagger before
netifd runs - `qca-8021q` for `qca8k` (QCA8337), `rtl8365mb-8021q` for
`rtl8365mb` (RTL8367S) - so the switch talks to the CPU in plain 802.1Q,
which the firmware parses, and after the arm `qca-dsa-nss` gives every port,
bridge and 802.1Q upper of a port a firmware VLAN interface so ECM can write
rules for them.

A board whose switch is driven by `qca8k` or `rtl8365mb` gets the `dsa`
topology on first boot, and **nothing about the wiring is configured**: the
switch stays with its driver, so ports, CPU port and VLANs are the kernel's,
and the rest is read off the board at every boot - the GMACs from the nodes
their netdevs sit on (`ethernet@39c00000` = GMAC0 = `phys_if 0`,
`ethernet@39d00000` = GMAC1 = `phys_if 1`), the trunk as the conduit DSA hangs
the user ports off, and `fw_mask` from what netifd brought up: the conduit
plus any GMAC that is not a conduit and is up (a WAN PHY of its own), never a
second CPU port DSA does not use or a GMAC nothing configured.
`nss-dwmac-probe` prints that view; `trunk`, `trunk_if`, `extra_ports` and
`fw_mask` in uci still override it if a board needs that. One thing first boot
does change: a board table that splits the DSA ports across both CPU links
(the Redmi AX5400, the Cudy P5 and the CMCC PZ-L8 put `lan1`-`lan3` on `eth1`
and `wan` on `eth0`) is consolidated onto the link that carries most of them,
because only the armed conduit's ports get a firmware VLAN interface - and on
the AX5400 the odd link is GMAC0 into switch port 5, whose RX is dead in
mainline as well (openwrt#24696). The split is read where `board.d` puts it,
`/etc/board.json` (`network_device.<port>.conduit`, applied by netifd - there
is no `config device` section for it in `/etc/config/network`), with any such
section LuCI or an admin added taking precedence; the moved port gets its
conduit in the section that names it, or in a new one. A port left on the
other link is not an error, it just stays on the host path; the service says
which ports and where in the log at boot. An even split is left alone: nothing
on the board says which link works.

A board of these three set up on the `dsa` topology before 21 September 2026
kept the split - the first-boot script read only `/etc/config/network` then,
found no conduit there and moved nothing, so `wan` stayed on `eth0` and off
the firmware. First boot does not run again on its own; the fix by hand is
what the script now writes (if `uci show network | grep "name='wan'"` already
finds a `config device` for `wan` - a MAC override, say - set the conduit in
that one instead; two sections for one device do not mix):

```
uci add network device
uci set network.@device[-1].name='wan'
uci set network.@device[-1].conduit='eth1'
uci commit network
reboot
```
The VTU and the `qca8337-nss` parameters have no meaning here. A tagged ISP VLAN or a VLAN
for an SSID is plain netifd (`wan.35`, `lan1.10`): the kernel installs it in
the switch. To try it on a board that migrated to the trunk on another image:
`uci set nss.general.topology='dsa'`, put the network config back on the DSA
ports, reboot. Measured on the GL-B3000 against the trunk topology:
the same plane (5 GHz → NAT → WAN 597/570/631 up, 650/642/629 down Mbit/s
at 1-6 % CPU), and PPPoE over `wan.35` accelerated (see below). On the
TP-Link Archer AX55 v1 (RTL8367S), with nothing in uci but the defaults and
`wifi_offload`: 5 GHz → NAT → WAN 532/502/457 up, 582/693/597 down Mbit/s at
4-6 % CPU. Wi-Fi offload is on by default here as well, also where the DTS
asks for `qcom,ath11k-fw-memory-mode = <1>` (the AX55, the Xiaomi AX6000 and
others): wifili runs in that mode - measured on the AX55 and on two AX6000s -
and the service only logs a warning; `nss.general.wifi_offload=0` keeps the
radios on the host. Not yet: VLAN-aware bridges (refused by both taggers) and
switches other than these two.

**The trunk (`vlan-trunk`, `lan-trunk`)** stays supported next to it. A board
with no such switch gets it on first boot from the board table (see
*Porting*), and a board that migrated on an earlier image keeps it: `eth0` is
the switch trunk, netifd builds `eth0.1` (lan) and `eth0.2` (wan) on it. That
migration rewrote a DSA-style network config once (`br-lan` ports → `eth0.1`,
`wan`/`wan6` device → `eth0.2`) and left a marker
(`nss.general.topology='vlan-trunk'`) so it never runs again. The VTU and the
`qca8337-nss` parameters belong to this topology alone; see *Why DSA has to
go*.

### `uci` knobs (`/etc/config/nss`, section `general`)

On the `dsa` topology only the first two matter (`fw_logbuf` has its default);
`fw_mask`, `trunk`, `trunk_if` and `extra_ports` are read off the board and
the uci values, if set, override that. The switch rows are for the trunk
topology alone.

| Option | Default | Meaning |
|---|---|---|
| `enabled` | `1` | `0` = stay on the host stack (same topology, no firmware) |
| `wifi_offload` | `1` | load ath11k with `nss_offload=1`, after the arm. Needs ath11k built with NSS support (the service warns if it is not). `0` = Wi-Fi on the host path. |
| `fw_mask` | `0x2` | bitmask of GMACs to hand to the firmware; bit N = GMAC N, and every bit needs a netdev (`trunk` / `extra_ports`). `0x3` arms both; two GMACs have run on the Linksys SPNMX56 and MX6200, the Xunison D50 and the Redmi AX5400 (see *Porting*). |
| `vtu` | *(B3000 wiring on the B3000, empty elsewhere)* | VTU program for `qca8337-nss`; empty = VTU off, the switch stays one untagged LAN. Needed when WAN shares the trunk, and for every further VLAN - an ISP's tagged VLAN, a VLAN per SSID - which the switch drops unless it is listed (examples in *Porting*) |
| `trunk` | `eth0` | the switch trunk netdev. `eth1` on a board whose GMAC0 has a netdev of its own - a WAN PHY (Xunison D50, Zyxel SCR50AXE) or a second link into the switch (Redmi AX5400, CMCC PZ-L8); `lan` on the MX6200, which has no switch. |
| `trunk_if` | `1` | which GMAC the trunk is = its NSS phys_if. `0` where the switch hangs off GMAC0 (SPNMX56, AX6000) or there is no switch (MX6200). |
| `extra_ports` | *(empty)* | other GMACs in use, as `<if>:<netdev>` entries - the D50's ethernet WAN is `0:wan`, the SPNMX56's 2.5G PHY `1:wan`, the Redmi AX5400's second link into the switch `0:eth0` (WAN on `eth0.2`, LAN on `eth1.1`). Named here, armed by `fw_mask`. |
| `fabric` | `qca8337` | `none` on a board with no switch: skips the qca8k unbind and the fabric module. |
| `switch_dev` | `90000.mdio-1:11` | the switch's MDIO device, unbound from `qca8k` before the re-arm. `90000.mdio-1:18` on the I-O DATA WN-DAX3000GR and the Elecom WRC-X3000GS2 / GST2. |
| `switch_args` | *(empty)* | further `qca8337-nss` parameters, passed verbatim (`cpu_port=`, `ports=`, `wake_phys=`, `bus_via=`) |
| `meminfo` | *(empty; GMAC1's rings in SDRAM on the Redmi AX5400, MR5500 and AX6000)* | where the firmware keeps the GMAC descriptor rings, written to `qca-nss-drv`'s `meminfo_user_config` before the core boots, e.g. `<0, gmac_tx_desc_1, SDRAM>, <0, gmac_rx_desc_1, SDRAM>`. With GMAC1's rings in the default `UTCM_SHARED` the port never starts on those three boards (`rs=0 ts=0`, `rx_fw=0`) - worth trying on any other board with that symptom. `default` keeps the firmware's placement on them. `grep gmac /sys/kernel/debug/qca-nss-drv/meminfo/core0` shows where they ended up. |
| `fw_logbuf` | `256` | firmware log ring size, read at `/sys/kernel/debug/qca-nss-drv/logs` |

There is no runtime detach. `/etc/init.d/nss stop` prints how to disable the
plane across reboots; returning to the host path needs a reboot.

### Checking that it works

```sh
cat /sys/kernel/debug/qca-dwmac-nss/status        # phys_if 1: started dev=eth0 ...
logread -e nss                                    # "NSS wired plane + ECM up (Wi-Fi on the NSS path)"
cat /sys/kernel/debug/ecm/ecm_nss_ipv4/tcp_accelerated_count   # > 0 under traffic
grep -m1 ipv4_rx_pkts /sys/kernel/debug/qca-nss-drv/stats/ipv4  # climbing under traffic
```

Two things that mislead: on the trunk topology the `eth0.1` / `eth0.2`
**counters do not see accelerated traffic** - they show the first packet of
each flow and then stop (on `dsa` the port counters come from the switch's
MIB and do) - and `ipv4_rx_byts` in the firmware stats counts **both directions** of a
flow, so read it as roughly double the useful throughput. `top` is the honest
gauge: idle stays above 90 % under a full-rate flow, softirq stays flat.

## Why DSA has to go

The port model on this SoC is the reverse of ipq807x: the NSS `phys_if` is the
GMAC, and the netdev is the switch trunk. The firmware parses 802.1Q natively
(dynamic interface type 17) but cannot parse the two-byte Atheros header that
DSA's `tag_qca` puts where the ethertype should be; and DSA user ports never
get an NSS interface number, so ECM would not try to accelerate them anyway.
On IPQ5018, **DSA user ports and ECM acceleration are mutually exclusive** -
with the Atheros header. They are not with the `qca-8021q` tagger, which puts
the source port in a VLAN tag instead; that is the `dsa` topology above, the
default, and this section describes the trunk topology that stays supported
next to it.

If you are seeing `eth_rx_unknown_l3_protocol` counting most of your frames,
with `iface_count=0` and `accelerated_count=0`, on a QCA8337 board: this is
why, and no ECM patch will fix it.

What works instead: let `qca8k` do the bring-up, unbind it, and program the
fabric directly - MACs on, Atheros header off, VLANs in the VTU with the front
ports untagged in their VLAN and the CPU port carrying all of them tagged.
`qca8337-nss` does exactly that over raw paged MDIO (the protocol is copied from
`qca8k-8xxx.c`). It is not a probe-based driver: nothing binds automatically,
it reaches the switch through *named* MDIO devices given as parameters.

| Parameter | Default | Meaning |
|---|---|---|
| `bus_via` | `90000.mdio-1:01` | any MDIO device on the switch's bus, used to find the bus |
| `wake_phys` | `90000.mdio-1:00,…:01,…:02` | front-panel PHYs to power back up (qca8k's teardown leaves them in BMCR power-down) |
| `vlans` | *(empty = off)* | VTU program, `<vid>:<port><t|u>[,…][;…]`; a port listed `u` also gets the vid as PVID |
| `cpu_port` | `0` | the switch port wired to the SoC GMAC (forced 1G full duplex); `255` = none, for boards whose SoC link enters the switch through one of its PHYs and autonegotiates (AX6000, SPNMX56) |
| `ports` | `0x0f` | bitmask of switch ports to enable, CPU port included |
| `switch_fixup` | `1` | the MAC/header/flooding re-arm; `0` = only wake the PHYs |

Load it with `insmod`, not `modprobe`: kmodloader's `modprobe` drops module
parameters. The service does this for you.

## Porting to another IPQ5018 board

The board side is small. The parts, in order of effort:

1. **DTS**: add `#include "ipq5018-nss.dtsi"` to the board file. That is the
   entire diff between the B3000 DTS and a plain board, and without it there
   is no `nss@40000000` node, so `qca-nss-drv` never probes and nothing in
   this branch works - quietly (reported by @Pe3ucTop on an AX6000). **Every
   IPQ5018 board DTS in this branch already carries the line**; a board you
   add yourself needs it. It costs no memory: the 16 MB `nss_region`
   reservation sits in `ipq5018.dtsi` for every board either way. (On a
   256 MB board it does cost - 16 MB off `MemTotal` - and stock firmware
   reserves 8 MiB there; see *256 MB boards* below.)
2. **Which GMAC feeds what.** phys_if N is GMAC N. On the B3000 the switch is
   on GMAC1 (`fw_mask=0x2`, `trunk=eth0`, `trunk_if=1`) and GMAC0 is unused.
   The glue takes a map of netdevs per phys_if (`ifmap=1:eth0,0:wan`, or the
   same string into `/sys/kernel/debug/qca-dwmac-nss/ifmap`) and arms
   whichever bits `fw_mask` names, so a board with two GMACs in use - the
   switch on one and a WAN PHY on the other - can hand both to the firmware:
   `trunk_if` for the trunk's GMAC, `extra_ports='<if>:<netdev>'` for the
   other, `fw_mask=0x3`. Two GMACs, and a trunk on GMAC0, have run on the
   Linksys SPNMX56 and MX6200 and the Xunison D50.
   Every IPQ5018 board in `02_network` has an entry in the table
   (`nss-dwmac.defaults`, keyed on `board_name`, started by George
   Moussalem). Run on the board: GL-B3000, Linksys MX2000, SPNMX56 and
   MX6200, Xunison D50, CMCC MR3000D-CI (wired plane and 5 GHz offload on
   the table entry as written), Redmi AX5400 (both CPU links: LAN on
   `eth1.1`, WAN on `eth0.2`, Wi-Fi on the host), TP-Link EX511 v2
   (RTL8367D, both radios, see the switch note below), Xiaomi AX6000.
   Straight from the DTS, untested on the trunk: Linksys MX5500 and MR5500,
   Zyxel SCR50AXE, CMCC PZ-L8, I-O DATA WN-DAX3000GR, Elecom WRC-X3000GS2 /
   GST2, Yuncore AX830 and AX850. On those the settings apply themselves on
   first boot. Anything else logs a line telling you to set them by hand.

   The `dsa` topology needs no table entry. Besides the GL-B3000 it has run
   at testers' on the Linksys MX2000 and MR5500, the Xiaomi AX6000, the
   Zyxel SCR50AXE, the Redmi AX5400, the Cudy P5 and the TP-Link Archer AX55
   v1.

   The table applies once, on a config that has no `nss.general.topology`
   yet; a sysupgrade that keeps settings keeps the old layout. An AX5400
   already on the single-link layout (WAN on `eth1.2`) moves by hand:
   `fw_mask=0x3`, `extra_ports='0:eth0'`, `switch_args` and `vtu` as in
   the table, `network.wan.device` / `wan6.device` = `eth0.2`, reboot.

3. **The switch.** Find your MDIO device names with
   `ls /sys/bus/mdio_bus/devices/` and set `bus_via` / `wake_phys` from them;
   read the port wiring off the `ethernet-switch` node in your DTS and write
   `cpu_port`, `ports` and the `vlans` map from it. All of it goes into uci -
   `nss.general.vtu` for the VTU map, `nss.general.switch_args` for the rest,
   `nss.general.switch_dev` if the switch is not at `90000.mdio-1:11`.

   Two worked examples straight from the DTS files in this tree (untested on
   the boards themselves - the defaults are the only wiring validated here):

   | Board | CPU port | wan | lan | `switch_args` | `vtu` |
   |---|:---:|---|---|---|---|
   | GL-B3000 (default) | 0 | port 1 / PHY 0 | ports 2-3 / PHY 1-2 | *(none)* | `1:0t,2u,3u;2:0t,1u` |
   | Linksys MX2000 | 6 | port 2 / PHY 1 | ports 3-5 / PHY 2-4 | `cpu_port=6 ports=0x7c wake_phys=90000.mdio-1:01,90000.mdio-1:02,90000.mdio-1:03,90000.mdio-1:04` | `1:6t,3u,4u,5u;2:6t,2u` |
   | Linksys MR5500 | 6 | port 5 / PHY 4 | ports 1-4 / PHY 0-3 | `cpu_port=6 ports=0x7e wake_phys=90000.mdio-1:00,90000.mdio-1:01,90000.mdio-1:02,90000.mdio-1:03,90000.mdio-1:04` | `1:6t,1u,2u,3u,4u;2:6t,5u` |

   A board whose CPU port is not 0 was the one thing the fabric re-arm could
   not do until the `cpu_port` / `ports` parameters; with them the module
   carries no board assumption of its own any more.

   **More VLANs than lan and wan.** The switch carries only the VLANs listed
   in `vtu`; a VLAN missing there is dropped inside the switch, with nothing
   on the host. Each VLAN needs the CPU port tagged plus the ports it rides
   on, and the numbers are switch ports, not panel labels. Netifd then uses
   `<trunk>.<vid>` like any 802.1Q device. The module reads the map at boot,
   so reboot after changing it. Two layouts that run:

   | Use | Board | `vtu` | Netifd |
   |---|---|---|---|
   | ISP hands PPPoE over tagged VLAN 35 on the WAN jack | Linksys MX2000 (LS3434) | `1:6t,3u,4u,5u;35:6t,2t` | `eth0.35` as the PPPoE device |
   | Dumb AP: lan3 is a tagged uplink with VLANs 10-13 and 40; lan1, lan2 and the WAN jack are untagged ports in VLAN 10 | CMCC MR3000D-CI (csharper2005) - ports 1-3 = lan3/lan2/lan1, 4 = wan, 6 = CPU | `10:6t,1t,2u,3u,4u;11:6t,1t;12:6t,1t;13:6t,1t;40:6t,1t` | `eth1.10` … `eth1.40` in their bridges |

   **A Realtek switch (RTL8367D/S) takes a different route.** `qca8337-nss`
   does not apply, and the unbind trick does not work either: the RTL8367D's
   CPU port is a phylink-managed SGMII PCS, so unbinding the driver takes
   the link down and the fabric goes quiet however correctly it was
   programmed. On the TP-Link EX511 v2 the switch stays on its DSA driver
   (`rtl8365mb`, with the RTL8367D family patch in this branch) and the CPU
   tag is turned off instead: `realtek,headerless-cpu-port` in the switch
   node selects `DSA_TAG_PROTO_NONE` and clears `CPU_CTRL_EN`, so `eth0`
   carries plain Ethernet the firmware can parse. The table entry is
   `fabric='none'`, `wan_on_trunk=0`, no VTU, and `keep_ports='wan lan1
   lan2 lan3 lan4'`: without a tagger the DSA user ports cannot receive, so
   they are not bridge members, but they must stay up - `dsa_port_disable()`
   writes `BR_STATE_DISABLED` into the switch, and a down port is dead in
   the fabric, not just a dark PHY - and with address learning enabled on
   them, since the driver only turns learning on at bridge join, which
   these ports never see. The result is one flat untagged domain across
   all five sockets (switched, not a hub): the WAN socket is a fifth LAN
   port - the first-boot script leaves `wan` with no protocol and removes
   `wan6` - and `bridge-vlan` does not work (it moves dead DSA ports into
   `br-lan` and drops `eth0` out of it, which cuts every connection
   including SSH). VLANs on a Realtek switch under this plane are an open
   item on this route. The Archer AX55 v1 has the same switch and takes
   the other one: unbind `rtl8365mb` and re-arm the fabric from a module,
   which does give it VLANs (gabonpivovich-web, forum #306); on the EX511
   that left the fabric quiet. On either route the table entry is
   `fabric='none'`.

Also: a board whose WAN is on the internal GE PHY (GMAC0), like the D50, needs
no VTU at all - the switch only carries LANs, `vlans` can stay empty - and the
migration then puts `br-lan` on the untagged trunk and leaves `wan` alone
(`wan_on_trunk=0` in the table). Whether that WAN gets accelerated is
`fw_mask`: `0x2` keeps it on the host, `0x3` arms it as phys_if 0. Keep it at
`0x3`: on the D50 with `0x2`, ECM created no rule for LAN<->WAN at all and the
CPU sat at 90-100 %, against 4 % with both armed. After changing it, read
`status`: both ports should show `started` with `rs=3 ts=6`.

## What is accelerated

Legend as in the [IPQ807x README](/README.md): ✅ offloaded & validated ·
🟨 in code, not validated here · ⬜ not carried · ❌ not available.

| Feature | IPQ5018 | Notes |
|---|:---:|---|
| IPv4 NAT / routing | ✅ | ECM; ~900 Mbit/s at the single-CPU-port ceiling, host >90 % idle |
| IPv6 routing | 🟨 | built (`NSS_DRV_IPV6_ENABLE`), not measured |
| 802.1Q VLAN | ✅ | the trunk itself; `qca-nss-vlan` |
| L2 between LAN ports | ✅ | in the switch fabric (same VLAN), never reaches the SoC |
| PPPoE | ✅ | Kernel patch `0961` gained the lockless `__ppp_hold_channels()` / `__ppp_is_multilink()` that ECM's deadlock fix needs, `kmod-qca-nss-drv-pppoe` is selected, and `nss-dwmac-up` **loads it** after the arm - without the manager in memory ECM tracks the PPPoE flows, marks every rule invalid and the WAN silently stays on the host path (measured: 0 rules, 22k exceptions in 20 s; the same silent failure AugustoAmaral hit before the package was selected at all, ~950 Mbit/s at 84-95 % idle on an AX6000 once it was in). Measured here on the `dsa` topology with the ISP's VLAN on the WAN port (`wan.35`, PPPoE server on the bench): rules created, 120k-157k firmware hits per 15-20 s, 0-65 exceptions, 2 % CPU, at the 100 Mbit/s ceiling of the bench client. On the trunk topology LS3434 runs it as `eth0.35` with `vtu='...;35:6t,2t'` (#154, #156): 890-950 down / 310 up at 1-5 % CPU. |
| Wi-Fi (wifili) | ✅ | both radios; 734/447 Mbit/s over 5 GHz through the router, host ~90 % idle. Needs the core-clock fix - see below |
| SQM / NSS qdiscs | ⬜ | not carried for ipq50xx |
| Multicast snooping (`qca-mcs`) | ⬜ | not carried for ipq50xx |
| MAP-T / DS-Lite | 🟨 | `kmod-nat46` staging from the base; untested here |

### The single CPU port ceiling

Every routed flow crosses the trunk twice - in on one VLAN, out on the other -
and the data of one direction shares the wire with the ACKs of the other. With
1500-byte MTU that is `2×1448 / (2×1542 + 94) ≈ 91 %` of 1 GbE: **~911 Mbit/s**
is the most a single-CPU-port board can route, however idle the CPU is. That
matches the measurements, and it is a property of the board layout, not of the
offload.

### Wi-Fi

Both radios run the firmware's data path, with ath11k on the host doing
management only. It is on by default (`nss.general.wifi_offload=1`, `0` keeps
the radios on the host); the service loads ath11k after the arm, because
`ath11k_nss_setup()` checks the NSS core state at module load and never
retries.

What used to make this impossible was **not** a firmware bug, contrary to what
this file said for weeks. It was `gcc_ubi0_core_clk`: the bootloader leaves the
branch running, Linux has no consumer for it until `qca-nss-drv` probes ~25 s
into boot, so `clk_disable_unused()` gates it at ~2 s and the UBI32 core is
released from reset unclocked. It boots and answers, and the first WPA2 client
then takes the offload down. Kernel patch `0192` flags the branch
`CLK_IGNORE_UNUSED`; measured on cold boots, 11 lives / 0 deaths with the
branch kept against 0 / 3 with it gated. The earlier trap analysis (`0x40004918`,
`PEER_UPDATE_AUTH_FLAG` ordering) was a dead end and is withdrawn.

Two things learned on the way, in case they save someone else the time: results
from warm reboots mean nothing here - one variant lived 5/0 across `reboot` and
died on its first cold boot - and an empty WIFILI section in `nss_stats` was our
own N2H bounds check dropping every SOC statistics message, because those are
larger (2092 B) than the data frame size the host advertises (2048 B).

## 256 MB boards

Two IPQ5018 boards on this branch have 256 MB - the TP-Link EX511 v2
(IPQ5018 + QCN6122) and the Cudy P5 - and the rest 512 MB. The defaults
tuned for 512 MB do not fit in 256: on the EX511 the first flashed build
OOM-killed the AP daemon on a single iperf3 run. What it needed, all in the branch and measured on the board
(2026-09-13) - and what the next 256 MB board will need too:

- **`nss_region` 8 MiB** in the board DTS, overriding the 16 MiB in
  `ipq5018.dtsi`. Stock reserves 8 MiB for the same MP firmware family; the
  driver reports whatever is there as `heap_ddr_size` and the core boots
  and runs at full throughput in it. 8 MiB back on `MemTotal`.
- **`qca-nss-pbuf.init` 256MB profile = QSDK's MP_256 values on an
  IPQ5018** (`extra_pbuf_core0=800000 n2h_high_water_core0=16336
  n2h_wifi_pool_buf=0`, gated on `qcom,ipq5018` in the device tree's
  compatible list). The profile shipped before was QSDK's IPQ807x
  `ap-ac02` one with two digits transposed; its 4096-buffer Wi-Fi pool is
  what produced the OOM. The script ships with `kmod-ath11k` on every
  qualcommax target and picks the profile from `MemTotal` alone, so a
  256 MB IPQ807x or IPQ60xx board keeps those earlier values. The counts
  matter more than they look: every payload the firmware holds is
  `alloc_skb(1984 + 64)` on the host, and on 64-bit that is 2368 bytes
  with `skb_shared_info`, which kmalloc rounds up to 4096 - twice what
  the 32-bit stock kernel pays per buffer. Watch `drv_nss_skb_count` in
  `/sys/kernel/debug/qca-nss-drv/stats/drv` (~3250 idle, ~4500 after a
  load run), not `Slab`.
- **`coherent_pool=512K`** in the board DTS instead of the 2M every other
  board passes - only safe together with patch `0828` below. 2M costs
  three pools of 2 MiB, one of which doubles itself: 8 MiB.
- **Memory profiles.** `NSS_MEM_PROFILE_LOW` (anything else hands the
  firmware an 8192-entry empty-buffer pool: 35 MB of kmalloc-4k before the
  first packet) and `ATH11K_MEM_PROFILE_256M`. The 256M choice used to be a
  dead symbol - nothing in the ath11k patches branched on it, so it gave
  the *largest* rings; it now means the 512M profile plus smaller RXDMA
  rings (`DP_RXDMA_BUF_RING_SIZE` 512, which also sizes the NSS Wi-Fi RX
  descriptor pool), and 512M/1G builds are untouched by that. Both Kconfig
  choices default to these values in a single-device build of a board named
  in their conditions, so no menuconfig visit is needed there. Two things
  about that are easy to get wrong, and both cost real memory silently:

  - The default keys on the **profile** symbol
    (`CONFIG_TARGET_qualcommax_ipq50xx_DEVICE_tplink_ex511-v2=y`), not on
    the per-device checkbox of a multi-device image
    (`CONFIG_TARGET_DEVICE_...`). A multi-device image shares one profile
    across all its boards, so the defaults deliberately leave it alone: an
    image that includes a 256 MB board next to 512 MB ones has to pick LOW
    / 256M itself, and its other members then pay for it in Wi-Fi RX
    descriptors and connection-table size.
  - `ATH11K_MEM_PROFILE_256M` lives in `package/kernel/mac80211/ath.mk`,
    which the package Makefile pulls in with `include`. The metadata scan
    keys on the package's own `Makefile`, so editing `ath.mk` does **not**
    invalidate `tmp/info/.packageinfo-kernel_mac80211`: `make defconfig`
    then rebuilds `tmp/.packageinfo` from the stale cache, the new
    `default ... if ...` line never reaches Kconfig, and the old profile is
    selected with nothing in the output to say so. `rm -rf tmp/info` before
    `make prepare-tmpinfo` after touching any included `.mk`, and check
    that the cache file is newer than the file you edited.
    `NSS_MEM_PROFILE_LOW` is not in this tree at all - it is a choice in
    the `qca-nss-drv` package of the feed, so a board has to be named in
    both places - the EX511 v2 and the Cudy P5 are, in both. The firmware
    version
  (`NSS_FIRMWARE_VERSION_12_2`) is still chosen by hand, as on every
  ipq50xx board. `qcom,ath11k-fw-memory-mode = <2>` on both radios is in
  the DTS.

Result: ~850 / ~740 Mbit/s on 5 GHz with 4 streams (stock: 843 / 906),
0 OOM, 20 MB available after the run against 3 MB before. Not done:
`vm.min_free_kbytes` (OpenWrt's init sets 16384 on anything over 64 MB,
stock uses 2048), and ~50 MB of used memory that no `/proc/meminfo` counter
names on this kernel config - attributing it needs `CONFIG_PAGE_OWNER`.

## Fixes worth knowing about

- **`0828` (kernel) - MPD firmware segments allocated with `GFP_KERNEL`.**
  `mdt_load_split_segment_dma()` allocated its per-segment bounce buffers
  with a bare `GFP_DMA`, which carries no reclaim bit, so the DMA layer
  served the 268-374 KB Q6 user-PD segments from the atomic coherent pool -
  from a function that calls `request_firmware()` a few lines later. That
  is why every ipq50xx board carries `coherent_pool=2M` and why the Q6
  fails `-12` without it (`qcom-q6-mpd pd-1: Error in dma alloc ptr:
  268164`). With the fix nothing on the board draws on the pools; 512K is
  plenty and the kernel default would do.

- **`0136` - park the core before copying the firmware over it.** Warm
  reboots used to leave the NSS core dead one time in two: the old firmware
  was still executing while the new one was copied over it. Holding the core
  in reset around the copy fixed it (4/4 warm reboots). ADCDS's AX3000T port
  documents the same symptom as unresolved; this is the fix.
- **`0137` - map the meminfo block table non-cacheable** (`ioremap_wc`). Found
  first by Adriel Santos for the AX3000T port; carried here with credit.
- **`0192` (kernel) - keep `gcc_ubi0_core_clk` enabled**, so the NSS core never
  leaves reset unclocked. See *Wi-Fi*. Same family as the CMN PLL fix that this
  branch also needs.
- **`0138` (feed) - enable the NSS core clock before setting its rate**, and
  configure it before the AXI buses. Removes the `rcg didn't update its
  configuration` warnings and makes the DTS frequency stick; the core runs at
  1 GHz instead of 850 MHz.

## Acknowledgements

- [Julius Bairaktaris](https://github.com/JuliusBairaktaris) - the tree this is
  layered on, and the NSS-on-upstream-drivers idea itself.
- [ADCDS](https://github.com/ADCDS/openwrt-xiaomi-ax3000t-rd03v2) - the first
  NSS on IPQ5018 (AX3000T, `qca-nss-dp`, 6.12), and patch `0137`.
- [George Moussalem](https://github.com/georgemoussalem) - the ipq50xx target
  and the upstream stmmac/uniphy conversion this sits on.
- [qosmio](https://github.com/qosmio/openwrt-ipq) - packaging and the firmware
  tarballs.
- MayorBug (Cudy P5), AugustoAmaral (AX6000), Wallys (DR5018S) - the CMN PLL
  test matrix; LS3434 (Exigo D50) - first external user of `qca8337-nss`.

Much of the investigation behind this branch was done with an AI assistant
(Claude) in the loop; every measurement in it was taken on real hardware.
