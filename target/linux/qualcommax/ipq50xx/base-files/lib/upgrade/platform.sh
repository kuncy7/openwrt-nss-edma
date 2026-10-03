. /lib/functions/bootconfig.sh

PART_NAME=firmware
REQUIRE_IMAGE_METADATA=1

RAMFS_COPY_BIN='cmp dumpimage fw_printenv fw_setenv head seq'
RAMFS_COPY_DATA='/etc/fw_env.config /var/lock/fw_printenv.lock'

xiaomi_initramfs_prepare() {
	# Wipe UBI if running initramfs
	[ "$(rootfs_type)" = "tmpfs" ] || return 0

	local rootfs_mtdnum="$( find_mtd_index rootfs )"
	if [ ! "$rootfs_mtdnum" ]; then
		echo "unable to find mtd partition rootfs"
		return 1
	fi

	local kern_mtdnum="$( find_mtd_index ubi_kernel )"
	if [ ! "$kern_mtdnum" ]; then
		echo "unable to find mtd partition ubi_kernel"
		return 1
	fi

	ubidetach -m "$rootfs_mtdnum"
	ubiformat /dev/mtd$rootfs_mtdnum -y

	ubidetach -m "$kern_mtdnum"
	ubiformat /dev/mtd$kern_mtdnum -y
}

remove_oem_ubi_volume() {
	local oem_volume_name="$1"
	local oem_ubivol
	local mtdnum
	local ubidev

	mtdnum=$(find_mtd_index "$CI_UBIPART")
	if [ ! "$mtdnum" ]; then
		return
	fi

	ubidev=$(nand_find_ubi "$CI_UBIPART")
	if [ ! "$ubidev" ]; then
		ubiattach --mtdn="$mtdnum"
		ubidev=$(nand_find_ubi "$CI_UBIPART")
	fi

	if [ "$ubidev" ]; then
		oem_ubivol=$(nand_find_volume "$ubidev" "$oem_volume_name")
		[ "$oem_ubivol" ] && ubirmvol "/dev/$ubidev" --name="$oem_volume_name"
	fi
}

cudy_p5_set_bootslot() {
	local bootconfig_idx
	local bootconfig1_idx
	local image="/tmp/cudy-p5-bootconfig.bin"
	local verify="/tmp/cudy-p5-bootconfig.verify"
	local idx

	bootconfig_idx="$(find_mtd_index "0:bootconfig")"
	bootconfig1_idx="$(find_mtd_index "0:bootconfig1")"

	if [ -z "$bootconfig_idx" ] || [ -z "$bootconfig1_idx" ]; then
		echo "cannot find Cudy P5 bootconfig partitions"
		return 1
	fi

	# Preserve the complete 256 KiB partition, including any unknown data
	# outside the 336-byte Qualcomm bootconfig structure.
	dd if="/dev/mtd$bootconfig_idx" of="$image" bs=128k count=2 2>/dev/null || {
		echo "failed to read bootconfig"
		return 1
	}

	validate_bootconfig_magic "$image" || return 1
	set_bootconfig_primaryboot "$image" rootfs 0 || return 1

	if [ "$(get_bootconfig_primaryboot "$image" rootfs)" != "0" ]; then
		echo "failed to prepare rootfs_1 boot selection"
		return 1
	fi

	# Update and verify the redundant copy first, then the primary copy.
	for idx in "$bootconfig1_idx" "$bootconfig_idx"; do
		mtd write "$image" "/dev/mtd$idx" 2>/dev/null || {
			echo "failed to write /dev/mtd$idx"
			return 1
		}

		dd if="/dev/mtd$idx" of="$verify" bs=128k count=2 2>/dev/null || {
			echo "failed to read back /dev/mtd$idx"
			return 1
		}

		cmp -s "$image" "$verify" || {
			echo "bootconfig verification failed for /dev/mtd$idx"
			return 1
		}
	done

	rm -f "$image" "$verify"
	return 0
}

