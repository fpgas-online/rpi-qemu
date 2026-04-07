#!/usr/bin/env python3
"""
Diagnostic script to reproduce and characterize the U-Boot booti failure.

Tests multiple configurations to identify why booti doesn't work:
1. Different console parameters (ttyAMA0 vs ttyAMA1)
2. Different kernel formats (compressed kernel8.img vs uncompressed Image)
3. Different memory layouts
4. Captures all output with timestamps for analysis.

Usage: uv run diagnose-booti.py
"""

import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).parent.resolve()
QEMU = BASE / "upstream-qemu" / "build" / "qemu-system-aarch64"
UBOOT = BASE / "test-images" / "u-boot" / "u-boot.bin"
DTB = BASE / "test-images" / "bcm2711-rpi-4-b.dtb"
TFTPBOOT = BASE / "test-images" / "tftpboot"
LOG_DIR = BASE / "tmp"


def check_prerequisites():
    """Verify all required files exist."""
    missing = []
    for name, path in [
        ("QEMU", QEMU),
        ("U-Boot", UBOOT),
        ("DTB", DTB),
        ("TFTP dir", TFTPBOOT),
        ("TFTP Image", TFTPBOOT / "Image"),
        ("TFTP kernel8", TFTPBOOT / "kernel8.img"),
        ("TFTP DTB", TFTPBOOT / "bcm2711-rpi-4-b.dtb"),
        ("TFTP initrd", TFTPBOOT / "initrd.gz"),
    ]:
        if not path.exists():
            missing.append(f"  {name}: {path}")
    if missing:
        print("Missing prerequisites:\n" + "\n".join(missing))
        return False
    return True


