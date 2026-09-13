# Shared helpers for the NSS runtime scripts (nss-up, nss-status).
#
# The nss_* helpers return their results by setting NSS_* variables the
# caller reads, so they are never used in this file.
# shellcheck disable=SC2034

# Firmware blob and version string. qca-nss0.bin is a symlink the firmware
# hotplug creates on the first arm; on a freshly flashed image it does not
# exist yet - fall back to the packaged retail blob name. Matches both
# version-string shapes: NSS.FW.12.5-210-HK.R (12.x line) and
# NSS.HK.11.4.0.5-6-R (11.4 line, the mesh-capable firmware).
nss_fw_version() {
	local blob=/lib/firmware/qca-nss0.bin
	[ -e "$blob" ] || blob=/lib/firmware/qca-nss0-retail.bin
	grep -aom1 'NSS\.[A-Z][A-Z]*\.[0-9][0-9A-Za-z.-]*' "$blob" 2>/dev/null
}

# 802.11s mesh state. Sets NSS_MESH_CFG=1 when any wireless vif is
# configured in mesh mode, and NSS_MESH_CAPABLE=1 when the firmware line
# supports mesh interfaces (11.4 only - every newer firmware rejects the
# mesh dynamic-interface allocation) AND the image was built with the mesh
# offload (the Wi-Fi mesh manager module is present).
nss_mesh_state() {
	NSS_MESH_CFG=0
	uci -q show wireless | grep -q "\.mode='mesh'" && NSS_MESH_CFG=1
	NSS_MESH_CAPABLE=0
	case "$(nss_fw_version)" in
	*.11.4.*) [ -e "/lib/modules/$(uname -r)/qca-nss-wifi-meshmgr.ko" ] && NSS_MESH_CAPABLE=1 ;;
	esac
}

# poll_until <seconds> <command...>: run the command once per second until
# it succeeds or the timeout elapses; returns the last status.
poll_until() {
	local _n=$1 _i=0
	shift
	while ! "$@"; do
		_i=$((_i + 1))
		[ "$_i" -lt "$_n" ] || return 1
		sleep 1
	done
	return 0
}

# The SQM queue section the NSS shaper serves. Resolved by TYPE, not by
# name (the section name is user-chosen): first enabled queue section,
# else the first one. Sets NSS_SQM_SEC (section) and NSS_SQM_DEV (the
# shaped netdev, from the section's 'interface' option).
nss_sqm_section() {
	local s
	NSS_SQM_SEC=""
	NSS_SQM_DEV=""
	for s in $(uci -q show sqm | sed -n "s/^sqm\.\([^.=]*\)=queue$/\1/p"); do
		[ -n "$NSS_SQM_SEC" ] || NSS_SQM_SEC=$s
		[ "$(uci -q get "sqm.$s.enabled")" = "1" ] && { NSS_SQM_SEC=$s; break; }
	done
	[ -n "$NSS_SQM_SEC" ] && NSS_SQM_DEV=$(uci -q get "sqm.${NSS_SQM_SEC}.interface")
}

# ===== C-3PO (nss.general.topology=dsa): what the board says about itself =====
#
# On the dsa topology nothing about the wiring is configured: the switch
# stays with its DSA driver, so the ports, the CPU port and the VLANs are
# the kernel's business, and which GMAC feeds what is read off sysfs here.
# The helpers below are the whole of it; nss-dwmac-probe prints their view.

# nss_gmac_index <netdev>: the IPQ5018 GMAC behind a netdev - GMAC0 is the
# ethernet@39c00000 node, GMAC1 ethernet@39d00000 - which is also its NSS
# phys_if. Prints nothing for a netdev that is not a GMAC.
nss_gmac_index() {
	case "$(readlink "/sys/class/net/$1/device" 2>/dev/null)" in
	*/39c00000.ethernet) echo 0 ;;
	*/39d00000.ethernet) echo 1 ;;
	esac
}

# nss_gmac_netdevs: every GMAC that has a netdev, one "<if>:<netdev>" per
# line - the glue's ifmap syntax.
nss_gmac_netdevs() {
	local d n i
	for d in /sys/class/net/*; do
		n="${d##*/}"
		i="$(nss_gmac_index "$n")"
		[ -n "$i" ] && echo "$i:$n"
	done
	return 0
}

