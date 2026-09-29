#!/bin/sh
# USB/IP interop with the Linux kernel's own USB/IP host, vhci-hcd:
#   - run-usbip-vhci-test.py: vhci-hcd imports a device that QEMU's
#     usbip-server exports -- the interop usbip-server exists for;
#   - run-rpi0-gadget-vhci-test.py: the emulated Pi Zero's USB gadget (stock
#     kernel, dwc2 peripheral mode) on this host's kernel, through vhci-hcd
#     (#22).
#
# Runs as root (sudo) on the runner itself, not in a container, since it
# loads vhci-hcd into the runner's kernel: deb.yml's "USB/IP interop" step.
# That is why it tests the static binary (tmp/static-output/, from
# ci/build-static.sh), which runs on the runner's Ubuntu, not the .debs.
# Run from the repository's top.
set -eu

apt-get update
apt-get install -y "linux-modules-extra-$(uname -r)"
modprobe vhci-hcd
ls /sys/devices/platform/vhci_hcd.0

mkdir -p tmp/static
tar xzf tmp/static-output/qemu-rpi-static-linux-amd64.tar.gz -C tmp/static
qemu=$PWD/tmp/static/qemu-rpi-system-aarch64-static

echo "=== USB/IP interop (vhci-hcd) ==="
QEMU_OVERRIDE=$qemu python3 run-usbip-vhci-test.py

echo "=== Raspberry Pi Zero gadget on the host's vhci-hcd ==="
sh ci/fetch-rpi0.sh
# Ubuntu 24.04's fdtoverlay (dtc 1.7.0) fails on dwc2.dtbo
# (FDT_ERR_NOTFOUND); Debian trixie's (1.7.2) applies it, as in the boot
# test, which has usually made this file already.
if [ ! -s test-images/rpi0/bcm2708-rpi-zero-w-dwc2.dtb ]; then
  docker run --rm --platform linux/amd64 -v "$PWD/test-images/rpi0:/w" -w /w debian:trixie sh -ec '
    apt-get update -qq
    apt-get install -y -qq device-tree-compiler
    fdtoverlay -i bcm2708-rpi-zero-w.dtb -o bcm2708-rpi-zero-w-dwc2.dtb dwc2.dtbo'
fi
python3 build-initramfs.py --target rpi0-gadget
QEMU_OVERRIDE=$qemu python3 run-rpi0-gadget-vhci-test.py