cudy_p5_do_upgrade() {
	# The DTS exposes the physical rootfs_1 slot as "rootfs" because
	# stock U-Boot hard-codes ubi.mtd=rootfs in the kernel command line.
	CI_UBIPART="rootfs"

	remove_oem_ubi_volume ubi_rootfs
	sync

	if ! nand_do_flash_file "$1"; then
		echo "failed to write OpenWrt rootfs slot"
		nand_do_upgrade_failed
		return 1
	fi

	if ! cudy_p5_set_bootslot; then
		echo "OpenWrt rootfs slot was written, but boot slot selection failed"
		nand_do_upgrade_failed
		return 1
	fi

	nand_do_upgrade_success
}

linksys_bootconfig_set_primaryboot() {
	local partname=$1
	local tempfile
	local mtdidx

	mtdidx=$(find_mtd_index "$partname")
	[ ! "$mtdidx" ] && {
		echo "cannot find mtd index for $partname"
		return 1
	}

	# No need to cleanup as files in /tmp will be removed upon reboot
	tempfile=/tmp/mtd"$mtdidx".bin
	dd if=/dev/mtd"$mtdidx" of="$tempfile" bs=1 count=336 2>/dev/null
	[ $? -ne 0 ] || [ ! -f "$tempfile" ] && {
		echo "failed to create a temp copy of /dev/mtd$mtdidx"
		return 1
	}

	set_bootconfig_primaryboot "$tempfile" "0:HLOS" $2
	[ $? -ne 0 ] && {
		echo "failed to toggle primaryboot on 0:HLOS part"
		return 1
	}

	set_bootconfig_primaryboot "$tempfile" "rootfs" $2
	[ $? -ne 0 ] && {
		echo "failed to toggle primaryboot for rootfs part"
		return 1
	}

	mtd write "$tempfile" /dev/mtd"$mtdidx" 2>/dev/null
	[ $? -ne 0 ] && {
		echo "failed to write temp copy back to /dev/mtd$mtdidx"
		return 1
	}
}

linksys_bootconfig_pre_upgrade() {
	local setenv_script="/tmp/fw_env_upgrade"

	CI_UBIPART="rootfs_1"
	boot_part="$(fw_printenv -n boot_part)"
	if [ -n "$UPGRADE_OPT_USE_CURR_PART" ]; then
		CI_UBIPART="rootfs"
	else
		if [ "$boot_part" -eq "1" ]; then
			echo "boot_part 2" >> $setenv_script
			linksys_bootconfig_set_primaryboot "0:bootconfig" 1
			linksys_bootconfig_set_primaryboot "0:bootconfig1" 1
		else
			echo "boot_part 1" >> $setenv_script
			linksys_bootconfig_set_primaryboot "0:bootconfig" 0
			linksys_bootconfig_set_primaryboot "0:bootconfig1" 0
		fi
	fi

	boot_part_ready="$(fw_printenv -n boot_part_ready)"
	if [ "$boot_part_ready" -ne "3" ]; then
		echo "boot_part_ready 3" >> $setenv_script
	fi

	auto_recovery="$(fw_printenv -n auto_recovery)"
	if [ "$auto_recovery" != "yes" ]; then
		echo "auto_recovery yes" >> $setenv_script
	fi

	if [ -f "$setenv_script" ]; then
		fw_setenv -s $setenv_script || {
			echo "failed to update U-Boot environment"
			return 1
		}
	fi
}

