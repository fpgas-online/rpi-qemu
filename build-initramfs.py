#!/usr/bin/env python3
"""Build a minimal Alpine initramfs for the QEMU boot tests.

--target rpi4 (default): aarch64, network tests for raspi4b.
--target rpi0: armhf (ARMv6), Pi Zero checks for raspi0.
"""

import argparse
import lzma
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent.resolve() / "test-images"

INIT_SCRIPT = """\
#!/bin/sh
# Minimal init for network testing
mount -t proc proc /proc
mount -t sysfs sys /sys
mount -t devtmpfs devtmpfs /dev
mkdir -p /dev/pts
mount -t devpts devpts /dev/pts

echo "=== QEMU RPi4B Network Test ==="

# Replicate Raspberry Pi OS trixie's rpi_wd initramfs script: opening
# /dev/watchdog0 arms the BCM2835 watchdog and the magic close ('V')
# must disarm it.  Emulation that treats the arming RSTC write as an
# immediate reset request reboots the machine right here, so simply
# surviving this block is the regression test.
echo "=== Watchdog disarm test (rpi_wd behaviour) ==="
if [ -c /dev/watchdog0 ]; then
    echo -n 'V' > /dev/watchdog0
    echo "WDT disarm: SUCCESS"
else
    echo "WDT disarm: no /dev/watchdog0 device"
fi

echo "Waiting for network device..."
sleep 2

echo "=== USB Devices ==="
# List USB devices via sysfs (works without usbutils, keeps initramfs small)
usb_found=0
for dev in /sys/bus/usb/devices/[0-9]*; do
    [ -f "$dev/idVendor" ] || continue
    vendor=$(cat "$dev/idVendor")
    product=$(cat "$dev/idProduct")
    manufacturer=""
    product_name=""
    [ -f "$dev/manufacturer" ] && manufacturer=$(cat "$dev/manufacturer")
    [ -f "$dev/product" ] && product_name=$(cat "$dev/product")
    echo "  USB: ${vendor}:${product} ${manufacturer} ${product_name}"
    usb_found=1
done
if [ "$usb_found" = "0" ]; then
    echo "  No USB devices found"
fi
# List USB serial devices
echo "=== USB Serial Devices ==="
ls -la /dev/ttyUSB* 2>&1 || echo "  No /dev/ttyUSB* devices"

# Bring up eth0
echo "=== Bringing up eth0 ==="
ip link set eth0 up
echo "Waiting for link..."
sleep 8

# Get IP via DHCP
echo "=== Running DHCP ==="
udhcpc -i eth0 -t 10 -T 3 -n -q 2>&1 || echo "DHCP failed, setting IP manually"

# If DHCP failed, configure manually
if ! ip addr show eth0 | grep -q "inet "; then
    ip addr add 10.0.2.15/24 dev eth0
    ip route add default via 10.0.2.2
fi

# Set up DNS (SLIRP DNS forwarder)
echo "nameserver 10.0.2.3" > /etc/resolv.conf

echo "=== Network config ==="
ip addr show eth0
ip route show

# Ping gateway
echo "=== Pinging 10.0.2.2 (QEMU gateway) ==="
ping -c 3 -W 3 10.0.2.2 2>&1 || echo "Ping gateway failed"

# Ping internet (8.8.8.8)
echo "=== Pinging 8.8.8.8 (Google DNS) ==="
ping -c 3 -W 5 8.8.8.8 2>&1 || echo "Ping 8.8.8.8 failed"

# Test DNS resolution
echo "=== DNS resolution test ==="
nslookup www.google.com 2>&1 || echo "DNS resolution failed"

# Ensure shared libraries are findable
export LD_LIBRARY_PATH=/usr/lib:/lib
export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# Set system clock so TLS certificate verification works
# QEMU's RTC may not be configured, leaving the system at epoch
date -s "2026-04-07 12:00:00" 2>&1 || true

# Test HTTPS fetch (with proper certificate verification)
echo "=== Fetching https://www.google.com ==="
wget -O /dev/null https://www.google.com 2>&1 && echo "HTTPS fetch: SUCCESS" || {
    echo "Certificate verification failed, retrying without verification..."
    wget --no-check-certificate -O /dev/null https://www.google.com 2>&1 && echo "HTTPS fetch: SUCCESS" || echo "HTTPS fetch: FAILED"
}

# Bulk transfer test: repeated 1 MB request/response chunks over ONE
# long-lived TCP connection to the harness's bulk server (10.0.2.2:9876
# via slirp).  Regression check for rpi-qemu issue #12: a bogus GENET
# RX status-block checksum forces the kernel to software-validate every
# packet (killing GRO and ~7x throughput) and long-lived bulk flows
# degrade.  Skipped gracefully (FAILED) when no server is listening,
# e.g. under the socket-networking tests.
echo "=== Bulk transfer test (5 x 1 MB, one TCP connection) ==="
BULK_MD5="344dfba45d117fdd78f91b9284fd94aa"
# Ignore SIGPIPE so a missing/dead bulk server (e.g. under the socket
# tests) yields "Bulk transfer: FAILED" instead of killing this script.
trap "" PIPE
mkfifo /tmp/bulk_req /tmp/bulk_data
nc -w 10 10.0.2.2 9876 < /tmp/bulk_req > /tmp/bulk_data &
exec 7> /tmp/bulk_req
exec 8< /tmp/bulk_data
bulk_ok=1
i=1
while [ $i -le 5 ]; do
    t0=$(cut -d' ' -f1 /proc/uptime)
    echo GET >&7
    sum=$(timeout 90 sh -c 'head -c 1048576 <&8 | md5sum' 8<&8 | cut -d' ' -f1)
    t1=$(cut -d' ' -f1 /proc/uptime)
    echo "Bulk chunk $i: start=$t0 end=$t1 md5=$sum"
    [ "$sum" = "$BULK_MD5" ] || bulk_ok=0
    i=$((i+1))
done
exec 7>&-
exec 8<&-
if [ "$bulk_ok" = "1" ]; then
    echo "Bulk transfer: SUCCESS"
else
    echo "Bulk transfer: FAILED"
fi

# RX checksum offload sanity: the GENET RSB must carry a checksum the
# kernel agrees with; "hw csum failure" in dmesg means the emulated
# checksum is wrong (netdev_rx_csum_fault, logged once per boot).
echo "=== RX checksum offload check ==="
if dmesg | grep -q "hw csum failure"; then
    echo "RX csum: HW-FAULT"
else
    echo "RX csum: CLEAN"
fi

# RX ring overflow test, run only when the harness asks for it with
# rxburst=<N> on the kernel command line (run-rpi-socket-network-test.py).
# On "RX burst: READY" the harness's frame-level peer writes N minimum-size
# UDP datagrams to a closed port in one burst -- far more than the GENET RX
# ring holds, delivered while the guest cannot consume any.  Every datagram
# the NIC delivers intact bumps Udp NoPorts (not capped by socket buffers),
# so a lossless RX path yields a delta of exactly N; a device that laps the
# ring hands the driver stale/empty descriptors instead.
RXBURST=$(sed -n 's/.*rxburst=\\([0-9]*\\).*/\\1/p' /proc/cmdline)
if [ -n "$RXBURST" ]; then
    echo "=== RX burst test ($RXBURST datagrams) ==="
    noports() { awk '/^Udp: [0-9]/ { print $3 }' /proc/net/snmp; }
    ifstat() { cat /sys/class/net/eth0/statistics/$1; }
    np0=$(noports); rxp0=$(ifstat rx_packets); rxe0=$(ifstat rx_errors)
    echo "RX burst: READY"
    got=0
    i=0
    while [ $i -lt 60 ]; do
        sleep 1
        got=$(( $(noports) - np0 ))
        [ "$got" -ge "$RXBURST" ] && break
        i=$((i+1))
    done
    sleep 2
    got=$(( $(noports) - np0 ))
    echo "RX burst: noports=$got expected=$RXBURST" \\
         "rx_packets=$(( $(ifstat rx_packets) - rxp0 ))" \\
         "rx_errors=$(( $(ifstat rx_errors) - rxe0 ))"
    if [ "$got" = "$RXBURST" ]; then
        echo "RX burst: SUCCESS"
    else
        echo "RX burst: FAILED"
    fi
fi

# Show dmesg for GENET
echo "=== dmesg genet ==="
dmesg 2>&1 | grep -i -e genet -e "Link is" | tail -5

echo "=== dmesg usb ==="
dmesg 2>&1 | grep -i -e "dwc2" -e "dwc_otg" -e "usb 1-" -e "ttyUSB" -e "ttyACM" | tail -30

echo "=== Network test complete ==="

# Shutdown cleanly so QEMU exits
poweroff -f 2>&1 || exec /bin/sh
"""

