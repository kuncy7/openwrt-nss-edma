include $(TOPDIR)/rules.mk
include $(INCLUDE_DIR)/kernel.mk

PKG_NAME:=rtl8367s-nss
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only

include $(INCLUDE_DIR)/package.mk

define KernelPackage/rtl8367s-nss
  SECTION:=kernel
  CATEGORY:=Kernel modules
  SUBMENU:=Network Devices
  TITLE:=RTL8367S-VB fabric re-arm after rtl8365mb teardown
  DEPENDS:=@TARGET_qualcommax_ipq50xx
  FILES:=$(PKG_BUILD_DIR)/rtl8367s_nss.ko
endef

define KernelPackage/rtl8367s-nss/description
  Restores the trunk force word, disables CPU tag insertion and wakes
  the front PHYs after the DSA driver has been unbound.
endef

define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
	$(CP) ./src/* $(PKG_BUILD_DIR)/
endef

define Build/Compile
	+$(KERNEL_MAKE) M="$(PKG_BUILD_DIR)" modules
endef

$(eval $(call KernelPackage,rtl8367s-nss))
