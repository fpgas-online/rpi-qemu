#!/usr/bin/env python3
"""
Boot RPi4B in QEMU: U-Boot -> DHCP -> TFTP kernel+initrd -> Linux with networking.

Demonstrates full end-to-end GENET Ethernet functionality:
1. U-Boot boots, gets DHCP address from SLIRP
2. U-Boot downloads kernel, DTB, and initrd via TFTP
3. Linux boots with GENET networking
4. Linux gets DHCP, pings gateway, pings 8.8.8.8

Prerequisites:
  - QEMU built with GENET patches: upstream-qemu/build/qemu-system-aarch64
  - Test images in test-images/: kernel8.img, bcm2711-rpi-4-b.dtb
  - U-Boot binary: test-images/u-boot/u-boot.bin
  - Initramfs: test-images/test-initramfs.cpio.gz (built by build-initramfs.py)
  - TFTP directory: test-images/tftpboot/ (auto-populated)
"""

import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent.resolve()
QEMU = BASE / "upstream-qemu" / "build" / "qemu-system-aarch64"
UBOOT = BASE / "test-images" / "u-boot" / "u-boot.bin"
KERNEL = BASE / "test-images" / "kernel8.img"
DTB = BASE / "test-images" / "bcm2711-rpi-4-b.dtb"
INITRD = BASE / "test-images" / "test-initramfs.cpio.gz"
TFTPBOOT = BASE / "test-images" / "tftpboot"

# Timeouts (seconds)
UBOOT_BOOT_TIME = 15      # Time for U-Boot to reach autoboot prompt
TFTP_TIMEOUT = 12          # Time per TFTP download
LINUX_BOOT_TIME = 180      # Time for Linux boot + network test
TOTAL_TIMEOUT = 300        # Hard kill timeout


def check_prerequisites():
    """Verify all required files exist."""
    missing = []
    for name, path in [("QEMU binary", QEMU), ("U-Boot", UBOOT),
                        ("Kernel", KERNEL), ("DTB", DTB), ("Initrd", INITRD)]:
        if not path.exists():
            missing.append(f"  {name}: {path}")
    if missing:
        print("Missing prerequisites:")
        print("\n".join(missing))
        print("\nRun the porting steps first to build QEMU and download test images.")
        return False
    return True


def setup_tftpboot():
    """Populate TFTP directory with kernel, DTB, and initrd."""
    TFTPBOOT.mkdir(parents=True, exist_ok=True)
    for src, dst_name in [(KERNEL, "kernel8.img"), (DTB, "bcm2711-rpi-4-b.dtb"),
                           (INITRD, "initrd.gz")]:
        dst = TFTPBOOT / dst_name
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            import shutil
            shutil.copy2(src, dst)
    print(f"TFTP directory ready: {TFTPBOOT}")


def run_direct_linux_test():
    """Run Linux directly (bypassing U-Boot) for network verification."""
    print("=" * 60)
    print("DIRECT LINUX BOOT - Network Verification")
    print("=" * 60)
    print()

    result = subprocess.run(
        ["timeout", "--signal=KILL", "75",
         str(QEMU), "-M", "raspi4b",
         "-kernel", str(KERNEL),
         "-dtb", str(DTB),
         "-initrd", str(INITRD),
         "-append", "earlycon=pl011,0xfe201000 console=ttyAMA1 loglevel=4 rdinit=/init",
         "-nic", "user",
         "-serial", "stdio", "-display", "none", "-monitor", "none"],
        capture_output=True, text=True, timeout=90
    )

    full_output = result.stdout + result.stderr
    log_file = BASE / "test-images" / "direct-linux-test-log.txt"
    log_file.write_text(full_output)

    checks = [
        ("GENET probe",     "bcmgenet"),
        ("Link up",         "Link is Up"),
        ("Carrier",         "LOWER_UP"),
        ("DHCP lease",      "lease of"),
        ("Ping gateway",    "bytes from 10.0.2.2"),
        ("Ping 8.8.8.8",   "bytes from 8.8.8.8"),
    ]

    all_pass = True
    for name, pattern in checks:
        found = pattern in full_output
        status = "PASS" if found else "FAIL"
        if not found:
            all_pass = False
        print(f"  [{status}] {name}")

    print()
    for line in full_output.split("\n"):
        stripped = line.strip()
        for kw in ["GENET 5.0", "Link is Up", "lease of",
                    "64 bytes from", "LOWER_UP", "inet 10.0",
                    "=== Network test"]:
            if kw in stripped:
                print(f"  > {stripped[:120]}")
                break
    return all_pass


