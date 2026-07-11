include $(TOPDIR)/rules.mk

PKG_NAME:=ax55-testdefaults
PKG_VERSION:=7
PKG_RELEASE:=1

PKG_MAINTAINER:=AX55 port
PKG_LICENSE:=GPL-2.0-or-later

include $(INCLUDE_DIR)/package.mk

define Package/ax55-testdefaults
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=TP-Link Archer AX55 v1 test-build defaults
  DEPENDS:=@TARGET_qualcommax
endef

define Package/ax55-testdefaults/description
  Defaults for the TP-Link Archer AX55 v1 images: factory MACs from the
  tp_data UBI volume (label MAC on lan, +1 wan, +2/+3 radios), LAN management
  address 192.168.1.3, every WiFi radio up as a WPA2 access point (SSID
  OpenWrt-AX55) so a freshly flashed board is reachable without serial, WAN
  as a normal DHCP client, and (dev builds) ssh/ping allowed from wan.
endef

define Build/Compile
endef

define Package/ax55-testdefaults/install
	$(INSTALL_DIR) $(1)/etc/uci-defaults
	$(INSTALL_BIN) ./files/99-ax55-test $(1)/etc/uci-defaults/99-ax55-test
	$(INSTALL_BIN) ./files/20-ax55-macs $(1)/etc/uci-defaults/20-ax55-macs
	$(INSTALL_DIR) $(1)/etc/hotplug.d/ieee80211
	$(INSTALL_BIN) ./files/25-ax55-wifi $(1)/etc/hotplug.d/ieee80211/25-ax55-wifi
	$(INSTALL_DIR) $(1)/lib
	$(INSTALL_BIN) ./files/read-ax55-mac $(1)/lib/read-ax55-mac
endef

$(eval $(call BuildPackage,ax55-testdefaults))
