#!/usr/bin/env python3
"""
RPi4B QEMU PXE Boot Test: VideoCore emulation firmware

Tests that the qemu-rpi-pxeboot firmware correctly emulates the
VideoCore bootloader's PXE sequence. The firmware boots autonomously
with zero interaction -- just like a real Pi.

Prerequisites:
  - QEMU with GENET: qemu-rpi-system-aarch64 or QEMU_OVERRIDE
  - PXE boot firmware: test-images/u-boot/u-boot.bin (built with
    rpi_4_qemu_pxeboot_defconfig) or PXEBOOT_OVERRIDE
  - RPi kernel + DTB + initramfs in test-images/tftpboot/<serial>/

Usage: uv run run-rpi-pxeboot-test.py
"""

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).parent.resolve()

# QEMU binary
_qemu = os.environ.get("QEMU_OVERRIDE")
if _qemu:
    QEMU = Path(_qemu)
else:
    QEMU = Path(shutil.which("qemu-rpi-system-aarch64") or
                "qemu-rpi-system-aarch64")

# PXE boot firmware
_pxeboot = os.environ.get("PXEBOOT_OVERRIDE")
if _pxeboot:
    FIRMWARE = Path(_pxeboot)
else:
    FIRMWARE = Path(shutil.which("rpi4b-pxeboot.bin") or
                    str(BASE / "test-images" / "u-boot" / "u-boot.bin"))

DTB = BASE / "test-images" / "bcm2711-rpi-4-b.dtb"
TFTPBOOT = BASE / "test-images" / "tftpboot"
SERIAL = "deadbeef"


def check_prerequisites():
    """Verify all required files exist."""
    missing = []
    for name, path in [
        ("QEMU with GENET", QEMU),
        ("PXE boot firmware", FIRMWARE),
        ("DTB", DTB),
        ("TFTP root", TFTPBOOT),
    ]:
        if not path.exists():
            missing.append(f"  {name}: {path}")
    if missing:
        print("Missing prerequisites:")
        print("\n".join(missing))
        return False
    return True


def setup_tftpboot():
    """Set up TFTP root with Pi 4B layout under serial prefix."""
    serial_dir = TFTPBOOT / SERIAL
    serial_dir.mkdir(parents=True, exist_ok=True)

    # Prefer compressed kernel8.img (exercises gzip decompression in firmware)
    # Fall back to uncompressed Image if compressed version unavailable
    kernel8_gz = BASE / "test-images" / "kernel8.img"
    image = TFTPBOOT / "Image"
    if kernel8_gz.exists():
        kernel_src = kernel8_gz
    elif image.exists():
        kernel_src = image
    else:
        print(f"ERROR: No kernel found ({kernel8_gz} or {image})")
        return False

    dtb = TFTPBOOT / "bcm2711-rpi-4-b.dtb"
    initrd = BASE / "test-images" / "test-initramfs.cpio.gz"

    # Copy files into serial-prefixed directory
    for src, name in [
        (kernel_src, "kernel8.img"),
        (dtb, "bcm2711-rpi-4-b.dtb"),
    ]:
        dst = serial_dir / name
        if src.exists() and (not dst.exists() or dst.stat().st_size != src.stat().st_size):
            shutil.copy2(src, dst)

    if initrd.exists():
        dst = serial_dir / "initrd.gz"
        if not dst.exists() or dst.stat().st_size != initrd.stat().st_size:
            shutil.copy2(initrd, dst)

    # Create config.txt with conditional sections to exercise cfgtxt parser
    config = serial_dir / "config.txt"
    if not config.exists():
        config.write_text(
            "[pi4]\n"
            "kernel=kernel8.img\n"
            "[all]\n"
            "arm_64bit=1\n"
            "enable_uart=1\n"
        )

    # Create cmdline.txt. Line 1 is the real kernel command line and
    # must reach the kernel verbatim. Lines 2+ are deliberately present
    # to exercise the VideoCore "first line only" rule: a real Raspberry
    # Pi firmware discards everything from the first '\n' onwards, and
    # our cmdlinetxt parser must do the same.
    cmdline = serial_dir / "cmdline.txt"
    cmdline.write_text(
        "earlycon=pl011,mmio32,0xfe201000 console=ttyAMA0 "
        "loglevel=7 rdinit=/init\n"
        "this_line_must_not_reach_kernel=yes\n"
        "# and this comment line must not reach the kernel either\n"
    )

    return True


