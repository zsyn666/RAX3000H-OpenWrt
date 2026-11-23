# SPDX-License-Identifier: GPL-2.0-only
include $(TOPDIR)/rules.mk

ARCH:=arm
BOARD:=rax3000h
BOARDNAME:=RAX3000H vendor-kernel userspace
CPU_TYPE:=cortex-a9
CPU_CFLAGS_cortex-a9:=-mcpu=cortex-a9
FEATURES:=squashfs targz
DEVICE_TYPE:=basic

# ImmortalWrt build metadata/UAPI baseline only. This target does not replace
# the running vendor Linux 5.10 kernel or supply compatible kernel modules.
KERNEL_PATCHVER:=6.12

include $(INCLUDE_DIR)/target.mk

define Target/Description
  Cortex-A9 soft-float userspace for the original RAX3000H kernel.
  Kernel and flash image support are not implemented.
endef

$(eval $(call BuildTarget))
