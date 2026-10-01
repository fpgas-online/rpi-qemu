#!/bin/sh
# Fetch what the Raspberry Pi Zero (raspi0) tests boot: the stock kernel,
# the Zero W device tree and its disable-bt and dwc2 overlays, the kernel
# modules the tests load, and Alpine's armhf (ARMv6) minirootfs.
#
# Everything comes from one pinned raspberrypi/firmware commit (kernel
# 6.18.52+), so the log checks see the same kernel whether deb.yml's cache
# restored test-images/rpi0/ or not. A file already there is kept.
#
# Used by ci/boot-test.sh (in a debian:<suite> container) and by deb.yml's
# USB/IP interop step (on the runner itself). Needs wget. Run from the
# repository's top.
set -eu

fw=https://raw.githubusercontent.com/raspberrypi/firmware/bead686816848038563a542dc854346ab13253a2
dir=test-images/rpi0

get() {  # get <url> <file>: download unless present
  [ -s "$2" ] || wget -q -O "$2" "$1"
}

mkdir -p "$dir/modules"
get "$fw/boot/kernel.img" "$dir/kernel.img"
get "$fw/boot/bcm2708-rpi-zero-w.dtb" "$dir/bcm2708-rpi-zero-w.dtb"
get "$fw/boot/overlays/disable-bt.dtbo" "$dir/disable-bt.dtbo"
get "$fw/boot/overlays/dwc2.dtbo" "$dir/dwc2.dtbo"

# USB network drivers, for usb-net.
for m in cdc_ether rndis_host; do
  get "$fw/modules/6.18.52+/kernel/drivers/net/usb/$m.ko.xz" "$dir/modules/$m.ko.xz"
done
# The dwc2 driver and the USB gadget functions (#22).
for p in roles/roles dwc2/dwc2 gadget/libcomposite \
         gadget/function/u_serial gadget/function/usb_f_acm \
         gadget/function/u_ether gadget/function/usb_f_ecm \
         gadget/function/usb_f_ncm gadget/function/usb_f_mass_storage \
         gadget/function/usb_f_ss_lb; do
  get "$fw/modules/6.18.52+/kernel/drivers/usb/$p.ko.xz" "$dir/modules/$(basename "$p").ko.xz"
done

get "https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/armhf/alpine-minirootfs-3.21.3-armhf.tar.gz" \
  test-images/alpine-minirootfs-armhf.tar.gz
