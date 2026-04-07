#!/usr/bin/env python3
"""
Debug booti hang using QEMU monitor interface.
Starts QEMU with monitor on a UNIX socket, triggers booti, waits for hang,
then queries CPU state via the monitor.
"""

import socket
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


def monitor_command(sock_path, cmd, timeout=5):
    """Send a command to QEMU monitor and return the response."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(str(sock_path))

    # Read initial banner
    data = b""
    while True:
        try:
            chunk = sock.recv(4096)
            data += chunk
            if b"(qemu)" in data:
                break
        except socket.timeout:
            break

    # Send command
    sock.sendall((cmd + "\n").encode())
    time.sleep(1)

    # Read response
    response = b""
    while True:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"(qemu)" in response:
                break
        except socket.timeout:
            break

    sock.close()
    return response.decode(errors="replace")


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    kernel_addr = 0x10000000
    dtb_addr    = 0x0f000000
    initrd_addr = 0x12000000
    bootargs = "earlycon=pl011,mmio32,0xfe201000 console=ttyAMA1 loglevel=7 rdinit=/init"

    monitor_sock = LOG_DIR / "qemu-monitor.sock"
    if monitor_sock.exists():
        monitor_sock.unlink()

    print("Starting QEMU with monitor socket...")

    proc = subprocess.Popen(
        [str(QEMU), "-M", "raspi4b",
         "-kernel", str(UBOOT), "-dtb", str(DTB),
         "-nic", f"user,tftp={TFTPBOOT}",
         "-serial", "stdio",
         "-display", "none",
         "-monitor", f"unix:{monitor_sock},server,nowait"],
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
        print(f"[{t:7.2f}s] >>> {cmd}")
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()
        time.sleep(wait)

    try:
        # Wait for U-Boot
        time.sleep(8)
        for _ in range(5):
            proc.stdin.write(" ")
            proc.stdin.flush()
            time.sleep(0.3)
        time.sleep(3)

        # DHCP + TFTP
        send("dhcp", 12)
        send(f"tftpboot 0x{kernel_addr:x} Image", 15)
        send(f"tftpboot 0x{dtb_addr:x} bcm2711-rpi-4-b.dtb", 8)
        send(f"tftpboot 0x{initrd_addr:x} initrd.gz", 10)

        # FDT + bootargs
        send(f"fdt addr 0x{dtb_addr:x}", 2)
        send("fdt resize 8192", 2)
        send(f'setenv bootargs "{bootargs}"', 2)

        # BOOTI
        send(f"booti 0x{kernel_addr:x} 0x{initrd_addr:x}:${{filesize}} 0x{dtb_addr:x}", 3)

        # Wait for hang to establish
        print("\n--- Waiting 15s for hang to establish ---")
        time.sleep(15)

        # Query CPU state via monitor
        print("\n--- Querying QEMU monitor ---")

        # Stop the VM first so we can inspect state
        print("\n=== Stopping VM ===")
        resp = monitor_command(monitor_sock, "stop")
        print(resp)
        time.sleep(1)

        # Get CPU registers
        print("\n=== CPU info (all CPUs) ===")
        resp = monitor_command(monitor_sock, "info cpus")
        print(resp)

        # Get registers for CPU 0
        print("\n=== CPU 0 registers ===")
        resp = monitor_command(monitor_sock, "info registers")
        print(resp)

        # Disassemble at current PC
        # First, parse PC from registers output
        for line in resp.split("\n"):
            if "PC=" in line or "pc " in line.lower():
                print(f"  Found PC line: {line.strip()}")

        # Try xp (physical memory examine) at key addresses
        print("\n=== Disassemble around current execution ===")
        resp2 = monitor_command(monitor_sock, "info registers", timeout=3)

        # Extract PC value
        pc_val = None
        for line in resp2.split("\n"):
            if "PC=" in line:
                # Format: PC=00000000xxxxxxxx
                parts = line.split("PC=")
                if len(parts) > 1:
                    pc_hex = parts[1].split()[0]
                    pc_val = int(pc_hex, 16)
                    print(f"  PC = 0x{pc_val:016x}")

        if pc_val is not None:
            # Dump instructions around PC
            print(f"\n=== Memory at PC (0x{pc_val:x}) ===")
            resp = monitor_command(monitor_sock, f"xp /20i 0x{pc_val:x}")
            print(resp)

            # Also check a range before PC
            if pc_val >= 0x20:
                print(f"\n=== Memory at PC-0x20 ===")
                resp = monitor_command(monitor_sock, f"xp /16i 0x{pc_val - 0x20:x}")
                print(resp)

        # Resume and get one more sample
        print("\n=== Resuming for 5s, then checking again ===")
        monitor_command(monitor_sock, "cont")
        time.sleep(5)
        monitor_command(monitor_sock, "stop")
        time.sleep(1)

        print("\n=== CPU 0 registers (2nd sample) ===")
        resp = monitor_command(monitor_sock, "info registers")
        print(resp)

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\nTerminating QEMU...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    # Save output
    log_file = LOG_DIR / "monitor-booti-output.log"
    with open(log_file, "w") as f:
        f.write("".join(out_lines))
    print(f"Full output saved to: {log_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
