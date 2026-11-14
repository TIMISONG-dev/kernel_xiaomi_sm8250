# SPDX-License-Identifier: GPL-2.0-only

ifdef CONFIG_SPECTRA_CAMERA_LEGACY
# Use USERINCLUDE when you must reference the UAPI directories only.
USERINCLUDE     += \
                -I$(srctree)/techpack/camera-legacy/include/uapi

# Use LINUXINCLUDE when you must reference the include/ directory.
# Needed to be compatible with the O= option
LINUXINCLUDE    += \
                -I$(srctree)/techpack/camera-legacy/include/uapi \
                -I$(srctree)/techpack/camera-legacy/include
obj-y += drivers/
else
$(info Target not found)
endif
