#!/usr/bin/env python3
"""Build a minimal aarch64 initramfs for network testing on QEMU raspi4b."""

import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent.resolve() / "test-images"
ALPINE_TAR = BASE / "alpine-minirootfs.tar.gz"
ROOTFS_DIR = BASE / "initramfs-root"
OUTPUT = BASE / "test-initramfs.cpio.gz"

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
ls -la /dev/ttyUSB* 2>/dev/null || echo "  No /dev/ttyUSB* devices"

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

# Show dmesg for GENET
echo "=== dmesg genet ==="
dmesg 2>&1 | grep -i -e genet -e "Link is" | tail -5

echo "=== dmesg usb ==="
dmesg 2>&1 | grep -i -e "dwc2" -e "usb 1-" -e "ttyUSB" | tail -10

echo "=== Network test complete ==="

# Shutdown cleanly so QEMU exits
poweroff -f 2>&1 || exec /bin/sh
"""

def main():
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
    os.mknod(str(ROOTFS_DIR / "dev" / "ttyAMA0"), 0o600 | 0o020000, os.makedev(204, 64))

    # Write our init script
    init_path = ROOTFS_DIR / "init"
    init_path.write_text(INIT_SCRIPT)
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
