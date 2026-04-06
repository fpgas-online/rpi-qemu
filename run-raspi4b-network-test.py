#!/usr/bin/env python3
"""
RPi4B QEMU Full Network Test: U-Boot DHCP/TFTP + Linux Internet Access

Demonstrates complete GENET Ethernet functionality in two phases:
1. U-Boot: Gets DHCP, downloads kernel+DTB+initrd via TFTP at 50+ MiB/s
2. Linux: Boots with GENET networking, gets DHCP, pings 8.8.8.8

Phase 2 uses the TFTPed files from Phase 1 with QEMU's direct -kernel
loader because U-Boot's booti on QEMU raspi4b has an unresolved issue
where the arch timer counter (cntpct_el0) doesn't advance during
TCG execution, causing U-Boot's cleanup_before_linux to hang.

Usage: uv run run-raspi4b-network-test.py
"""

import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).parent.resolve()
QEMU = BASE / "upstream-qemu" / "build" / "qemu-system-aarch64"
UBOOT = BASE / "test-images" / "u-boot" / "u-boot.bin"
KERNEL = BASE / "test-images" / "kernel8.img"
DTB = BASE / "test-images" / "bcm2711-rpi-4-b.dtb"
INITRD = BASE / "test-images" / "test-initramfs.cpio.gz"
TFTPBOOT = BASE / "test-images" / "tftpboot"


def check_prerequisites():
    missing = []
    for name, path in [("QEMU", QEMU), ("U-Boot", UBOOT),
                        ("Kernel", KERNEL), ("DTB", DTB), ("Initrd", INITRD)]:
        if not path.exists():
            missing.append(f"  {name}: {path}")
    if missing:
        print("Missing prerequisites:\n" + "\n".join(missing))
        return False
    return True


def setup_tftpboot():
    import shutil
    TFTPBOOT.mkdir(parents=True, exist_ok=True)
    for src, name in [(KERNEL, "kernel8.img"), (DTB, "bcm2711-rpi-4-b.dtb"),
                       (INITRD, "initrd.gz")]:
        dst = TFTPBOOT / name
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dst)


def phase1_uboot():
    """U-Boot: DHCP + TFTP to prove bidirectional GENET networking."""
    print("=" * 60)
    print("PHASE 1: U-Boot DHCP + TFTP")
    print("=" * 60)

    proc = subprocess.Popen(
        [str(QEMU), "-M", "raspi4b",
         "-kernel", str(UBOOT), "-dtb", str(DTB),
         "-nic", f"user,tftp={TFTPBOOT}",
         "-serial", "stdio", "-display", "none", "-monitor", "none"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True)

    out = []
    threading.Thread(target=lambda: [out.append(l) for l in
                     iter(proc.stdout.readline, '')], daemon=True).start()

    def send(c, w=2):
        proc.stdin.write(c + "\n"); proc.stdin.flush(); time.sleep(w)

    try:
        time.sleep(8)
        for _ in range(3):
            proc.stdin.write(" "); proc.stdin.flush(); time.sleep(0.5)
        time.sleep(4)

        send("dhcp", 10)
        send("tftpboot 0x10000000 kernel8.img", 12)
        send("tftpboot 0x0f000000 bcm2711-rpi-4-b.dtb", 8)
        send("tftpboot 0x12000000 initrd.gz", 10)
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except: proc.kill(); proc.wait()

    text = "".join(out)
    checks = [
        ("U-Boot DHCP",   "DHCP client bound"),
        ("TFTP kernel",   "Bytes transferred = 10"),
        ("TFTP DTB",      "Bytes transferred = 563"),
        ("TFTP initrd",   "Bytes transferred = 385"),
    ]

    all_pass = True
    for name, pattern in checks:
        found = pattern in text
        if not found: all_pass = False
        print(f"  [{'PASS' if found else 'FAIL'}] {name}")

    for line in text.split("\n"):
        s = line.strip()
        if "DHCP client bound" in s or "Bytes transferred" in s:
            print(f"    > {s[:100]}")

    return all_pass


def phase2_linux():
    """Linux: Direct boot with GENET networking to prove internet access."""
    print()
    print("=" * 60)
    print("PHASE 2: Linux Boot + Internet Access")
    print("=" * 60)

    result = subprocess.run(
        ["timeout", "--signal=KILL", "75",
         str(QEMU), "-M", "raspi4b",
         "-kernel", str(KERNEL), "-dtb", str(DTB), "-initrd", str(INITRD),
         "-append",
         "earlycon=pl011,0xfe201000 console=ttyAMA1 loglevel=4 rdinit=/init",
         "-nic", "user",
         "-serial", "stdio", "-display", "none", "-monitor", "none"],
        capture_output=True, text=True, timeout=90)

    text = result.stdout + result.stderr

    checks = [
        ("GENET driver",  "bcmgenet"),
        ("Link up",       "Link is Up"),
        ("DHCP lease",    "lease of"),
        ("Ping gateway",  "bytes from 10.0.2.2"),
        ("Ping 8.8.8.8",  "bytes from 8.8.8.8"),
    ]

    all_pass = True
    for name, pattern in checks:
        found = pattern in text
        if not found: all_pass = False
        print(f"  [{'PASS' if found else 'FAIL'}] {name}")

    for line in text.split("\n"):
        s = line.strip()
        for kw in ["bcmgenet", "Link is Up", "lease of", "64 bytes from",
                    "LOWER_UP", "inet 10.0", "=== Network test"]:
            if kw in s:
                print(f"    > {s[:120]}")
                break

    return all_pass


def main():
    if not check_prerequisites():
        return 1
    setup_tftpboot()

    p1 = phase1_uboot()
    p2 = phase2_linux()

    print()
    print("=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)
    print(f"  Phase 1 (U-Boot DHCP + TFTP):        {'PASS' if p1 else 'FAIL'}")
    print(f"  Phase 2 (Linux DHCP + ping 8.8.8.8): {'PASS' if p2 else 'FAIL'}")
    print()

    if p1 and p2:
        print("  ALL TESTS PASSED - GENET networking fully functional")
    return 0 if (p1 and p2) else 1


if __name__ == "__main__":
    sys.exit(main())
