#!/usr/bin/env python3
"""
RPi4B QEMU Single-Instance Boot Test: U-Boot → TFTP → booti → Linux → Internet

End-to-end test of Raspberry Pi 4B emulation in QEMU with GENET Ethernet:
1. U-Boot boots and gets DHCP address
2. U-Boot downloads kernel, DTB, and initrd via TFTP
3. U-Boot boots Linux via booti (kernel launched by U-Boot, not QEMU)
4. Linux boots with GENET networking
5. Linux gets DHCP, pings 8.8.8.8, and fetches https://www.google.com

All in a single QEMU instance with zero human interaction.

Prerequisites:
  - QEMU with GENET patches: install qemu-rpi-system-arm package,
    or set QEMU_OVERRIDE env var to a custom binary path
  - U-Boot built with rpi_4_qemu_defconfig: test-images/u-boot/u-boot.bin
  - Stock RPi kernel (uncompressed): test-images/tftpboot/Image
  - BCM2711 device tree: test-images/tftpboot/bcm2711-rpi-4-b.dtb
  - Initramfs with network tools: test-images/tftpboot/initrd.gz

Usage: uv run run-rpi-boot-test.py
"""

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).parent.resolve()
# QEMU binary: QEMU_OVERRIDE env var, or APT-installed qemu-rpi-system-aarch64
_qemu_override = os.environ.get("QEMU_OVERRIDE")
if _qemu_override:
    QEMU = Path(_qemu_override)
else:
    QEMU = Path(shutil.which("qemu-rpi-system-aarch64") or
                "qemu-rpi-system-aarch64")
UBOOT = BASE / "test-images" / "u-boot" / "u-boot.bin"
DTB = BASE / "test-images" / "bcm2711-rpi-4-b.dtb"
INITRD = BASE / "test-images" / "test-initramfs.cpio.gz"
TFTPBOOT = BASE / "test-images" / "tftpboot"

# Memory layout for TFTP loads (avoid overlap with kernel relocation to 0x0)
KERNEL_ADDR = 0x10000000   # 256 MB - uncompressed Image loaded here
DTB_ADDR    = 0x0f000000   # 240 MB - device tree
INITRD_ADDR = 0x12000000   # 288 MB - initramfs

# Kernel boot parameters
BOOTARGS = "earlycon=pl011,mmio32,0xfe201000 console=ttyAMA0 loglevel=4 rdinit=/init"


def check_prerequisites():
    """Verify all required files exist."""
    missing = []
    for name, path in [
        ("QEMU (custom build with GENET)", QEMU),
        ("U-Boot (rpi_4_qemu_defconfig)", UBOOT),
        ("DTB", DTB),
        ("Uncompressed kernel Image", TFTPBOOT / "Image"),
        ("Initramfs", INITRD),
    ]:
        if not path.exists():
            missing.append(f"  {name}: {path}")
    if missing:
        print("Missing prerequisites:")
        print("\n".join(missing))
        return False
    return True


def setup_tftpboot():
    """Populate the TFTP boot directory with required files."""
    TFTPBOOT.mkdir(parents=True, exist_ok=True)

    if not (TFTPBOOT / "Image").exists():
        print("ERROR: Uncompressed Image not found in tftpboot/")
        print("  The kernel must be an uncompressed ARM64 Image for booti")
        return False

    for src, name in [
        (DTB, "bcm2711-rpi-4-b.dtb"),
        (INITRD, "initrd.gz"),
    ]:
        dst = TFTPBOOT / name
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dst)

    return True


