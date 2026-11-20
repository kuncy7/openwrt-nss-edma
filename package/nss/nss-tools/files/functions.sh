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
	local blob=/lib/firmware/qca-nss0.bin line
	[ -e "$blob" ] || blob=/lib/firmware/qca-nss0-retail.bin
	# The line named to the driver (nss_fw_select) wins over both.
	line="$(cat /sys/module/qca_nss_drv/parameters/firmware 2>/dev/null)" || line=''
	[ -n "$line" ] && [ -e "/lib/firmware/qca-nss0-retail-$line.bin" ] &&
		blob="/lib/firmware/qca-nss0-retail-$line.bin"
	grep -aom1 'NSS\.[A-Z][A-Z]*\.[0-9][0-9A-Za-z.-]*' "$blob" 2>/dev/null
}

# Pick the firmware line the core boots: nss.general.firmware = auto (or
# unset), 12.5 or 12.2. An image carries the lines as
# qca-nss0-retail-<line>.bin (nss-firmware, NSS_FIRMWARE_IPQ50XX_BOTH), with
# qca-nss0-retail.bin a link to the one it was built for. 'auto' is 12.2 on
# a board with less than 384 MB of RAM - with 12.5 some 802.11ac clients leave
# payload buffers in the firmware, which the small pool of such a board cannot
# afford (#64) - and the build's line everywhere else.
# The line goes to qca-nss-drv's 'firmware' parameter (feed patch 0151), which
# the driver reads when the first port is armed - so call this after the
# module is loaded and before the arm. Nothing is written to flash.
# The host side follows the line that was loaded (feed patch 0152), so one
# image runs either.
# Sets NSS_FW_LINE to the line asked for, empty when the driver keeps its own
# name (qca-nss0.bin).
nss_fw_select() {
	local want built mem knob=/sys/module/qca_nss_drv/parameters/firmware

	NSS_FW_LINE=''
	built="$(readlink /lib/firmware/qca-nss0-retail.bin)" || built=''
	built="${built##*/qca-nss0-retail-}"
	built="${built#qca-nss0-retail-}"
	built="${built%.bin}"
	want="$(uci -q get nss.general.firmware)" || want=''
	case "$want" in
	''|auto)
		want="$built"
		mem="$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo)" || mem=''
		[ -n "$mem" ] && [ "$mem" -lt 393216 ] &&
			[ -e /lib/firmware/qca-nss0-retail-12.2.bin ] && want=12.2
		;;
	12.2|12.5)
		;;
	*)
		logger -p user.warn -t nss "nss.general.firmware is '$want', expected auto, 12.5 or 12.2 - keeping the default"
		want="$built"
		;;
	esac
	# An image with one firmware file has no line to name.
	[ -n "$want" ] || return 0
	if [ ! -e "/lib/firmware/qca-nss0-retail-$want.bin" ]; then
		logger -p user.warn -t nss "nss.general.firmware=$want: this image has no qca-nss0-retail-$want.bin - keeping the default"
		want="$built"
		[ -n "$want" ] && [ -e "/lib/firmware/qca-nss0-retail-$want.bin" ] || return 0
	fi
	if [ ! -w "$knob" ]; then
		[ "$want" = "$built" ] ||
			logger -p user.warn -t nss "nss.general.firmware=$want: this qca-nss-drv has no firmware parameter - ignored"
		return 0
	fi
	printf %s "$want" > "$knob" 2>/dev/null || return 0
	NSS_FW_LINE="$want"
	return 0
}

# Hand nss.general.mem_profile to qca-nss-drv (feed patch 0153): 256m is its
# low profile, 512m and 1g its medium one, auto (or unset) leaves the choice to
# the driver - low below 384 MB of RAM, the build's profile otherwise. The
# driver takes the parameter when it boots the core, so call this after the
# module is loaded and before the first port is armed. Never fails.
nss_drv_mem_profile() {
	local want n knob=/sys/module/qca_nss_drv/parameters/mem_profile

	want="$(uci -q get nss.general.mem_profile)" || want=''
	case "$want" in
	256m|256M) n=3 ;;
	512m|512M|1g|1G) n=2 ;;
	*) n=0 ;;
	esac
	if [ -w "$knob" ]; then
		echo "$n" > "$knob" 2>/dev/null || true
	elif [ "$n" != 0 ]; then
		logger -p user.warn -t nss "nss.general.mem_profile=$want: this qca-nss-drv has no mem_profile parameter - ignored"
	fi
	return 0
}

