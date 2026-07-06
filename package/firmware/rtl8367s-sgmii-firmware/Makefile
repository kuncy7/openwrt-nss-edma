include $(TOPDIR)/rules.mk

PKG_NAME:=rtl8367s-sgmii-firmware
PKG_RELEASE:=2

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
  the SGMII/HSGMII extension port. This is the 1223-byte Sgmii_Init array from
  the GPL-2.0 Realtek rtl8367c vendor driver as used for the RTL8367S on the
  TP-Link Archer AX55 (chip rev 0x00A0, option 1). NOTE: an earlier 1183-byte
  variant from OpenWrt's mediatek target is a DIFFERENT 8051 program and brings
  the trunk link up but does not pass traffic on this part.
endef

define Build/Compile
endef

define Package/rtl8367s-sgmii-firmware/install
	$(INSTALL_DIR) $(1)/lib/firmware/rtl_switch
	$(INSTALL_DATA) ./files/rtl8367s-sgmii.bin \
		$(1)/lib/firmware/rtl_switch/rtl8367s-sgmii.bin
endef

$(eval $(call BuildPackage,rtl8367s-sgmii-firmware))