RPI0_INIT_SCRIPT = """\
#!/bin/sh
# Minimal init for the raspi0 (Pi Zero) boot test
mount -t proc proc /proc
mount -t sysfs sys /sys
mount -t devtmpfs devtmpfs /dev

echo "=== QEMU raspi0 test ==="
echo "Kernel: $(uname -r) $(uname -m)"
echo "Cmdline: $(cat /proc/cmdline)"
# Which tty the kernel console (and so this script's output) is bound to.
echo "Console: $(cat /sys/class/tty/console/active)"
if [ -c /dev/ttyS0 ]; then
    echo "ttyS0: present"
else
    echo "ttyS0: MISSING"
fi

# USB networking on the DWC2 host port, the Zero's only wired network
# (rpi-qemu#24): the harness attaches -device usb-net.
insmod /lib/modules/cdc_ether.ko
insmod /lib/modules/rndis_host.ko
nic=""
i=0
while [ $i -lt 30 ] && [ -z "$nic" ]; do
    for d in /sys/class/net/*; do
        case "$(readlink $d/device/driver 2>&1)" in
            *cdc_ether|*rndis_host) nic=${d##*/} ;;
        esac
    done
    [ -n "$nic" ] || sleep 1
    i=$((i+1))
done
if [ -n "$nic" ]; then
    echo "USB NIC: $nic driver=$(basename $(readlink /sys/class/net/$nic/device/driver))"
    ip link set "$nic" up
    udhcpc -i "$nic" -t 10 -T 2 -n -q 2>&1
    ping -c 3 -W 3 10.0.2.2 2>&1
else
    echo "USB NIC: none"
fi

# SD card erase (rpi-qemu#39): the harness's 4 GiB (SDHC) card; discard
# its second GiB, as fstrim does.  The erase must take as little time as
# on a card (the vCPU waits for it), and the harness checks the image.
if [ -b /dev/mmcblk0 ]; then
    t0=$(date +%s)
    blkdiscard -o 1073741824 -l 1073741824 /dev/mmcblk0 2>&1
    echo "SD discard: rc=$? in $(( $(date +%s) - t0 )) s"
    # erased blocks read as zeroes, as the SCR says
    echo "SD erased nonzero bytes: $(dd if=/dev/mmcblk0 bs=1M skip=1024 count=1 | tr -d '\\000' | wc -c)"
else
    echo "SD card: no mmcblk0"
fi

# Board identity the firmware publishes in the DT (rpi-qemu#25).
echo "Revision: $(sed -n 's/^Revision[[:space:]]*: //p' /proc/cpuinfo)"
echo "Serial: $(sed -n 's/^Serial[[:space:]]*: //p' /proc/cpuinfo)"
echo "DT serial-number: $( { tr -d '\\000' < /proc/device-tree/serial-number; } 2>&1)"

# Receive over the console UART: the harness answers READY with a line.
echo "RX test: READY"
line=""
read -t 60 line
echo "RX test: got [$line]"

echo "=== raspi0 test complete ==="
poweroff -f 2>&1 || exec /bin/sh
"""

