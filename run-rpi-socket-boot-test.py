#!/usr/bin/env python3
"""
RPi4B QEMU Socket Networking Boot Test (no peer)

Proves that QEMU raspi4b boots and produces serial output when using
socket networking (-nic socket) instead of user-mode networking (-nic user).
No network peer is connected -- this verifies the socket networking backend
doesn't prevent the machine from booting.

This directly addresses the claim that "QEMU raspi4b starts but produces
zero serial output" when using socket networking.

Test sequence:
1. QEMU starts with U-Boot and -nic socket,listen=:PORT
2. Kernel, DTB, and initrd pre-loaded into guest RAM via -device loader
3. U-Boot boots and produces serial output
4. U-Boot runs booti to start Linux (no TFTP needed)
5. Linux boots, GENET and DWC2 drivers initialize

Prerequisites:
  - QEMU with GENET: qemu-rpi-system-aarch64 or QEMU_OVERRIDE
  - U-Boot: test-images/u-boot/u-boot.bin
  - Kernel: test-images/tftpboot/Image
  - DTB: test-images/bcm2711-rpi-4-b.dtb
  - Initramfs: test-images/test-initramfs.cpio.gz

Usage: uv run run-rpi-socket-boot-test.py
"""

import os
import shutil
import socket as _socket
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).parent.resolve()

# QEMU binary (same resolution as other test scripts)
_qemu_override = os.environ.get("QEMU_OVERRIDE")
if _qemu_override:
    QEMU = Path(_qemu_override)
else:
    QEMU = Path(shutil.which("qemu-rpi-system-aarch64") or
                "qemu-rpi-system-aarch64")

UBOOT = BASE / "test-images" / "u-boot" / "u-boot.bin"
DTB = BASE / "test-images" / "bcm2711-rpi-4-b.dtb"
KERNEL = BASE / "test-images" / "tftpboot" / "Image"
INITRD = BASE / "test-images" / "test-initramfs.cpio.gz"

# Memory layout (same addresses as run-rpi-boot-test.py)
KERNEL_ADDR = 0x10000000   # 256 MB
DTB_ADDR    = 0x0f000000   # 240 MB
INITRD_ADDR = 0x12000000   # 288 MB

# Kernel boot parameters
BOOTARGS = "earlycon=pl011,mmio32,0xfe201000 console=ttyAMA0 loglevel=4 rdinit=/init"


def find_free_port():
    """Find an available TCP port.

    Note: small TOCTOU race -- the port can be taken between our close()
    and QEMU's bind().  Unavoidable here because QEMU is the listener,
    not us.  The socket-network-test avoids this by having Python bind
    first.  In practice the window is sub-millisecond on localhost.
    """
    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


def check_prerequisites():
    """Verify all required files exist."""
    missing = []
    for name, path in [
        ("QEMU (custom build with GENET)", QEMU),
        ("U-Boot (rpi_4_qemu_defconfig)", UBOOT),
        ("DTB", DTB),
        ("Uncompressed kernel Image", KERNEL),
        ("Initramfs", INITRD),
    ]:
        if not path.exists():
            missing.append(f"  {name}: {path}")
    if missing:
        print("Missing prerequisites:")
        print("\n".join(missing))
        return False
    return True


