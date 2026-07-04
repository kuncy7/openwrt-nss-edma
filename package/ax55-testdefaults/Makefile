include $(TOPDIR)/rules.mk

PKG_NAME:=ax55-testdefaults
PKG_VERSION:=2
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
  Test-only defaults for the TP-Link Archer AX55 v1 bring-up images: sets the
  LAN management address to 192.168.1.3, brings every WiFi radio up as a WPA2
  access point (SSID OpenWrt-AX55), and - since the RTL8367S LAN trunk is not
  yet verified working - also enables a reachable dropbear/static address on
  the WAN interface (plain GE PHY, independent of the RTL8367S/HSGMII path),
  so the board stays reachable even if only WAN link comes up.
endef

define Build/Compile
endef

define Package/ax55-testdefaults/install
	$(INSTALL_DIR) $(1)/etc/uci-defaults
	$(INSTALL_BIN) ./files/99-ax55-test $(1)/etc/uci-defaults/99-ax55-test
endef

$(eval $(call BuildPackage,ax55-testdefaults))