def run_booti_test(test_name, kernel_file, kernel_addr, dtb_addr, initrd_addr,
                   bootargs, timeout_secs=180):
    """
    Run a single booti test with given parameters.
    Returns (output_text, elapsed_seconds).
    """
    print(f"\n{'=' * 70}")
    print(f"TEST: {test_name}")
    print(f"  Kernel: {kernel_file} @ 0x{kernel_addr:08x}")
    print(f"  DTB: @ 0x{dtb_addr:08x}")
    print(f"  Initrd: @ 0x{initrd_addr:08x}")
    print(f"  Bootargs: {bootargs}")
    print(f"  Timeout: {timeout_secs}s")
    print(f"{'=' * 70}")

    serial2_log = LOG_DIR / f"{test_name}-serial2.log"

    proc = subprocess.Popen(
        [str(QEMU), "-M", "raspi4b",
         "-kernel", str(UBOOT), "-dtb", str(DTB),
         "-nic", f"user,tftp={TFTPBOOT}",
         "-serial", "stdio",
         "-serial", f"file:{serial2_log}",
         "-display", "none", "-monitor", "none"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)

    out_lines = []
    err_lines = []
    start_time = time.time()

    def read_stdout():
        for line in iter(proc.stdout.readline, ''):
            elapsed = time.time() - start_time
            out_lines.append(f"[{elapsed:7.2f}s] {line}")

    def read_stderr():
        for line in iter(proc.stderr.readline, ''):
            elapsed = time.time() - start_time
            err_lines.append(f"[{elapsed:7.2f}s] STDERR: {line}")

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    def send(cmd, wait=2):
        """Send a command to U-Boot via serial stdin."""
        ts = time.time() - start_time
        print(f"  [{ts:7.2f}s] >>> {cmd}")
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()
        time.sleep(wait)

    try:
        # Wait for U-Boot to start and reach autoboot prompt
        print("  Waiting for U-Boot startup...")
        time.sleep(8)

        # Interrupt autoboot
        for _ in range(3):
            proc.stdin.write(" ")
            proc.stdin.flush()
            time.sleep(0.5)
        time.sleep(3)

        # DHCP
        send("dhcp", 12)

        # TFTP downloads
        send(f"tftpboot 0x{kernel_addr:08x} {kernel_file}", 15)
        send(f"tftpboot 0x{dtb_addr:08x} bcm2711-rpi-4-b.dtb", 8)
        send(f"tftpboot 0x{initrd_addr:08x} initrd.gz", 10)

        # Set up FDT
        send(f"fdt addr 0x{dtb_addr:08x}", 2)
        send("fdt resize 8192", 2)

        # Set bootargs
        send(f'setenv bootargs "{bootargs}"', 2)

        # Print environment for debugging
        send("printenv bootargs", 2)

        # Execute booti
        filesize_hex = "${filesize}"  # U-Boot will substitute
        send(f"booti 0x{kernel_addr:08x} 0x{initrd_addr:08x}:{filesize_hex} 0x{dtb_addr:08x}", 3)

        # Now wait for kernel output or timeout
        print(f"  Waiting up to {timeout_secs - 70}s for kernel output...")
        booti_wait = timeout_secs - 70  # account for time spent in U-Boot
        time.sleep(booti_wait)

    except Exception as e:
        print(f"  ERROR: {e}")
    finally:
        elapsed = time.time() - start_time
        print(f"  [{elapsed:7.2f}s] Terminating QEMU...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)

    all_output = "".join(out_lines)
    all_stderr = "".join(err_lines)

    # Save to log file
    log_file = LOG_DIR / f"{test_name}-output.log"
    with open(log_file, "w") as f:
        f.write(f"=== TEST: {test_name} ===\n")
        f.write(f"Kernel: {kernel_file} @ 0x{kernel_addr:08x}\n")
        f.write(f"DTB: @ 0x{dtb_addr:08x}\n")
        f.write(f"Initrd: @ 0x{initrd_addr:08x}\n")
        f.write(f"Bootargs: {bootargs}\n\n")
        f.write("=== STDOUT ===\n")
        f.write(all_output)
        f.write("\n=== STDERR ===\n")
        f.write(all_stderr)

    # Check for second serial output
    serial2_content = ""
    if serial2_log.exists():
        serial2_content = serial2_log.read_text()
        if serial2_content.strip():
            serial2_file = LOG_DIR / f"{test_name}-serial2-content.log"
            with open(serial2_file, "w") as f:
                f.write(serial2_content)

    # Analyze results
    elapsed = time.time() - start_time
    print(f"\n  --- Results for {test_name} ({elapsed:.1f}s) ---")

    checks = {
        "U-Boot prompt": "U-Boot>" in all_output or "=>" in all_output,
        "DHCP bound": "DHCP client bound" in all_output,
        "TFTP kernel": "Bytes transferred" in all_output,
        "Starting kernel": "Starting kernel" in all_output,
        "Linux version": "Linux version" in all_output,
        "earlycon output": "earlycon" in all_output.lower() or "[    0." in all_output,
        "bcmgenet": "bcmgenet" in all_output,
        "Link is Up": "Link is Up" in all_output,
        "DHCP lease": "lease of" in all_output,
        "ping 8.8.8.8": "bytes from 8.8.8.8" in all_output,
        "Serial2 has content": bool(serial2_content.strip()),
    }

    for check, passed in checks.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {check}")

    if serial2_content.strip():
        print(f"\n  Serial2 output ({len(serial2_content)} bytes):")
        for line in serial2_content.split("\n")[:10]:
            print(f"    serial2> {line[:120]}")

    # Print last 30 lines of stdout for context
    print(f"\n  Last 30 lines of output:")
    lines = all_output.split("\n")
    for line in lines[-30:]:
        print(f"    {line[:150]}")

    print(f"\n  Full log saved to: {log_file}")
    return all_output, elapsed, checks


def main():
    if not check_prerequisites():
        return 1

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Test 1: Original configuration (reproduce the known failure)
    # Uses compressed kernel8.img at 0x80000, console=ttyAMA0
    run_booti_test(
        test_name="test1-original",
        kernel_file="kernel8.img",
        kernel_addr=0x00080000,
        dtb_addr=0x02600000,
        initrd_addr=0x02700000,
        bootargs="earlycon=pl011,0xfe201000 console=ttyAMA0 loglevel=4 rdinit=/init",
        timeout_secs=120,
    )

    # Test 2: Uncompressed Image with console=ttyAMA1, safe memory layout
    run_booti_test(
        test_name="test2-image-ttyAMA1",
        kernel_file="Image",
        kernel_addr=0x10000000,
        dtb_addr=0x0f000000,
        initrd_addr=0x12000000,
        bootargs="earlycon=pl011,mmio32,0xfe201000 console=ttyAMA1 loglevel=7 rdinit=/init",
        timeout_secs=120,
    )

    # Test 3: Uncompressed Image with console=ttyAMA0 (to isolate console vs kernel issue)
    run_booti_test(
        test_name="test3-image-ttyAMA0",
        kernel_file="Image",
        kernel_addr=0x10000000,
        dtb_addr=0x0f000000,
        initrd_addr=0x12000000,
        bootargs="earlycon=pl011,mmio32,0xfe201000 console=ttyAMA0 loglevel=7 rdinit=/init",
        timeout_secs=120,
    )

    # Test 4: Compressed kernel8.img with safe memory layout and ttyAMA1
    run_booti_test(
        test_name="test4-kernel8-ttyAMA1-safe",
        kernel_file="kernel8.img",
        kernel_addr=0x10000000,
        dtb_addr=0x0f000000,
        initrd_addr=0x12000000,
        bootargs="earlycon=pl011,mmio32,0xfe201000 console=ttyAMA1 loglevel=7 rdinit=/init",
        timeout_secs=120,
    )

    print("\n" + "=" * 70)
    print("ALL DIAGNOSTIC TESTS COMPLETE")
    print(f"Logs saved to: {LOG_DIR}/")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