#
# TP-Link EX511 v2 A/B slot handling.
#
# The active slot is selected by the QCA bootconfig table in 0:BOOTCONFIG /
# 0:BOOTCONFIG1, not by a U-Boot environment variable: this U-Boot exposes no
# boot_part, and 0:APPSBLENV ships erased, so its environment must be neither
# trusted nor written. OpenWrt's shared bootconfig.sh parses this board's table
# unmodified (magic OK, numparts 8, rootfs at index 5, 0:HLOS at index 4).
#
# Nothing lives past the 336-byte table in either partition -- both are 0xFF to
# the end -- so reading 336 bytes, editing and writing back loses nothing.
#
tplink_ex511_set_primaryboot() {
	local partname="$1"
	local value="$2"
	local mtdidx tempfile

	mtdidx=$(find_mtd_index "$partname")
	[ -n "$mtdidx" ] || {
		echo "cannot find mtd index for $partname"
		return 1
	}

	# Deliberately not derived from $partname: tr is not among the binaries
	# stage2 copies into the sysupgrade ramfs, so a name built with it would
	# come out empty once this runs from a flashed system rather than from
	# initramfs. The two calls are sequential, so one scratch file is enough.
	tempfile="/tmp/bootconfig_write.bin"
	dd if=/dev/mtd"$mtdidx" of="$tempfile" bs=1 count=336 2>/dev/null || {
		echo "failed to read $partname"
		return 1
	}

	# Keep 0:HLOS in step with rootfs. The kernel actually lives inside the
	# rootfs UBI on this board, but stock carries both entries and the
	# in-tree ipq50xx caller (linksys,mx6200) flips both, so match that.
	set_bootconfig_primaryboot "$tempfile" "0:HLOS" "$value" || return 1
	set_bootconfig_primaryboot "$tempfile" "rootfs" "$value" || return 1

	mtd write "$tempfile" /dev/mtd"$mtdidx" || {
		echo "failed to write $partname"
		return 1
	}
}

tplink_ex511_pre_upgrade() {
	local mtdidx tempfile cur

	# Always install into slot 0, and make sure the table points there.
	#
	# The A/B machinery itself works: U-Boot does honour this table. With
	# primaryboot flipped to 1 it loaded the kernel out of rootfs_1 --
	# confirmed on hardware by the UBI image sequence number in its own log
	# matching the one ubiformat had just written to that partition.
	#
	# What does not work is the kernel side. U-Boot passes
	# "ubi.mtd=rootfs" on the command line, naming the active slot in its
	# own swapped view of the flash, while this board's DTS declares
	# fixed-partitions and so always resolves "rootfs" to the first slot.
	# Booting slot 1 therefore starts our kernel with stock's UBI attached
	# and hangs before init:
	#
	#   ubi0: attached mtd13 (name "rootfs", size 36 MiB)
	#   Waiting for root device /dev/ubiblock0_1...
	#
	# Slot 1 only becomes usable once the kernel can learn the active slot
	# at runtime. Until then pin installs to slot 0. Flipping a board that
	# an earlier attempt left pointing at slot 1 is part of the job, so the
	# table is corrected rather than merely inspected.
	CI_UBIPART="rootfs"

	mtdidx=$(find_mtd_index "0:BOOTCONFIG")
	[ -n "$mtdidx" ] || {
		echo "cannot find 0:BOOTCONFIG -- refusing to upgrade"
		return 1
	}

	tempfile=/tmp/bootconfig_read.bin
	dd if=/dev/mtd"$mtdidx" of="$tempfile" bs=1 count=336 2>/dev/null || {
		echo "failed to read 0:BOOTCONFIG -- refusing to upgrade"
		return 1
	}

	validate_bootconfig_magic "$tempfile" || {
		echo "0:BOOTCONFIG holds no valid table -- refusing to upgrade"
		return 1
	}

	cur=$(get_bootconfig_primaryboot "$tempfile" "rootfs")
	case "$cur" in
	0)
		echo "upgrading into rootfs; boot table already selects it"
		;;
	1)
		echo "upgrading into rootfs; moving the boot table back to it"
		tplink_ex511_set_primaryboot "0:BOOTCONFIG" 0 || return 1
		tplink_ex511_set_primaryboot "0:BOOTCONFIG1" 0 || return 1
		;;
	*)
		echo "unexpected primaryboot value '$cur' -- refusing to upgrade"
		return 1
		;;
	esac
}