def run_test():
    """Run the single-instance boot test."""
    print("=" * 70)
    print("RPi4B QEMU Single-Instance Boot Test")
    print("  U-Boot → DHCP → TFTP → booti → Linux → Internet")
    print("=" * 70)

    proc = subprocess.Popen(
        [str(QEMU), "-M", "raspi4b",
         "-kernel", str(UBOOT), "-dtb", str(DTB),
         "-nic", f"user,tftp={TFTPBOOT}",
         "-serial", "stdio",
         "-display", "none", "-monitor", "none"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)

    out_lines = []
    start = time.time()

    def read_stdout():
        for line in iter(proc.stdout.readline, ''):
            out_lines.append(line)

    def read_stderr():
        for line in iter(proc.stderr.readline, ''):
            pass  # Suppress QEMU stderr noise

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()

    def send(cmd, wait=2):
        """Send a command to U-Boot serial console."""
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()
        time.sleep(wait)

    def wait_for(pattern, timeout=30, label=""):
        """Wait for a pattern to appear in the output."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = "".join(out_lines)
            if pattern in text:
                return True
            time.sleep(0.5)
        return False

    try:
        # === Phase 1: U-Boot startup ===
        print("\n--- Phase 1: U-Boot startup ---")
        time.sleep(6)

        # Interrupt autoboot
        for _ in range(5):
            proc.stdin.write(" ")
            proc.stdin.flush()
            time.sleep(0.3)
        time.sleep(2)

        # === Phase 2: DHCP + TFTP ===
        print("--- Phase 2: DHCP + TFTP ---")
        send("dhcp", 3)
        wait_for("DHCP client bound", timeout=15, label="DHCP")

        send(f"tftpboot 0x{KERNEL_ADDR:x} Image", 3)
        wait_for("Bytes transferred", timeout=30, label="TFTP kernel")

        send(f"tftpboot 0x{DTB_ADDR:x} bcm2711-rpi-4-b.dtb", 3)
        wait_for("Bytes transferred", timeout=15, label="TFTP DTB")

        send(f"tftpboot 0x{INITRD_ADDR:x} initrd.gz", 3)
        wait_for("Bytes transferred", timeout=15, label="TFTP initrd")

        # === Phase 3: FDT setup + booti ===
        print("--- Phase 3: FDT setup + booti ---")
        send(f"fdt addr 0x{DTB_ADDR:x}", 2)
        send("fdt resize 8192", 2)
        send(f'setenv bootargs "{BOOTARGS}"', 2)
        # Note: ${filesize} is set by the LAST tftpboot (initrd.gz)
        send(f"booti 0x{KERNEL_ADDR:x} 0x{INITRD_ADDR:x}:${{filesize}} 0x{DTB_ADDR:x}", 3)

        # === Phase 4: Wait for Linux to complete network tests ===
        print("--- Phase 4: Waiting for Linux boot + network tests ---")

        # Wait for the init script to complete (up to 120 seconds)
        if not wait_for("Network test complete", timeout=120, label="network tests"):
            print("  TIMEOUT waiting for network tests!")

    finally:
        elapsed = time.time() - start
        print(f"\n--- Terminating QEMU after {elapsed:.1f}s ---")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    # === Results ===
    text = "".join(out_lines)

    # Required checks (must all pass)
    checks = [
        ("U-Boot DHCP",         "DHCP client bound"),
        ("TFTP transfers",      "Bytes transferred"),
        ("booti starts kernel", "Starting kernel"),
        ("Kernel boots",        "Booting Linux on physical CPU"),
        ("GENET driver",        "bcmgenet"),
        ("Link up",             "Link is Up"),
        ("DHCP lease",          "lease of"),
        ("HTTPS fetch",         "HTTPS fetch: SUCCESS"),
    ]
    # Optional checks (reported but don't fail the test)
    # Ping may fail in CI environments that block ICMP
    optional_checks = [
        ("Ping 8.8.8.8",       "bytes from 8.8.8.8"),
    ]

    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)

    all_pass = True
    for name, pattern in checks:
        found = pattern in text
        if not found:
            all_pass = False
        print(f"  [{'PASS' if found else 'FAIL'}] {name}")

    for name, pattern in optional_checks:
        found = pattern in text
        status = "PASS" if found else "SKIP"
        print(f"  [{status}] {name} (optional)")

    # Print key output lines
    print()
    for line in text.split("\n"):
        s = line.strip()
        for kw in ["DHCP client bound", "Bytes transferred",
                    "Starting kernel", "Booting Linux",
                    "bcmgenet", "Link is Up", "lease of",
                    "64 bytes from", "HTTPS fetch",
                    "Network test complete"]:
            if kw in s:
                print(f"  > {s[:130]}")
                break

    print()
    if all_pass:
        print("  ALL TESTS PASSED")
        print("  Single QEMU instance: U-Boot → TFTP → booti → Linux → Internet")
    else:
        print("  SOME TESTS FAILED")

    print("=" * 70)
    return 0 if all_pass else 1


def main():
    if not check_prerequisites():
        return 1
    if not setup_tftpboot():
        return 1
    return run_test()


if __name__ == "__main__":
    sys.exit(main())