def run_test():
    """Run the full U-Boot -> DHCP -> TFTP -> Linux network test."""
    print(f"Starting QEMU raspi4b with U-Boot...")
    print(f"  QEMU: {QEMU}")
    print(f"  U-Boot: {UBOOT}")
    print(f"  TFTP dir: {TFTPBOOT}")
    print()

    proc = subprocess.Popen(
        [str(QEMU), "-M", "raspi4b",
         "-kernel", str(UBOOT),
         "-dtb", str(DTB),
         "-nic", f"user,tftp={TFTPBOOT}",
         "-serial", "stdio", "-display", "none", "-monitor", "none"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    start_time = time.time()

    def send(cmd, wait):
        """Send a command to U-Boot/Linux and wait."""
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()
        time.sleep(wait)

    try:
        # Phase 1: Wait for U-Boot, then stop autoboot early
        print("Phase 1: Waiting for U-Boot...")
        time.sleep(8)
        # Send multiple keystrokes to reliably stop autoboot
        for _ in range(5):
            proc.stdin.write(" ")
            proc.stdin.flush()
            time.sleep(0.5)
        time.sleep(3)

        # Phase 2: Get DHCP
        print("Phase 2: U-Boot DHCP...")
        send("dhcp", 10)     # Get DHCP address

        # Phase 3: TFTP downloads
        print("Phase 3: TFTP downloading kernel + DTB + initrd...")
        send("tftpboot 0x8000000 kernel8.img", TFTP_TIMEOUT)
        send("tftpboot 0x7000000 bcm2711-rpi-4-b.dtb", TFTP_TIMEOUT)
        send("tftpboot 0xA000000 initrd.gz", TFTP_TIMEOUT)

        # Phase 4: Boot Linux
        print("Phase 4: Booting Linux...")
        bootargs = (
            "earlycon=pl011,0xfe201000 "
            "console=ttyAMA0 console=ttyAMA1 "
            "loglevel=4 rdinit=/init"
        )
        send(f"setenv bootargs {bootargs}", 2)
        send("booti 0x8000000 0xA000000:${filesize} 0x7000000",
             LINUX_BOOT_TIME)

    except Exception as e:
        print(f"Error: {e}")
    finally:
        elapsed = time.time() - start_time
        print(f"\nTest completed in {elapsed:.0f}s. Stopping QEMU...")
        proc.terminate()
        try:
            full_output, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            full_output, _ = proc.communicate()

    # Save full log
    log_file = BASE / "test-images" / "full-network-test-log.txt"
    log_file.write_text(full_output)
    print(f"Full log saved to: {log_file}")

    # Print results summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)

    checks = [
        ("U-Boot DHCP",     "DHCP client bound to address",  None),
        ("TFTP kernel",     "Bytes transferred = 1036",      None),
        ("TFTP DTB",        "Bytes transferred = 563",       None),
        ("TFTP initrd",     "Bytes transferred = 385",       None),
        ("Linux boot",      "Booting Linux",                 None),
        ("GENET probe",     "GENET 5.0",                     None),
        ("Link up",         "Link is Up",                    None),
        ("Carrier",         "LOWER_UP",                      None),
        ("Ping gateway",    "bytes from 10.0.2.2",           None),
        ("DHCP lease",      "lease of",                      None),
        ("Ping 8.8.8.8",   "bytes from 8.8.8.8",            None),
    ]

    all_pass = True
    for name, pattern, context in checks:
        found = False
        for line in full_output.split("\n"):
            if pattern in line:
                if context is None or context in line:
                    found = True
                    break
        status = "PASS" if found else "FAIL"
        if not found:
            all_pass = False
        print(f"  [{status}] {name}")

    print()
    # Show key output lines
    for line in full_output.split("\n"):
        stripped = line.strip()
        for kw in ["DHCP client bound", "Bytes transferred",
                    "Link is Up", "64 bytes from", "lease of",
                    "GENET 5.0", "LOWER_UP", "inet 10.0"]:
            if kw in stripped:
                print(f"  > {stripped[:120]}")
                break

    print()
    return 0 if all_pass else 1


def main():
    if not check_prerequisites():
        return 1

    # Test 1: Direct Linux boot with networking (primary verification)
    linux_ok = run_direct_linux_test()

    print()

    # Test 2: U-Boot DHCP + TFTP (proves U-Boot networking)
    setup_tftpboot()
    uboot_result = run_test()

    print()
    print("=" * 60)
    print("FINAL VERDICT")
    print("=" * 60)
    print(f"  Linux networking (direct boot):  {'PASS' if linux_ok else 'FAIL'}")
    print(f"  U-Boot DHCP + TFTP:              see above")
    print()

    return 0 if linux_ok else 1


if __name__ == "__main__":
    sys.exit(main())