linksys_mx_pre_upgrade() {
	local setenv_script="/tmp/fw_env_upgrade"

	CI_UBIPART="rootfs"
	boot_part="$(fw_printenv -n boot_part)"
	if [ -n "$UPGRADE_OPT_USE_CURR_PART" ]; then
		if [ "$boot_part" -eq "2" ]; then
			CI_KERNPART="alt_kernel"
			CI_UBIPART="alt_rootfs"
		fi
	else
		if [ "$boot_part" -eq "1" ]; then
			echo "boot_part 2" >> $setenv_script
			CI_KERNPART="alt_kernel"
			CI_UBIPART="alt_rootfs"
		else
			echo "boot_part 1" >> $setenv_script
		fi
	fi

	boot_part_ready="$(fw_printenv -n boot_part_ready)"
	if [ "$boot_part_ready" -ne "3" ]; then
		echo "boot_part_ready 3" >> $setenv_script
	fi

	auto_recovery="$(fw_printenv -n auto_recovery)"
	if [ "$auto_recovery" != "yes" ]; then
		echo "auto_recovery yes" >> $setenv_script
	fi

	if [ -f "$setenv_script" ]; then
		fw_setenv -s $setenv_script || {
			echo "failed to update U-Boot environment"
			return 1
		}
	fi
}

platform_check_image() {
	case "$(board_name)" in
	cudy,p5)
		nand_do_platform_check "$(board_name)" "$1"
		;;
	xiaomi,ax3000tv2)
		# The stock U-Boot can only attach a kernel UBI that was freshly
		# ubiformatted, which xiaomi_initramfs_prepare only does when
		# running from the initramfs. An in-place sysupgrade leaves the
		# device unbootable ("UBI init error 22").
		if [ "$(rootfs_type)" != "tmpfs" ]; then
			v "Boot the initramfs image and run sysupgrade from there."
			return 1
		fi
		;;
	*)
		return 0
		;;
	esac
}

platform_pre_upgrade() {
	case "$(board_name)" in
	xiaomi,ax3000tv2|\
	xiaomi,ax6000)
		xiaomi_initramfs_prepare
		;;
	esac
}

