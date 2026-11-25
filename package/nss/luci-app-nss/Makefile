# SPDX-License-Identifier: GPL-2.0-only
#
# The NSS offload status page: Status -> NSS Offload renders
# 'nss-status -j', and Status -> Overview gets the NSS core load, the
# ECM-accelerated connections and the SoC temperatures.
#
# A LuCI package of its own (it used to come out of nss-tools) so that
# translations can be added the LuCI way: po/<lang>/nss.po builds
# luci-i18n-nss-<lang>. po/templates/nss.pot is regenerated with
#   ./feeds/luci/build/i18n-scan.pl package/nss/luci-app-nss \
#     > package/nss/luci-app-nss/po/templates/nss.pot

include $(TOPDIR)/rules.mk

LUCI_TITLE:=LuCI status page for NSS offloading
LUCI_DESCRIPTION:=Status -> NSS Offload: renders 'nss-status -j' (firmware state, \
 cores, ports, Wi-Fi radios, ECM acceleration counts, SQM and fast-lane health), and \
 adds the NSS core load and the ECM-accelerated connections to Status -> Overview.
# IPQ50xx only. Selecting nss-tools on the other targets as well made
# kconfig report a recursive dependency: nss-tools-dwmac conflicts with
# nss-tools, and nss-tools is @BROKEN there anyway.
LUCI_DEPENDS:=@TARGET_qualcommax_ipq50xx +luci-base +nss-tools-dwmac
LUCI_PKGARCH:=all

PKG_LICENSE:=GPL-2.0-only

include $(TOPDIR)/feeds/luci/luci.mk

# call BuildPackage - OpenWrt buildroot signature