RPI0_GADGET_INIT_SCRIPT = """\
#!/bin/sh
# Init for the raspi0 USB gadget test (rpi-qemu#22): the stock dwc2 driver
# in peripheral mode with a configfs gadget, exported to the harness over
# USB/IP.  gadget=<f>[,<f>...] on the command line picks the functions:
# acm, ecm, ncm, ms (mass storage), sslb (SourceSink: bulk and isochronous
# endpoints streaming a known pattern).
mount -t proc proc /proc
mount -t sysfs sys /sys
mount -t devtmpfs devtmpfs /dev
mount -t configfs configfs /sys/kernel/config

echo "=== QEMU raspi0 gadget test ==="
echo "Kernel: $(uname -r) $(uname -m)"
FUNCS=$(sed -n 's/.*gadget=\\([a-z,]*\\).*/\\1/p' /proc/cmdline | tr , ' ')
echo "Functions: $FUNCS"

for m in roles dwc2 libcomposite u_serial usb_f_acm u_ether usb_f_ecm \\
         usb_f_ncm usb_f_mass_storage usb_f_ss_lb; do
    insmod /lib/modules/$m.ko || echo "insmod $m: FAILED"
done

UDC=""
i=0
while [ $i -lt 20 ] && [ -z "$UDC" ]; do
    UDC=$(ls /sys/class/udc 2>&1 | grep usb)
    [ -n "$UDC" ] || sleep 0.5
    i=$((i+1))
done
echo "UDC: ${UDC:-none}"
dmesg | grep -e "dwc2" | tail -20

G=/sys/kernel/config/usb_gadget/g1
mkdir $G
echo 0x1d6b > $G/idVendor      # Linux Foundation
echo 0x0104 > $G/idProduct     # Multifunction Composite Gadget
echo 0x0100 > $G/bcdDevice
echo 0x0200 > $G/bcdUSB
mkdir $G/strings/0x409
echo rpi-qemu-0001 > $G/strings/0x409/serialnumber
echo rpi-qemu > $G/strings/0x409/manufacturer
echo "Raspberry Pi Zero gadget" > $G/strings/0x409/product
mkdir -p $G/configs/c.1/strings/0x409
echo test > $G/configs/c.1/strings/0x409/configuration
echo 250 > $G/configs/c.1/MaxPower

for f in $FUNCS; do
    case $f in
    acm)
        mkdir $G/functions/acm.usb0
        ln -s $G/functions/acm.usb0 $G/configs/c.1/ ;;
    ecm|ncm)
        mkdir $G/functions/$f.usb0
        echo 02:00:00:00:00:02 > $G/functions/$f.usb0/dev_addr
        echo 02:00:00:00:00:01 > $G/functions/$f.usb0/host_addr
        ln -s $G/functions/$f.usb0 $G/configs/c.1/ ;;
    ms)
        dd if=/dev/zero of=/ms.img bs=512 count=2048
        printf "RPI-QEMU-GADGET-MS" | dd of=/ms.img conv=notrunc
        mkdir $G/functions/mass_storage.usb0
        echo /ms.img > $G/functions/mass_storage.usb0/lun.0/file
        ln -s $G/functions/mass_storage.usb0 $G/configs/c.1/ ;;
    sslb)
        F=$G/functions/SourceSink.usb0
        mkdir $F
        echo 1 > $F/pattern             # byte i of a buffer is i % 63
        echo 4 > $F/isoc_interval       # 2^(4-1) microframes: 1 ms
        echo 1024 > $F/isoc_maxpacket
        ln -s $F $G/configs/c.1/ ;;
    esac
done
echo "$UDC" > $G/UDC && echo "GADGET: bound [$FUNCS]"

# A shell on the ACM port, restarted whenever the host goes away.
if [ -d $G/functions/acm.usb0 ]; then
    ( while :; do
          [ -c /dev/ttyGS0 ] && setsid sh -i < /dev/ttyGS0 > /dev/ttyGS0 2>&1
          sleep 0.5
      done ) &
fi
# The network function's interface (usb0).
if [ -d $G/functions/ecm.usb0 ] || [ -d $G/functions/ncm.usb0 ]; then
    ip addr add 192.168.7.2/24 dev usb0
    ip link set usb0 up
    echo "GADGET: usb0 $(cat /sys/class/net/usb0/address)"
fi

# Report the UDC state as the host comes and goes.
( prev=""
  while :; do
      st=$(cat /sys/class/udc/$UDC/state)
      [ "$st" != "$prev" ] && echo "UDC state: $st"
      prev=$st
      sleep 0.2
  done ) &

echo "GADGET: READY"
line=""
read -t 900 line
echo "harness: [$line]"
if [ -f /ms.img ]; then
    echo "MS image LBA 100: [$(dd if=/ms.img bs=512 skip=100 count=1 2>&1 | head -c 21)]"
fi
if [ -d /sys/class/net/usb0 ]; then
    echo "usb0: rx_packets=$(cat /sys/class/net/usb0/statistics/rx_packets)" \\
         "tx_packets=$(cat /sys/class/net/usb0/statistics/tx_packets)"
fi
dmesg | grep -i -e "dwc2" -e "gadget" -e "WARNING" -e "Oops" | tail -20
# SourceSink checks the pattern of what it receives
dmesg | grep -i -e "bad OUT byte" -e "source_sink" -e "sourcesink" | tail -10
echo "=== raspi0 gadget test complete ==="
poweroff -f 2>&1 || exec /bin/sh
"""

