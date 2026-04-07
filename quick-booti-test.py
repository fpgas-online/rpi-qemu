#!/usr/bin/env python3
"""Quick single booti test for fast iteration. Configurable via command line."""

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


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Configuration
    kernel_file = "Image"
    kernel_addr = 0x10000000
    dtb_addr    = 0x0f000000
    initrd_addr = 0x12000000
    bootargs = "earlycon=pl011,mmio32,0xfe201000 console=ttyAMA1 loglevel=7 rdinit=/init"
    post_booti_wait = 60

    serial2_log = LOG_DIR / "quick-serial2.log"
    cpu_log = LOG_DIR / "quick-cpu.log"

    print(f"Kernel: {kernel_file} @ 0x{kernel_addr:x}")
    print(f"Bootargs: {bootargs}")
    print(f"Post-booti wait: {post_booti_wait}s")
    print()

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
    start = time.time()

    def read_out():
        for line in iter(proc.stdout.readline, ''):
            t = time.time() - start
            tagged = f"[{t:7.2f}s] {line}"
            out_lines.append(tagged)
            sys.stdout.write(tagged)
            sys.stdout.flush()

    def read_err():
        for line in iter(proc.stderr.readline, ''):
            t = time.time() - start
            tagged = f"[{t:7.2f}s] STDERR: {line}"
            out_lines.append(tagged)
            sys.stderr.write(tagged)
            sys.stderr.flush()

    threading.Thread(target=read_out, daemon=True).start()
    threading.Thread(target=read_err, daemon=True).start()

    def send(cmd, wait=2):
        t = time.time() - start
        marker = f"[{t:7.2f}s] >>> {cmd}"
        print(marker)
        out_lines.append(marker + "\n")
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()
        time.sleep(wait)

    # Monitor CPU usage during post-booti wait
    def monitor_cpu():
        with open(cpu_log, "w") as f:
            while proc.poll() is None:
                try:
                    # Read /proc/PID/stat for CPU time
                    stat = Path(f"/proc/{proc.pid}/stat").read_text().split()
                    utime = int(stat[13])
                    stime = int(stat[14])
                    t = time.time() - start
                    f.write(f"[{t:.1f}s] utime={utime} stime={stime} total={utime+stime}\n")
                    f.flush()
                except (FileNotFoundError, ProcessLookupError):
                    break
                time.sleep(2)

    cpu_thread = threading.Thread(target=monitor_cpu, daemon=True)
    cpu_thread.start()

    try:
        # Wait for U-Boot
        time.sleep(8)
        for _ in range(5):
            proc.stdin.write(" ")
            proc.stdin.flush()
            time.sleep(0.3)
        time.sleep(3)

        # DHCP
        send("dhcp", 12)

        # TFTP
        send(f"tftpboot 0x{kernel_addr:x} {kernel_file}", 15)
        send(f"tftpboot 0x{dtb_addr:x} bcm2711-rpi-4-b.dtb", 8)
        send(f"tftpboot 0x{initrd_addr:x} initrd.gz", 10)

        # FDT setup
        send(f"fdt addr 0x{dtb_addr:x}", 2)
        send("fdt resize 8192", 2)

        # Skip board-specific FDT fixups (which may hang in QEMU)
        send("setenv skip_board_fixup 1", 2)

        # Bootargs
        send(f'setenv bootargs "{bootargs}"', 2)
        send("printenv bootargs", 2)

        # BOOTI
        send(f"booti 0x{kernel_addr:x} 0x{initrd_addr:x}:${{filesize}} 0x{dtb_addr:x}", 3)

        # Wait and monitor
        print(f"\n--- Waiting {post_booti_wait}s for kernel output ---")

        # Check CPU usage every 5 seconds during wait
        for i in range(0, post_booti_wait, 5):
            time.sleep(5)
            t = time.time() - start
            try:
                stat = Path(f"/proc/{proc.pid}/stat").read_text().split()
                utime = int(stat[13])
                stime = int(stat[14])
                print(f"  [{t:.0f}s] QEMU CPU: utime={utime} stime={stime}")
            except (FileNotFoundError, ProcessLookupError):
                print(f"  [{t:.0f}s] QEMU process ended")
                break

    finally:
        elapsed = time.time() - start
        print(f"\n[{elapsed:.1f}s] Terminating QEMU...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    # Save log
    log_file = LOG_DIR / "quick-booti.log"
    with open(log_file, "w") as f:
        f.write("".join(out_lines))

    # Check serial2
    if serial2_log.exists():
        s2 = serial2_log.read_text()
        if s2.strip():
            print(f"\n--- Serial2 output ({len(s2)} bytes) ---")
            for line in s2.split("\n")[:20]:
                print(f"  serial2> {line[:120]}")

    # Summary
    text = "".join(out_lines)
    print(f"\n--- Summary ---")
    for name, pattern in [
        ("DHCP bound", "DHCP client bound"),
        ("TFTP transfer", "Bytes transferred"),
        ("Starting kernel", "Starting kernel"),
        ("Linux version", "Linux version"),
        ("earlycon [0.", "[    0."),
        ("bcmgenet", "bcmgenet"),
        ("Link is Up", "Link is Up"),
        ("ping 8.8.8.8", "bytes from 8.8.8.8"),
    ]:
        found = pattern in text
        print(f"  [{'PASS' if found else 'FAIL'}] {name}")

    # Show CPU log
    if cpu_log.exists():
        print(f"\n--- CPU usage log ---")
        print(cpu_log.read_text()[-500:])

    print(f"\nFull log: {log_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
