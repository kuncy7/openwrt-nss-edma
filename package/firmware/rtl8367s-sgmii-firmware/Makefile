include $(TOPDIR)/rules.mk

PKG_NAME:=rtl8367s-sgmii-firmware
PKG_RELEASE:=1

PKG_LICENSE:=GPL-2.0
PKG_LICENSE_FILES:=

include $(INCLUDE_DIR)/package.mk

define Package/rtl8367s-sgmii-firmware
  SECTION:=firmware
  CATEGORY:=Firmware
  TITLE:=Realtek RTL8367S (H)SGMII 8051 firmware
endef

define Package/rtl8367s-sgmii-firmware/description
  8051 microcontroller firmware for the RTL8367S SerDes, required to bring up
  the SGMII/HSGMII extension port. Extracted from the GPL-2.0 mediatek vendor
  driver (rtl8367c_asicdrv_port.c, Sgmii_Init array) already shipped in
  OpenWrt's mediatek target.
endef

define Build/Compile
endef

define Package/rtl8367s-sgmii-firmware/install
	$(INSTALL_DIR) $(1)/lib/firmware/rtl_switch
	$(INSTALL_DATA) ./files/rtl8367s-sgmii.bin \
		$(1)/lib/firmware/rtl_switch/rtl8367s-sgmii.bin
endef

$(eval $(call BuildPackage,rtl8367s-sgmii-firmware))