platform_do_upgrade() {
	case "$(board_name)" in
	cudy,p5)
		cudy_p5_do_upgrade "$1"
		;;
	cmcc,mr3000d-ci|\
	cmcc,pz-l8|\
	cmcc,rax3000q|\
	elecom,wrc-x3000gs2|\
	elecom,wrc-x3000gst2|\
	iodata,wn-dax3000gr)
		local delay

		delay=$(fw_printenv bootdelay)
		[ -z "$delay" ] || [ "$delay" -eq "0" ] && \
			fw_setenv bootdelay 3

		elecom_upgrade_prepare

		remove_oem_ubi_volume bt_fw
		remove_oem_ubi_volume ubi_rootfs
		remove_oem_ubi_volume wifi_fw
		nand_do_upgrade "$1"
		;;
	glinet,gl-b3000)
		glinet_do_upgrade "$1"
		;;
	glinet,gl-x2000)
		# The stock UBI fills the whole partition (0 free LEBs) with its
		# own wifi_fw and ubi_rootfs volumes, leaving no room for the
		# OpenWrt rootfs. Drop them before upgrading.
		CI_UBIPART="rootfs"
		remove_oem_ubi_volume ubi_rootfs
		remove_oem_ubi_volume wifi_fw
		glinet_do_upgrade "$1"
		;;
	linksys,mr5500|\
	linksys,mx2000|\
	linksys,mx5500|\
	linksys,spnmx56)
		linksys_mx_pre_upgrade "$1"
		remove_oem_ubi_volume squashfs
		nand_do_upgrade "$1"
		;;
	linksys,mx6200)
		linksys_bootconfig_pre_upgrade "$1"
		remove_oem_ubi_volume ubi_rootfs
		nand_do_upgrade "$1"
		;;
	mercusys,mr80x-v2)
		# A/B: write the inactive slot, then point tp_boot_idx at it.
		# tp_boot_idx=0 boots "rootfs", 1 boots "rootfs_1". primaryboot
		# must stay 0, the stock loader and the button recovery pick the
		# slot by name from tp_boot_idx alone. tp_boot_idx only changes
		# once the new slot is fully written.
		local tp active target newtp primaryboot bcidx bcfile

		# Warn only, flipping primaryboot here would desync the running
		# and the next-boot slot names.
		bcidx=$(find_mtd_index "0:bootconfig")
		if [ -n "$bcidx" ]; then
			bcfile=/tmp/mtd"$bcidx".bin
			dd if=/dev/mtd"$bcidx" of="$bcfile" bs=1 count=336 2>/dev/null
			primaryboot=$(get_bootconfig_primaryboot "$bcfile" "rootfs")
			[ "$primaryboot" = "0" ] || \
				echo "WARNING: primaryboot=$primaryboot (expected 0); slot selection may be inverted"
		fi

		tp=$(fw_printenv -n tp_boot_idx 2>/dev/null)
		case "$tp" in
			1) active="rootfs_1"; target="rootfs";   newtp=0 ;;
			*) active="rootfs";   target="rootfs_1"; newtp=1 ;;
		esac

		if [ -n "$UPGRADE_OPT_USE_CURR_PART" ]; then
			CI_UBIPART="$active"
		else
			CI_UBIPART="$target"
		fi

		remove_oem_ubi_volume ubi_rootfs
		remove_oem_ubi_volume wifi_fw
		remove_oem_ubi_volume bt_fw
		sync
		nand_do_flash_file "$1" || nand_do_upgrade_failed
		if [ -z "$UPGRADE_OPT_USE_CURR_PART" ]; then
			fw_setenv tp_boot_idx "$newtp" || {
				echo "failed to set tp_boot_idx"
				nand_do_upgrade_failed
			}
		fi
		nand_do_upgrade_success
		;;
	tplink,archer-ax55-v1|\
	tplink,eap650-outdoor-v1)
		# Dual boot: install into the inactive rootfs/rootfs_1 slot,
		# then point tp_boot_idx at it. The running slot is left
		# untouched as a fallback - if the new image fails to load,
		# TP-Link's U-Boot boots the other slot on its own (only on
		# load failure though: there is no boot counter, a kernel
		# that boots and then crashes is not detected).
		local idx=1
		CI_UBIPART="rootfs_1"
		if grep -q 'ubi.mtd=rootfs_1' /proc/cmdline; then
			idx=0
			CI_UBIPART="rootfs"
		fi
		fw_setenv tp_boot_idx $idx || {
			echo "failed to set tp_boot_idx $idx"
			return 1
		}
		# a slot last written by TP-Link firmware carries extra
		# volumes that would leave no room for ours
		remove_oem_ubi_volume ubi_rootfs
		remove_oem_ubi_volume tp_data
		nand_do_upgrade "$1"
		;;
	xiaomi,ax3000tv2|\
	xiaomi,ax6000|\
	xiaomi,redmi-ax5400)
		# Make sure that UART is enabled
		fw_setenv boot_wait on
		fw_setenv uart_en 1

		# Enforce single partition.
		fw_setenv flag_boot_rootfs 0
		fw_setenv flag_last_success 0
		fw_setenv flag_boot_success 1
		fw_setenv flag_try_sys1_failed 8
		fw_setenv flag_try_sys2_failed 8

		# Kernel and rootfs are placed in 2 different UBI
		CI_KERN_UBIPART="ubi_kernel"
		CI_ROOT_UBIPART="rootfs"
		CI_DATA_UBIPART="rootfs"
		nand_do_upgrade "$1"
		;;
	yuncore,ax830|\
	yuncore,ax850|\
	zyxel,scr50axe)
		CI_UBIPART="rootfs"
		remove_oem_ubi_volume ubi_rootfs
		remove_oem_ubi_volume bt_fw
		remove_oem_ubi_volume wifi_fw
		nand_do_upgrade "$1"
		;;
	tplink,ex511-v2)
		# Selects the inactive slot and sets CI_UBIPART to it. The UBI
		# container is the "rootfs"/"rootfs_1" partition, never the
		# default "ubi": without CI_UBIPART set, both
		# remove_oem_ubi_volume and nand_do_upgrade look up an mtd
		# partition that does not exist here, so the volume removals
		# silently no-op and the upgrade fails.
		tplink_ex511_pre_upgrade "$1" || return 1
		# Stock UBI holds kernel + bt_fw + ubi_rootfs. Clearing them is
		# a no-op on the erased slot and required on the stock one.
		remove_oem_ubi_volume bt_fw
		remove_oem_ubi_volume ubi_rootfs
		remove_oem_ubi_volume kernel
		nand_do_upgrade "$1"
		;;
	*)
		default_do_upgrade "$1"
		;;
	esac
}
