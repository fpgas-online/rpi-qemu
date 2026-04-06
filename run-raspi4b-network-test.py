#!/usr/bin/env python3
"""
Boot RPi4B in QEMU: U-Boot -> DHCP -> TFTP kernel+initrd -> Linux with internet.

Full end-to-end demonstration of GENET Ethernet:
1. U-Boot boots, gets DHCP from SLIRP
2. U-Boot downloads kernel, DTB, initrd via TFTP
3. U-Boot fixes DTB stdout-path for QEMU serial
4. Linux boots, gets DHCP, pings gateway and 8.8.8.8

Usage: uv run run-raspi4b-network-test.py
"""

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


def run():
    print("=" * 60)
    print("RPi4B QEMU Network Test: U-Boot -> TFTP -> Linux -> Internet")
    print("=" * 60)
    print()

    proc = subprocess.Popen(
        [str(QEMU), "-M", "raspi4b",
         "-kernel", str(UBOOT), "-dtb", str(DTB),
         "-nic", f"user,tftp={TFTPBOOT}",
         "-serial", "stdio", "-display", "none", "-monitor", "none"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True)

    def send(cmd, wait=2):
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()
        time.sleep(wait)

    try:
        # Phase 1: Stop autoboot
        print("[1/5] Waiting for U-Boot...")
        time.sleep(8)
        for _ in range(5):
            proc.stdin.write(" ")
            proc.stdin.flush()
            time.sleep(0.3)
        time.sleep(3)

        # Phase 2: DHCP
        print("[2/5] U-Boot DHCP...")
        send("dhcp", 10)

        # Phase 3: TFTP
        print("[3/5] TFTP: kernel + DTB + initrd...")
        send("tftpboot 0x8000000 kernel8.img", 12)
        send("tftpboot 0x7000000 bcm2711-rpi-4-b.dtb", 8)
        send("tftpboot 0xA000000 initrd.gz", 10)

        # Phase 4: Fix DTB and boot Linux
        # The RPi DTB sets stdout-path to serial0 (mini UART) but QEMU's
        # -serial connects to PL011 (serial1). Fix the DTB so Linux uses
        # the right console.
        print("[4/5] Fixing DTB console and booting Linux...")
        # Fix the DTB for QEMU serial console.
        # RPi DTB has serial0=mini-UART (not connected in QEMU),
        # serial1=PL011 (connected to -serial stdio).
        # Swap serial0/1 aliases so Linux uses PL011 as its primary UART.
        # Also delete the original bootargs from DTB to avoid conflict.
        # Fix DTB for QEMU: swap serial aliases so PL011 = serial0 = ttyAMA0
        # RPi DTB has serial0=mini-UART, serial1=PL011
        # QEMU only connects PL011 to -serial stdio
        send("fdt addr 0x7000000")
        send('fdt set /aliases serial0 "/soc/serial@7e201000"')
        send('fdt set /aliases serial1 "/soc/serial@7e215040"')
        send('fdt set /chosen stdout-path "serial0:115200n8"')
        send('fdt rm /chosen bootargs')
        send("setenv bootargs earlycon=pl011,0xfe201000 console=ttyAMA0 loglevel=4 rdinit=/init")
        send("booti 0x8000000 0xA000000:${filesize} 0x7000000", 120)

        print("[5/5] Waiting for network test results...")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        proc.terminate()
        try:
            output, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            output, _ = proc.communicate()

    # Save log
    log_file = BASE / "test-images" / "full-network-test-log.txt"
    log_file.write_text(output)

    # Check results
    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)

    checks = [
        ("U-Boot DHCP",     "DHCP client bound"),
        ("TFTP kernel",     "Bytes transferred = 1036"),
        ("TFTP DTB",        "Bytes transferred = 563"),
        ("TFTP initrd",     "Bytes transferred = 385"),
        ("Linux boot",      "Booting Linux"),
        ("GENET driver",    "bcmgenet"),
        ("Link up",         "Link is Up"),
        ("DHCP lease",      "lease of"),
        ("Ping gateway",    "bytes from 10.0.2.2"),
        ("Ping 8.8.8.8",   "bytes from 8.8.8.8"),
    ]

    all_pass = True
    for name, pattern in checks:
        found = pattern in output
        if not found:
            all_pass = False
        print(f"  [{'PASS' if found else 'FAIL'}] {name}")

    print()
    for line in output.split("\n"):
        s = line.strip()
        for kw in ["DHCP client bound", "Bytes transferred",
                    "Link is Up", "64 bytes from", "lease of",
                    "bcmgenet", "LOWER_UP", "inet 10.0",
                    "=== Network test"]:
            if kw in s:
                print(f"  > {s[:130]}")
                break

    print()
    print(f"Log: {log_file}")
    return 0 if all_pass else 1


def main():
    if not check_prerequisites():
        return 1
    setup_tftpboot()
    return run()


if __name__ == "__main__":
    sys.exit(main())
