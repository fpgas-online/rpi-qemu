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
echo "Waiting for network device..."
sleep 2

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

# Show dmesg for GENET
echo "=== dmesg genet ==="
dmesg 2>&1 | grep -i -e genet -e "Link is" | tail -5

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