def run_test():
    """Run the socket networking boot test (no peer)."""
    port = find_free_port()
    initrd_size = INITRD.stat().st_size

    print("=" * 70)
    print("RPi4B QEMU Socket Networking Boot Test (no peer)")
    print(f"  Networking: -nic socket,listen=:{port} (no peer connected)")
    print("  Kernel loaded via: -device loader (pre-loaded into guest RAM)")
    print("=" * 70)

    proc = subprocess.Popen(
        [str(QEMU), "-M", "raspi4b",
         "-kernel", str(UBOOT), "-dtb", str(DTB),
         # Socket networking -- no peer will connect
         "-nic", f"socket,listen=:{port}",
         # Pre-load kernel, DTB, and initrd into guest RAM
         # so U-Boot can boot Linux without TFTP
         "-device", f"loader,file={KERNEL},addr=0x{KERNEL_ADDR:x},force-raw=on",
         "-device", f"loader,file={DTB},addr=0x{DTB_ADDR:x},force-raw=on",
         "-device", f"loader,file={INITRD},addr=0x{INITRD_ADDR:x},force-raw=on",
         # USB devices (same as boot test)
         "-device", "usb-kbd",
         "-chardev", "null,id=usb-serial0",
         "-device", "usb-serial,chardev=usb-serial0",
         "-device", "usb-net",
         "-serial", "stdio", "-display", "none", "-monitor", "none"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)

    out_lines = []
    err_lines = []
    start = time.time()

    def read_stdout():
        for line in iter(proc.stdout.readline, ''):
            out_lines.append(line)

    def read_stderr():
        for line in iter(proc.stderr.readline, ''):
            err_lines.append(line)

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
        print("\n--- Phase 1: U-Boot startup (socket networking) ---")
        time.sleep(6)

        # Interrupt autoboot
        for _ in range(5):
            proc.stdin.write(" ")
            proc.stdin.flush()
            time.sleep(0.3)
        time.sleep(2)

        # === Phase 2: FDT setup + booti ===
        # Files are pre-loaded into RAM by -device loader,
        # so no TFTP/DHCP needed.
        print("--- Phase 2: FDT setup + booti (files pre-loaded via -device loader) ---")
        send(f"fdt addr 0x{DTB_ADDR:x}", 2)
        send("fdt resize 8192", 2)
        send("fdt set /aliases serial0 /soc/serial@7e201000", 2)
        send("fdt set /aliases serial1 /soc/serial@7e215040", 2)
        send(f'setenv bootargs "{BOOTARGS}"', 2)
        send(f"booti 0x{KERNEL_ADDR:x} 0x{INITRD_ADDR:x}:{initrd_size:x} 0x{DTB_ADDR:x}", 3)

        # === Phase 3: Wait for Linux ===
        print("--- Phase 3: Waiting for Linux boot ---")
        # Network tests in initramfs will fail (no peer), but the
        # init script completes gracefully regardless.
        if not wait_for("Network test complete", timeout=120, label="init complete"):
            if wait_for("Booting Linux on physical CPU", timeout=5):
                print("  Kernel booted (init script may still be running)")
            else:
                print("  TIMEOUT waiting for kernel boot")

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
    stderr_text = "".join(err_lines)

    # Required checks: prove socket networking doesn't prevent boot
    checks = [
        ("U-Boot serial output",  "U-Boot"),
        ("booti starts kernel",   "Starting kernel"),
        ("Kernel boots",          "Booting Linux on physical CPU"),
        ("GENET driver",          "bcmgenet"),
        ("DWC2 USB",              "dwc2"),
        ("USB device",            "USB:"),
        ("USB serial",            "ttyUSB"),
        ("Link up",               "Link is Up"),
    ]

    # Expected failures: no network peer connected
    no_peer_checks = [
        ("DHCP lease",            "lease of"),
        ("HTTPS fetch",           "HTTPS fetch: SUCCESS"),
    ]

    print("\n" + "=" * 70)
    print("RESULTS (socket networking, no peer)")
    print("=" * 70)

    all_pass = True
    for name, pattern in checks:
        found = pattern in text
        if not found:
            all_pass = False
        print(f"  [{'PASS' if found else 'FAIL'}] {name}")

    print()
    print("  Expected no-peer results:")
    for name, pattern in no_peer_checks:
        found = pattern in text
        print(f"  [{'PASS' if found else 'N/A '}] {name}")

    if stderr_text.strip():
        print()
        print("  QEMU stderr:")
        for line in stderr_text.strip().split("\n")[:10]:
            print(f"    {line.rstrip()}")

    # Key output lines
    print()
    for line in text.split("\n"):
        s = line.strip()
        for kw in ["U-Boot", "Net:", "bcmgenet", "socket",
                    "Starting kernel", "Booting Linux",
                    "dwc2", "USB:", "ttyUSB", "Link is Up",
                    "Network test complete"]:
            if kw in s:
                print(f"  > {s[:130]}")
                break

    print()
    if all_pass:
        print("  ALL TESTS PASSED")
        print("  Socket networking does NOT prevent boot or serial output")
    else:
        print("  SOME TESTS FAILED")

    print("=" * 70)
    return 0 if all_pass else 1


def main():
    if not check_prerequisites():
        return 1
    return run_test()


if __name__ == "__main__":
    sys.exit(main())
