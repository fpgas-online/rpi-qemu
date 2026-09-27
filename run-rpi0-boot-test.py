#!/usr/bin/env python3
"""
RPi Zero (raspi0) QEMU Boot Test

Boots the Raspberry Pi Zero W's own kernel and device tree (kernel.img and
bcm2708-rpi-zero-w.dtb from raspberrypi/firmware) on -M raspi0 with an
Alpine armhf (ARMv6) initramfs, the way Raspberry Pi OS boots a Zero W:
console=serial0,115200, i.e. the console on the mini UART.

Regression checks for:
  - rpi-qemu#27: bcm2835-power probing the ASB bridge without an external
    abort (the abort killed the deferred-probe worker, so nothing booted);
  - rpi-qemu#28: the mini UART registering ttyS0 and carrying the console,
    which needs QEMU to do the firmware's DT/cmdline fixups (GPIO 14/15
    pins for serial0, the DTB's own bootargs -- with 8250.nr_uarts=1 --
    kept ahead of -append, console=serial0 -> ttyS0).

Serial wiring: -serial #1 is the PL011 (the Zero W's Bluetooth UART, not
used here), -serial #2 the mini UART = serial0 = the console.

Prerequisites:
  - QEMU with the rpi-qemu patches: qemu-rpi-system-aarch64 or QEMU_OVERRIDE
  - Kernel: test-images/rpi0/kernel.img
  - DTB: test-images/rpi0/bcm2708-rpi-zero-w.dtb
  - Initramfs: test-images/test-initramfs-rpi0.cpio.gz
    (python3 build-initramfs.py --target rpi0)

Usage: uv run run-rpi0-boot-test.py
"""

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).parent.resolve()

_qemu_override = os.environ.get("QEMU_OVERRIDE")
if _qemu_override:
    QEMU = Path(_qemu_override)
else:
    QEMU = Path(shutil.which("qemu-rpi-system-aarch64") or
                "qemu-rpi-system-aarch64")

KERNEL = BASE / "test-images" / "rpi0" / "kernel.img"
DTB = BASE / "test-images" / "rpi0" / "bcm2708-rpi-zero-w.dtb"
INITRD = BASE / "test-images" / "test-initramfs-rpi0.cpio.gz"

# Raspberry Pi OS's own console argument; QEMU must rewrite it like the
# firmware does.  earlycon shows the boot before ttyS0 exists.
BOOTARGS = "console=serial0,115200 earlycon rdinit=/init"
RX_LINE = "ping-from-host"


def check_prerequisites():
    missing = [f"  {name}: {path}" for name, path in [
        ("QEMU (with rpi-qemu patches)", QEMU),
        ("Kernel (kernel.img)", KERNEL),
        ("DTB (bcm2708-rpi-zero-w.dtb)", DTB),
        ("Initramfs (--target rpi0)", INITRD),
    ] if not path.exists()]
    if missing:
        print("Missing prerequisites:")
        print("\n".join(missing))
        return False
    return True


def run_test():
    print("=" * 70)
    print("RPi Zero (raspi0) QEMU Boot Test")
    print(f"  QEMU: {QEMU}")
    print(f"  Kernel: {KERNEL}")
    print("=" * 70)

    proc = subprocess.Popen(
        [str(QEMU), "-M", "raspi0",
         "-kernel", str(KERNEL), "-dtb", str(DTB), "-initrd", str(INITRD),
         "-append", BOOTARGS,
         "-serial", "null",      # PL011 (Bluetooth UART on a Zero W)
         "-serial", "stdio",     # mini UART = serial0 = console
         "-display", "none", "-monitor", "none", "-no-reboot"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)

    out_lines, err_lines = [], []

    def reader(stream, sink):
        for line in iter(stream.readline, ''):
            sink.append(line)

    threading.Thread(target=reader, args=(proc.stdout, out_lines),
                     daemon=True).start()
    threading.Thread(target=reader, args=(proc.stderr, err_lines),
                     daemon=True).start()

    def wait_for(pattern, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if pattern in "".join(out_lines):
                return True
            if proc.poll() is not None:
                return pattern in "".join(out_lines)
            time.sleep(0.5)
        return False

    start = time.time()
    try:
        if wait_for("RX test: READY", timeout=240):
            proc.stdin.write(RX_LINE + "\n")
            proc.stdin.flush()
        else:
            print("  TIMEOUT waiting for the init script")
        if not wait_for("=== raspi0 test complete ===", timeout=90):
            print("  TIMEOUT waiting for test completion")
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
    finally:
        elapsed = time.time() - start
        if proc.poll() is None:
            print(f"\n--- Terminating QEMU after {elapsed:.1f}s ---")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        else:
            print(f"\n--- QEMU exited (rc={proc.returncode}) after {elapsed:.1f}s ---")

    text = "".join(out_lines)
    stderr_text = "".join(err_lines)

    checks = [
        ("Kernel boots",           "Booting Linux on physical CPU"),
        ("Power domains (#27)",    "Broadcom BCM2835 power domains driver"),
        ("DTB bootargs kept (#28)", "coherent_pool=1M 8250.nr_uarts=1"),
        ("console=serial0 -> ttyS0 (#28)", "console=ttyS0,115200 earlycon"),
        ("ttyS0 registered (#28)", "ttyS0 at MMIO 0x20215040"),
        ("Console on ttyS0",       "console [ttyS0] enabled"),
        ("Userspace console",      "Console: ttyS0"),
        ("RX over mini UART",      f"RX test: got [{RX_LINE}]"),
        ("Test complete",          "=== raspi0 test complete ==="),
    ]
    negative_checks = [
        ("No external abort (#27)", "external abort"),
        ("No pinctrl failure (#28)", "20215040.serial: there is not valid maps"),
        ("No oops",                  "Internal error:"),
    ]

    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    all_pass = True
    for name, pattern in checks:
        found = pattern in text
        all_pass &= found
        print(f"  [{'PASS' if found else 'FAIL'}] {name}")
    for name, pattern in negative_checks:
        found = pattern in text
        all_pass &= not found
        print(f"  [{'FAIL' if found else 'PASS'}] {name}")

    if stderr_text.strip():
        print("\n  QEMU stderr:")
        for line in stderr_text.strip().split("\n")[:10]:
            print(f"    {line.rstrip()}")

    print()
    for line in text.split("\n"):
        s = line.strip()
        for kw in ["Linux version", "Kernel command line", "power domains",
                   "external abort", "PC is at", "ttyS0", "Cmdline:",
                   "Console:", "RX test:", "raspi0 test complete"]:
            if kw in s:
                print(f"  > {s[:150]}")
                break

    print()
    print("  ALL TESTS PASSED" if all_pass else "  SOME TESTS FAILED")
    print("=" * 70)
    return 0 if all_pass else 1


def main():
    if not check_prerequisites():
        return 1
    return run_test()


if __name__ == "__main__":
    sys.exit(main())
