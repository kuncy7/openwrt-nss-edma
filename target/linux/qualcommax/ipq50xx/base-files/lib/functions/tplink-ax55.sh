# SPDX-License-Identifier: GPL-2.0-or-later
#
# The TP-Link Archer AX55 v1 stores its factory MAC as the 6-byte file
# "default-mac" inside the tp_data UBIFS volume (there is no raw nvmem cell
# for it). Read it once and cache the result.

. /lib/functions.sh

tplink_ax55_label_mac() {
	local cache="/tmp/.tplink-ax55-mac"
	local idx ubi mac

	# ubi{attach,detach} live in /usr/sbin, which the firmware (caldata)
	# hotplug's restricted PATH omits - make it explicit
	local PATH="/usr/sbin:/usr/bin:$PATH"

	[ -s "$cache" ] && { cat "$cache"; return; }

	# The wired (uci-defaults) and Wi-Fi (caldata) readers can run
	# concurrently; serialise them so they do not both ubiattach the same
	# volume (the loser would get "already attached" and no MAC).
	exec 9>/tmp/.tplink-ax55-mac.lock
	flock 9

	if [ ! -s "$cache" ]; then
		idx=$(find_mtd_index tp_data)
		if [ -n "$idx" ]; then
			ubi=$(ubiattach -m "$idx" 2>/dev/null | \
				sed -n 's/.*device number \([0-9]*\).*/\1/p')
			if [ -n "$ubi" ]; then
				mkdir -p /tmp/.tp_data
				if mount -t ubifs -o ro "/dev/ubi${ubi}_0" \
						/tmp/.tp_data 2>/dev/null; then
					[ -s /tmp/.tp_data/default-mac ] && \
						mac=$(hexdump -v -n6 \
							-e '5/1 "%02x:" 1/1 "%02x"' \
							/tmp/.tp_data/default-mac)
					umount /tmp/.tp_data
				fi
				ubidetach -m "$idx" 2>/dev/null
			fi
			[ -n "$mac" ] && echo "$mac" > "$cache"
		fi
	fi

	exec 9>&-

	[ -s "$cache" ] && cat "$cache"
}