# Hand nss.general.mem_profile to ath11k: auto (or unset) leaves the choice to
# the driver, which goes by the RAM size; 1g, 512m and 256m force a row of its
# table. Call between 'modprobe ath11k' and the bus modules - the profile is
# read when a radio probes, and kmodloader's modprobe passes no parameters.
# Never fails: it also runs where the radios have to come up regardless.
nss_ath11k_mem_profile() {
	local want n knob=/sys/module/ath11k/parameters/mem_profile

	want="$(uci -q get nss.general.mem_profile)" || want=''
	case "$want" in
	''|auto) n=0 ;;
	1g|1G) n=1 ;;
	512m|512M) n=2 ;;
	256m|256M) n=3 ;;
	*)
		logger -p user.warn -t nss "nss.general.mem_profile is '$want', expected auto, 1g, 512m or 256m - left to the driver"
		n=0
		;;
	esac
	if [ -w "$knob" ]; then
		echo "$n" > "$knob" 2>/dev/null || true
	elif [ "$n" != 0 ]; then
		logger -p user.warn -t nss "nss.general.mem_profile=$want: this ath11k has no mem_profile parameter - ignored"
	fi
	return 0
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
	[ -e "/lib/modules/$(uname -r)/qca-nss-wifi-meshmgr.ko" ] || return 0
	# 11.4 on ipq807x/ipq60xx, where newer firmware rejects the mesh vdev;
	# on IPQ5018 the 12.2 and 12.5 firmware run the mesh offload as well.
	case "$(nss_fw_version)" in
	*.11.4.*) NSS_MESH_CAPABLE=1 ;;
	*.12.*) grep -q 'qcom,ipq5018' /proc/device-tree/compatible 2>/dev/null && NSS_MESH_CAPABLE=1 ;;
	esac
	return 0
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

# nss_dsa_user_ports <netdev>: the DSA user ports whose conduit this netdev
# is - its uppers that carry a phys_switch_id - space separated. Empty for a
# CPU port DSA gave no user port, which on a two-CPU-port board is what
# board.d assigning no conduit leaves behind (DSA then hands every user port
# to the default conduit).
nss_dsa_user_ports() {
	local u p ports=''
	for u in "/sys/class/net/$1"/upper_*; do
		[ -e "$u" ] || continue
		p="${u##*/upper_}"
		[ -n "$(cat "/sys/class/net/$p/phys_switch_id" 2>/dev/null)" ] || continue
		ports="$ports $p"
	done
	echo "${ports# }"
}

# nss_dsa_conduit: the conduit that carries most of the switch's user ports
# - the trunk the tagger and the switch settings key off. A conduit is the
# netdev with a dsa/ directory; its user ports are the uppers that have a
# phys_switch_id. A board with two CPU ports (Redmi AX5400, CMCC PZ-L8,
# Cudy P5) has two conduits and DSA can hand user ports to both, so this is
# a majority vote, not "the conduit": for which GMACs the firmware needs,
# see nss_dsa_fw_mask. Sets NSS_CONDUIT (empty when there is no switch),
# NSS_CONDUIT_IF (its GMAC), NSS_USER_PORTS and NSS_SWITCH_DRIVER (the user
# ports' driver, e.g. qca8k).
nss_dsa_conduit() {
	local d n p ports cnt best=0
	NSS_CONDUIT=''
	NSS_CONDUIT_IF=''
	NSS_USER_PORTS=''
	NSS_SWITCH_DRIVER=''
	for d in /sys/class/net/*; do
		[ -d "$d/dsa" ] || continue
		n="${d##*/}"
		ports="$(nss_dsa_user_ports "$n")"
		cnt=0
		for p in $ports; do cnt=$((cnt + 1)); done
		[ "$cnt" -gt "$best" ] || continue
		best=$cnt
		NSS_CONDUIT="$n"
		NSS_CONDUIT_IF="$(nss_gmac_index "$n")"
		NSS_USER_PORTS="$ports"
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
	local p drv
	for p in $(nss_dsa_user_ports "$1"); do
		drv="$(readlink "/sys/class/net/$p/device/driver" 2>/dev/null)" || true
		drv="${drv##*/}"
		case "$drv" in
		qca8k) echo qca-8021q; return 0 ;;
		rtl8365mb-mdio|rtl8365mb-smi) echo rtl8365mb-8021q; return 0 ;;
		an8855-switch) echo an8855-8021q; return 0 ;;
		esac
		echo "${drv:-unknown}"
		return 1
	done
	return 1
}

# nss_dsa_fw_mask: which GMACs to hand to the firmware, from what is up:
# every CPU port DSA gave at least one user port, and every GMAC that is not
# a CPU port and is administratively up - a WAN PHY of its own (Xunison D50,
# Linksys SPNMX56, Xiaomi AX6000). What stays out: a CPU port DSA left with
# no user port, and a GMAC nothing configured (the CMCC MR3000D-CI's GMAC0).
# Meant to run after netifd, when "up" means "configured". Sets NSS_FW_MASK
# as 0x<hex>; never 0x0 while there is a conduit.
nss_dsa_fw_mask() {
	local e i n flags mask=0
	nss_dsa_conduit
	for e in $(nss_gmac_netdevs); do
		i="${e%%:*}"
		n="${e#*:}"
		if [ -d "/sys/class/net/$n/dsa" ]; then
			# Every CPU port with user ports, not only NSS_CONDUIT: a board
			# can put its wan port on the other one (board.d does exactly
			# that for the AX5400, the PZ-L8 and the Cudy P5), and then that
			# GMAC carries the WAN data path and the firmware needs it too.
			[ -n "$(nss_dsa_user_ports "$n")" ] || continue
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