# nss_dsa_conduit: the DSA conduit that carries the switch's user ports.
# A conduit is the netdev with a dsa/ directory; its user ports are the
# uppers that have a phys_switch_id. A board with two CPU ports (Redmi
# AX5400, CMCC PZ-L8) has two conduits, and the one DSA hands the user
# ports to is the one that matters here. Sets NSS_CONDUIT (empty when
# there is no switch), NSS_CONDUIT_IF (its GMAC), NSS_USER_PORTS and
# NSS_SWITCH_DRIVER (the user ports' driver, e.g. qca8k).
nss_dsa_conduit() {
	local d n u p ports cnt best=0
	NSS_CONDUIT=''
	NSS_CONDUIT_IF=''
	NSS_USER_PORTS=''
	NSS_SWITCH_DRIVER=''
	for d in /sys/class/net/*; do
		[ -d "$d/dsa" ] || continue
		n="${d##*/}"
		ports=''
		cnt=0
		for u in "$d"/upper_*; do
			[ -e "$u" ] || continue
			p="${u##*/upper_}"
			[ -n "$(cat "/sys/class/net/$p/phys_switch_id" 2>/dev/null)" ] || continue
			ports="$ports $p"
			cnt=$((cnt + 1))
		done
		[ "$cnt" -gt "$best" ] || continue
		best=$cnt
		NSS_CONDUIT="$n"
		NSS_CONDUIT_IF="$(nss_gmac_index "$n")"
		NSS_USER_PORTS="${ports# }"
	done
	if [ -n "$NSS_USER_PORTS" ]; then
		p="${NSS_USER_PORTS%% *}"
		# '|| true': these helpers run under nss-dwmac-up's set -e, where a
		# failing substitution in an assignment ends the caller.
		NSS_SWITCH_DRIVER="$(readlink "/sys/class/net/$p/device/driver" 2>/dev/null)" || true
		NSS_SWITCH_DRIVER="${NSS_SWITCH_DRIVER##*/}"
	fi
}

# nss_dsa_tagger <conduit>: the tag_8021q tagger for the switch behind the
# conduit - the kernel's tag driver that keeps the switch driver bound and
# has the switch talk to the CPU in plain 802.1Q, which the firmware
# parses. Prints the tagger's name; for a switch this plane has no such
# tagger for it prints the driver's name and returns 1.
nss_dsa_tagger() {
	local u p drv
	for u in "/sys/class/net/$1"/upper_*; do
		[ -e "$u" ] || continue
		p="${u##*/upper_}"
		[ -n "$(cat "/sys/class/net/$p/phys_switch_id" 2>/dev/null)" ] || continue
		drv="$(readlink "/sys/class/net/$p/device/driver" 2>/dev/null)" || true
		drv="${drv##*/}"
		case "$drv" in
		qca8k) echo qca-8021q; return 0 ;;
		rtl8365mb-mdio|rtl8365mb-smi) echo rtl8365mb-8021q; return 0 ;;
		esac
		echo "${drv:-unknown}"
		return 1
	done
	return 1
}

# nss_dsa_fw_mask: which GMACs to hand to the firmware, from what is up:
# the conduit that carries the user ports, and every GMAC that is not a
# conduit and is administratively up - a WAN PHY of its own (Xunison D50,
# Linksys SPNMX56, Xiaomi AX6000). What stays out: a second CPU port DSA
# does not use (AX5400, PZ-L8), and a GMAC nothing configured (the CMCC
# MR3000D-CI's GMAC0). Meant to run after netifd, when "up" means
# "configured". Sets NSS_FW_MASK as 0x<hex>; never 0x0 while there is a
# conduit.
nss_dsa_fw_mask() {
	local e i n flags mask=0
	nss_dsa_conduit
	for e in $(nss_gmac_netdevs); do
		i="${e%%:*}"
		n="${e#*:}"
		if [ -d "/sys/class/net/$n/dsa" ]; then
			[ "$n" = "$NSS_CONDUIT" ] || continue
		else
			flags="$(cat "/sys/class/net/$n/flags" 2>/dev/null || echo 0)"
			[ $(( flags & 1 )) -ne 0 ] || continue
		fi
		mask=$(( mask | (1 << i) ))
	done
	if [ "$mask" -eq 0 ] && [ -n "$NSS_CONDUIT_IF" ]; then
		mask=$(( 1 << NSS_CONDUIT_IF ))
	fi
	NSS_FW_MASK="$(printf '0x%x' "$mask")"
}