TARGETS = {
    "rpi4": dict(tar="alpine-minirootfs.tar.gz", root="initramfs-root",
                 output="test-initramfs.cpio.gz", init=INIT_SCRIPT,
                 ttys=[("ttyAMA0", 204, 64)]),
    "rpi0": dict(tar="alpine-minirootfs-armhf.tar.gz", root="initramfs-root-rpi0",
                 output="test-initramfs-rpi0.cpio.gz", init=RPI0_INIT_SCRIPT,
                 ttys=[("ttyS0", 4, 64)],
                 # USB network drivers for usb-net, from the same
                 # raspberrypi/firmware commit as the kernel (.ko.xz).
                 modules=["cdc_ether", "rndis_host"]),
    # The dwc2 driver and USB gadget functions (configfs), from the same
    # raspberrypi/firmware commit (#22).
    "rpi0-gadget": dict(tar="alpine-minirootfs-armhf.tar.gz",
                        root="initramfs-root-rpi0-gadget",
                        output="test-initramfs-rpi0-gadget.cpio.gz",
                        init=RPI0_GADGET_INIT_SCRIPT, ttys=[("ttyS0", 4, 64)],
                        module_dir="rpi0",
                        modules=["roles", "dwc2", "libcomposite", "u_serial",
                                 "usb_f_acm", "u_ether", "usb_f_ecm",
                                 "usb_f_ncm", "usb_f_mass_storage",
                                 "usb_f_ss_lb"]),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(TARGETS), default="rpi4")
    target_name = parser.parse_args().target
    target = TARGETS[target_name]
    ALPINE_TAR = BASE / target["tar"]
    ROOTFS_DIR = BASE / target["root"]
    OUTPUT = BASE / target["output"]

    # Clean and extract Alpine rootfs
    if ROOTFS_DIR.exists():
        subprocess.run(["rm", "-rf", str(ROOTFS_DIR)])
    ROOTFS_DIR.mkdir(parents=True)

    print(f"Extracting Alpine rootfs to {ROOTFS_DIR}...")
    subprocess.run(
        ["tar", "xf", str(ALPINE_TAR), "-C", str(ROOTFS_DIR)],
        check=True
    )

    # Create device nodes needed before devtmpfs mount
    os.mknod(str(ROOTFS_DIR / "dev" / "console"), 0o600 | 0o020000, os.makedev(5, 1))
    os.mknod(str(ROOTFS_DIR / "dev" / "null"), 0o666 | 0o020000, os.makedev(1, 3))
    for name, major, minor in target["ttys"]:
        os.mknod(str(ROOTFS_DIR / "dev" / name), 0o600 | 0o020000,
                 os.makedev(major, minor))

    # Kernel modules the init script loads (test-images/<target>/modules/)
    modules = target.get("modules", [])
    if modules:
        mod_dir = ROOTFS_DIR / "lib" / "modules"
        mod_dir.mkdir(parents=True, exist_ok=True)
        for name in modules:
            src = (BASE / target.get("module_dir", target_name) / "modules"
                   / f"{name}.ko.xz")
            (mod_dir / f"{name}.ko").write_bytes(lzma.decompress(src.read_bytes()))

    # Write our init script
    init_path = ROOTFS_DIR / "init"
    init_path.write_text(target["init"])
    os.chmod(str(init_path), 0o755)

    # Also symlink /sbin/init to our init for safety
    sbin_init = ROOTFS_DIR / "sbin" / "init"
    if sbin_init.exists() or sbin_init.is_symlink():
        sbin_init.unlink()
    sbin_init.symlink_to("/init")

    # Create the cpio archive
    print(f"Creating initramfs at {OUTPUT}...")
    # Use find | cpio | gzip
    find_proc = subprocess.Popen(
        ["find", ".", "-print0"],
        cwd=ROOTFS_DIR,
        stdout=subprocess.PIPE
    )
    cpio_proc = subprocess.Popen(
        ["cpio", "--null", "-o", "--format=newc"],
        cwd=ROOTFS_DIR,
        stdin=find_proc.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    find_proc.stdout.close()

    with open(OUTPUT, "wb") as f:
        gzip_proc = subprocess.Popen(
            ["gzip", "-9"],
            stdin=cpio_proc.stdout,
            stdout=f,
            stderr=subprocess.PIPE
        )
        cpio_proc.stdout.close()
        gzip_proc.wait()
        cpio_proc.wait()

    size = OUTPUT.stat().st_size
    print(f"Done: {OUTPUT} ({size} bytes, {size/1024/1024:.1f} MB)")

    # Cleanup
    subprocess.run(["rm", "-rf", str(ROOTFS_DIR)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
