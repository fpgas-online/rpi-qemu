#!/usr/bin/env python3
"""Dump raw memory around stuck PC to identify the instruction loop."""

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


def monitor_cmd(sock_path, cmd, timeout=5):
    """Send command to QEMU monitor, return response."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(str(sock_path))
    # Read banner
    data = b""
    while True:
        try:
            chunk = sock.recv(4096)
            data += chunk
            if b"(qemu)" in data:
                break
        except socket.timeout:
            break
    # Send
    sock.sendall((cmd + "\n").encode())
    time.sleep(1)
    # Read response
    resp = b""
    while True:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                break
            resp += chunk
            if b"(qemu)" in resp:
                break
        except socket.timeout:
            break
    sock.close()
    return resp.decode(errors="replace")


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    monitor_sock = LOG_DIR / "qemu-monitor.sock"
    if monitor_sock.exists():
        monitor_sock.unlink()

    proc = subprocess.Popen(
        [str(QEMU), "-M", "raspi4b",
         "-kernel", str(UBOOT), "-dtb", str(DTB),
         "-nic", f"user,tftp={TFTPBOOT}",
         "-serial", "stdio", "-display", "none",
         "-monitor", f"unix:{monitor_sock},server,nowait"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)

    out_lines = []
    start = time.time()

    def read_out():
        for line in iter(proc.stdout.readline, ''):
            out_lines.append(line)
    def read_err():
        for line in iter(proc.stderr.readline, ''):
            pass

    threading.Thread(target=read_out, daemon=True).start()
    threading.Thread(target=read_err, daemon=True).start()

    def send(cmd, wait=2):
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()
        time.sleep(wait)

    try:
        time.sleep(8)
        for _ in range(5):
            proc.stdin.write(" ")
            proc.stdin.flush()
            time.sleep(0.3)
        time.sleep(3)

        send("dhcp", 12)
        send("tftpboot 0x10000000 Image", 15)
        send("tftpboot 0xf000000 bcm2711-rpi-4-b.dtb", 8)
        send("tftpboot 0x12000000 initrd.gz", 10)
        send("fdt addr 0xf000000", 2)
        send("fdt resize 8192", 2)
        send('setenv bootargs "earlycon=pl011,mmio32,0xfe201000 console=ttyAMA1 loglevel=7 rdinit=/init"', 2)
        send("booti 0x10000000 0x12000000:${filesize} 0xf000000", 3)

        print("Waiting 10s for hang...")
        time.sleep(10)

        # Stop VM
        monitor_cmd(monitor_sock, "stop")
        time.sleep(1)

        # Get registers
        regs = monitor_cmd(monitor_sock, "info registers")

        # Extract PC
        pc_val = None
        for line in regs.split("\n"):
            if "PC=" in line:
                pc_hex = line.split("PC=")[1].split()[0]
                pc_val = int(pc_hex, 16)
                break

        if pc_val is None:
            print("Could not find PC!")
            return 1

        print(f"PC = 0x{pc_val:016x}")

        # Also get the U-Boot relocation info
        # Check for gd->reloc_off - look at X18 (U-Boot stores gd in X18)
        for line in regs.split("\n"):
            if "X18=" in line:
                # X18 is the global data pointer in U-Boot ARM64
                x18_str = line.split("X18=")[1].split()[0]
                x18_val = int(x18_str, 16)
                print(f"X18 (gd pointer) = 0x{x18_val:016x}")
                break

        # Dump raw 32-bit words around PC (aarch64 instructions are 4 bytes each)
        print(f"\nDumping raw memory around PC (0x{pc_val:x}):")
        # Use xp /Nxw to dump as hex words
        for offset in range(-0x40, 0x60, 0x40):
            addr = pc_val + offset
            resp = monitor_cmd(monitor_sock, f"xp /16xw 0x{addr:x}")
            # Clean up the response
            for line in resp.split("\n"):
                line = line.strip()
                if line and not line.startswith("(qemu)") and "xp" not in line:
                    print(f"  {line}")

        # Also dump U-Boot's global data struct to find reloc_off
        # In U-Boot ARM64, gd is at X18, and gd->reloc_off is at offset 0x98 (varies by version)
        # Let me dump the area around gd
        print(f"\nDumping gd struct area (X18=0x{x18_val:x}):")
        resp = monitor_cmd(monitor_sock, f"xp /32xw 0x{x18_val:x}")
        for line in resp.split("\n"):
            line = line.strip()
            if line and not line.startswith("(qemu)") and "xp" not in line:
                print(f"  {line}")

        # Now look at U-Boot's reloc_off in the ELF to find the struct layout
        # The reloc_off field offset depends on the version
        # For recent U-Boot, it's struct global_data member at offset ~0x58-0x68
        # Let me print the key register values clearly
        print("\nKey registers:")
        for line in regs.split("\n"):
            line = line.strip()
            if any(x in line for x in ["PC=", "X00=", "X19=", "X20=", "X21=", "X29=", "X30="]):
                print(f"  {line}")

        # X30 is the link register (return address). This tells us who called the loop function
        for line in regs.split("\n"):
            if "X30=" in line:
                x30_str = line.split("X30=")[1].split()[0]
                x30_val = int(x30_str, 16)
                print(f"\nX30 (return address) = 0x{x30_val:016x}")
                print(f"Dumping around return address:")
                resp = monitor_cmd(monitor_sock, f"xp /16xw 0x{x30_val - 0x20:x}")
                for rline in resp.split("\n"):
                    rline = rline.strip()
                    if rline and not rline.startswith("(qemu)") and "xp" not in rline:
                        print(f"  {rline}")
                break

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    return 0


if __name__ == "__main__":
    sys.exit(main())
