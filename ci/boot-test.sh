#!/bin/sh
# Boot-test the packages one build made: boot a Raspberry Pi kernel and
# initramfs in qemu-rpi-system-aarch64 -M raspi4b, over U-Boot, over socket
# networking and over PXE with qemu-rpi-pxeboot's firmware; export a device
# over USB/IP; and boot the stock Raspberry Pi Zero W kernel on -M raspi0,
# its USB gadget included.
#
# Runs as root inside a clean debian:<suite> container (deb.yml's "Boot test"
# step), with:
#   /src   this repository's checkout, read-write (the tests write
#          test-images/ and tmp/);
#   /debs  the built .debs, qemu-rpi-pxeboot's included.
set -eu
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y /debs/qemu-rpi-system-arm_*.deb /debs/qemu-rpi-system-data_*.deb \
  /debs/qemu-rpi-pxeboot_*.deb
apt-get install -y --no-install-recommends ca-certificates git wget cpio python3 \
  build-essential gcc-aarch64-linux-gnu make bc bison flex libssl-dev
qemu=$(command -v qemu-rpi-system-aarch64)
"$qemu" --version
cd /src

pin() {
  python3 -c 'import sys, tomllib; print(tomllib.load(open("upstreams.toml", "rb"))["qemu-rpi-pxeboot"][sys.argv[1]])' "$1"
}

echo "=== U-Boot for the direct boot tests (rpi_4_qemu_defconfig), at qemu-rpi-pxeboot's pin ==="
mkdir -p test-images/u-boot
git -C test-images/u-boot init -q
git -C test-images/u-boot fetch -q --depth 1 "$(pin git)" "$(pin commit)"
git -C test-images/u-boot checkout -q FETCH_HEAD
cp ci/rpi_4_qemu_defconfig test-images/u-boot/configs/rpi_4_qemu_defconfig
make -C test-images/u-boot rpi_4_qemu_defconfig
make -C test-images/u-boot -j"$(nproc)" CROSS_COMPILE=aarch64-linux-gnu-

echo "=== Raspberry Pi kernel and device tree ==="
# Restored from the runner's cache (deb.yml) when present.
mkdir -p test-images/tftpboot
if [ ! -f test-images/kernel8.img ]; then
  fw=https://raw.githubusercontent.com/raspberrypi/firmware/master/boot
  wget -q -O test-images/kernel8.img "$fw/kernel8.img"
  wget -q -O test-images/bcm2711-rpi-4-b.dtb "$fw/bcm2711-rpi-4-b.dtb"
fi
cp test-images/bcm2711-rpi-4-b.dtb test-images/tftpboot/
# kernel8.img is gzip-compressed; the direct boot tests want the raw Image.
zcat test-images/kernel8.img > test-images/tftpboot/Image

echo "=== Alpine initramfs ==="
wget -q -O test-images/alpine-minirootfs.tar.gz \
  "https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/aarch64/alpine-minirootfs-3.21.3-aarch64.tar.gz"
python3 build-initramfs.py
cp test-images/test-initramfs.cpio.gz test-images/tftpboot/initrd.gz

echo "=== Single-instance boot test ==="
QEMU_OVERRIDE=$qemu python3 run-rpi-boot-test.py
echo "=== Socket networking boot test (no peer) ==="
QEMU_OVERRIDE=$qemu python3 run-rpi-socket-boot-test.py
echo "=== Socket network test (with DHCP/TFTP peer) ==="
QEMU_OVERRIDE=$qemu python3 run-rpi-socket-network-test.py

pxeboot=/usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.bin
echo "=== PXE boot test (cfgtxt + gzip decompression), with qemu-rpi-pxeboot's firmware ==="
QEMU_OVERRIDE=$qemu PXEBOOT_OVERRIDE=$pxeboot python3 run-rpi-pxeboot-test.py
echo "=== PXE boot test from a flat TFTP root (prefix fallback) ==="
QEMU_OVERRIDE=$qemu PXEBOOT_OVERRIDE=$pxeboot PXE_FLAT=1 python3 run-rpi-pxeboot-test.py

echo "=== USB/IP server test ==="
QEMU_OVERRIDE=$qemu python3 run-usbip-test.py

echo "=== Raspberry Pi Zero kernel, device tree, overlays and modules ==="
# Restored from the runner's cache (deb.yml) when present.
sh ci/fetch-rpi0.sh
apt-get install -y --no-install-recommends device-tree-compiler
# Debian's dtc (1.7.2 in trixie) applies dwc2.dtbo; Ubuntu 24.04's 1.7.0
# fails on it (FDT_ERR_NOTFOUND).
fdtoverlay -i test-images/rpi0/bcm2708-rpi-zero-w.dtb \
  -o test-images/rpi0/bcm2708-rpi-zero-w-disable-bt.dtb test-images/rpi0/disable-bt.dtbo
# dtoverlay=dwc2: the upstream dwc2 driver on the OTG port (#22).
fdtoverlay -i test-images/rpi0/bcm2708-rpi-zero-w.dtb \
  -o test-images/rpi0/bcm2708-rpi-zero-w-dwc2.dtb test-images/rpi0/dwc2.dtbo

echo "=== Raspberry Pi Zero initramfs ==="
python3 build-initramfs.py --target rpi0
python3 build-initramfs.py --target rpi0-gadget

# Boot 1 makes a sparse 4 GiB test-images/rpi0-sd.img for its SD discard
# check (#39), which the harness deletes again.
echo "=== Raspberry Pi Zero (raspi0) boot test ==="
QEMU_OVERRIDE=$qemu python3 run-rpi0-boot-test.py
echo "=== Raspberry Pi Zero (raspi0) USB gadget test ==="
QEMU_OVERRIDE=$qemu python3 run-rpi0-gadget-test.py