def run_test():
    """Run the pxeboot test -- firmware boots autonomously."""
    print("=" * 70)
    print("RPi4B QEMU PXE Boot Test (VideoCore emulation)")
    print("  Firmware: " + str(FIRMWARE))
    print("  TFTP root: " + str(TFTPBOOT))
    print("  Serial: " + SERIAL)
    print("=" * 70)

    proc = subprocess.Popen(
        [str(QEMU), "-M", "raspi4b",
         "-kernel", str(FIRMWARE), "-dtb", str(DTB),
         "-nic", f"user,tftp={TFTPBOOT}",
         "-device", "usb-kbd",
         "-chardev", "null,id=usb-serial0",
         "-device", "usb-serial,chardev=usb-serial0",
         "-serial", "stdio", "-display", "none", "-monitor", "none"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)

    out_lines = []
    start = time.time()

    def read_stdout():
        for line in iter(proc.stdout.readline, ''):
            out_lines.append(line)

    def read_stderr():
        for line in iter(proc.stderr.readline, ''):
            pass

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()

    # No stdin commands -- firmware is fully autonomous!
    # Wait for the init script to complete
    deadline = time.time() + 120
    while time.time() < deadline:
        text = "".join(out_lines)
        if "Network test complete" in text or "poweroff" in text:
            time.sleep(2)  # Let final output flush
            break
        time.sleep(1)

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

    # Required checks
    checks = [
        ("VC banner",           "Raspberry Pi Bootloader"),
        ("DHCP",                "DHCP client bound"),
        ("TFTP config.txt",     f"{SERIAL}/config.txt"),
        ("TFTP kernel",         f"{SERIAL}/kernel8.img"),
        ("Config parsed",       "config.txt: kernel=kernel8.img"),
        ("cmdline.txt line 1 applied",
                                "Kernel command line: earlycon=pl011,mmio32,0xfe201000 console=ttyAMA0 loglevel=7 rdinit=/init"),
        ("Gzip decompress",     "Decompressed kernel"),
        ("Kernel boots",        "Booting Linux on physical CPU"),
        ("GENET driver",        "bcmgenet"),
        ("USB controller",      "DWC OTG Controller"),
        ("USB hub",             "USB hub found"),
        ("USB keyboard",        "QEMU USB Keyboard"),
        ("Link up",             "Link is Up"),
        ("DHCP lease",          "lease of"),
        ("HTTPS fetch",         "HTTPS fetch: SUCCESS"),
    ]
    # Negative checks: strings that must NOT appear, to prove the
    # "first line only" rule from real VideoCore firmware works.
    negative_checks = [
        ("cmdline.txt line 2 discarded",
                                "this_line_must_not_reach_kernel"),
        ("cmdline.txt comment discarded",
                                "and this comment line must not reach"),
    ]
    # Optional
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

    for name, pattern in negative_checks:
        absent = pattern not in text
        if not absent:
            all_pass = False
        print(f"  [{'PASS' if absent else 'FAIL'}] {name}")

    for name, pattern in optional_checks:
        found = pattern in text
        print(f"  [{'PASS' if found else 'SKIP'}] {name} (optional)")

    # Key output lines
    print()
    for line in text.split("\n"):
        s = line.strip()
        for kw in ["Raspberry Pi Bootloader", "Board serial",
                    "DHCP client bound", "Loading.*kernel8",
                    "Decompressed kernel", "Starting kernel",
                    "Booting Linux",
                    "bcmgenet", "dwc2", "USB:", "ttyUSB",
                    "Link is Up", "lease of",
                    "64 bytes from", "HTTPS fetch",
                    "Network test complete"]:
            if kw in s:
                print(f"  > {s[:130]}")
                break

    print()
    if all_pass:
        print("  ALL TESTS PASSED")
        print("  Autonomous PXE boot: VC emulation → TFTP → kernel → Internet")
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
