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
  - rpi-qemu#24: a usb-net NIC on the DWC2 host port binding cdc_ether,
    getting a DHCP lease and pinging the gateway, with dwc_otg's FIQ FSM
    enabled as Raspberry Pi OS runs it (patch 0032);
  - rpi-qemu#25: the board identity (-M raspi0,board-serial=...) reaching
    /proc/cpuinfo and /proc/device-tree/serial-number;
  - rpi-qemu#23: a host BREAK reaching magic SysRq on the PL011 but -- as
    on the hardware, whose mini UART has no break detection -- not on the
    mini UART;
  - rpi-qemu#28: the mini UART registering ttyS0 and carrying the console,
    which needs QEMU to do the firmware's DT/cmdline fixups (GPIO 14/15
    pins for serial0, the DTB's own bootargs -- with 8250.nr_uarts=1 --
    kept ahead of -append, console=serial0 -> ttyS0);
  - rpi-qemu#39: a discard (SD erase) of a GiB of the SD card taking
    seconds, not minutes, and leaving exactly that range reading as zeroes.

Serial wiring: -serial #1 is the PL011 (the Zero W's Bluetooth UART, not
used here), -serial #2 the mini UART = serial0 = the console.

Prerequisites:
  - QEMU with the rpi-qemu patches: qemu-rpi-system-aarch64 or QEMU_OVERRIDE
  - Kernel: test-images/rpi0/kernel.img
  - DTB: test-images/rpi0/bcm2708-rpi-zero-w.dtb, and the same with the
    firmware's overlays/disable-bt.dtbo applied:
    fdtoverlay -i bcm2708-rpi-zero-w.dtb -o bcm2708-rpi-zero-w-disable-bt.dtb disable-bt.dtbo
  - Initramfs: test-images/test-initramfs-rpi0.cpio.gz
    (python3 build-initramfs.py --target rpi0)

Usage: uv run run-rpi0-boot-test.py
"""

import os
import re
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
# The same DTB with the firmware's disable-bt overlay applied (fdtoverlay):
# the PL011 moves to GPIO 14/15 as serial0 and its Bluetooth child -- which
# otherwise owns the PL011's input via serdev -- is disabled.
DTB_DISABLE_BT = BASE / "test-images" / "rpi0" / "bcm2708-rpi-zero-w-disable-bt.dtb"
INITRD = BASE / "test-images" / "test-initramfs-rpi0.cpio.gz"
# A sparse 4 GiB card, so an SDHC one (block-addressed erase, #39).
SD_CARD = BASE / "test-images" / "rpi0-sd.img"
SD_SIZE = 4 << 30
GIB, MIB = 1 << 30, 1 << 20
# The guest discards [1 GiB, 2 GiB).  Marker MiBs at each end of that range,
# inside it (must read as zeroes after) and outside it (must be untouched).
SD_MARKERS = [(GIB - MIB, 0x5a, True), (GIB, 0xa5, False),
              (2 * GIB - MIB, 0xa5, False), (2 * GIB, 0x5a, True)]
# The guest's discard of 1 GiB must take no longer than this (a card erases
# it in well under a second; block by block it took about 40 s).
SD_DISCARD_MAX_S = 10

# Raspberry Pi OS's own console argument; QEMU must rewrite it like the
# firmware does.  earlycon shows the boot before ttyS0 exists.
BOOTARGS = "console=serial0,115200 earlycon rdinit=/init"
RX_LINE = "ping-from-host"
BREAK = "\x01b"      # -serial mon:stdio escape: send a BREAK to the UART
# Pinned board serial (rpi-qemu#25): the guest must see exactly this.
BOARD_SERIAL = "00000000c0ffee01"


def check_prerequisites():
    missing = [f"  {name}: {path}" for name, path in [
        ("QEMU (with rpi-qemu patches)", QEMU),
        ("Kernel (kernel.img)", KERNEL),
        ("DTB (bcm2708-rpi-zero-w.dtb)", DTB),
        ("disable-bt DTB (fdtoverlay)", DTB_DISABLE_BT),
        ("Initramfs (--target rpi0)", INITRD),
    ] if not path.exists()]
    if missing:
        print("Missing prerequisites:")
        print("\n".join(missing))
        return False
    return True


def make_sd_card():
    with open(SD_CARD, "wb") as f:
        f.truncate(SD_SIZE)
        for offset, byte, _ in SD_MARKERS:
            f.seek(offset)
            f.write(bytes([byte]) * MIB)


def sd_card_checks():
    """(name, ok) for each marker MiB of the card after the boot."""
    checks = []
    with open(SD_CARD, "rb") as f:
        for offset, byte, kept in SD_MARKERS:
            f.seek(offset)
            data = f.read(MIB)
            want = bytes([byte if kept else 0]) * MIB
            what = f"kept ({byte:#04x})" if kept else "erased to zeroes"
            checks.append((f"SD MiB at {offset / GIB:.3f} GiB {what} (#39)",
                           data == want))
    return checks


def boot_guest(serials, bootargs, on_ready, dtb=DTB):
    """Boot the Zero W kernel on raspi0; call on_ready(send) once the init
    script prints "RX test: READY".  Returns (serial output, QEMU stderr)."""
    proc = subprocess.Popen(
        [str(QEMU), "-M", f"raspi0,board-serial=0x{BOARD_SERIAL}",
         "-kernel", str(KERNEL), "-dtb", str(dtb), "-initrd", str(INITRD),
         "-append", bootargs, *serials,
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

    def send(data, pause=0.0):
        proc.stdin.write(data)
        proc.stdin.flush()
        time.sleep(pause)

    start = time.time()
    try:
        if wait_for("RX test: READY", timeout=240):
            on_ready(send)
        else:
            print("  TIMEOUT waiting for the init script")
        if not wait_for("=== raspi0 test complete ===", timeout=90):
            print("  TIMEOUT waiting for test completion")
        try:
            proc.wait(timeout=15)   # the init script powers the board off
        except subprocess.TimeoutExpired:
            pass
    finally:
        elapsed = time.time() - start
        if proc.poll() is None:
            print(f"--- Terminating QEMU after {elapsed:.1f}s ---")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        else:
            print(f"--- QEMU exited (rc={proc.returncode}) after {elapsed:.1f}s ---")
    return "".join(out_lines), "".join(err_lines)


def run_test():
    print("=" * 70)
    print("RPi Zero (raspi0) QEMU Boot Test")
    print(f"  QEMU: {QEMU}")
    print(f"  Kernel: {KERNEL}")
    print("=" * 70)

    # Boot 1: console on the mini UART (serial0), as Raspberry Pi OS has it.
    # mon:stdio lets Ctrl-A b send a BREAK.  The BREAK comes right before
    # the RX line: the mini UART has no break detection (BCM2835 ARM
    # Peripherals 2.2), so the line must arrive intact and no SysRq fire.
    print("\n--- Boot 1: console on the mini UART (ttyS0) ---")
    make_sd_card()
    text, stderr_text = boot_guest(
        ["-serial", "null",          # PL011 (Bluetooth UART on a Zero W)
         "-serial", "mon:stdio",     # mini UART = serial0 = console
         # A USB Ethernet adapter on the OTG port, as a Zero gets wired
         # networking (rpi-qemu#24); DHCP comes from -netdev user.
         "-netdev", "user,id=usbnet0", "-device", "usb-net,netdev=usbnet0",
         # An SD card for the erase test (#39)
         "-drive", f"file={SD_CARD},format=raw,if=sd"],
        BOOTARGS + " sysrq_always_enabled",
        lambda send: (send(BREAK, 1.0), send(RX_LINE + "\n")))

    # Boot 2: dtoverlay=disable-bt, the way to get a PL011 console on a
    # Zero W: RPi OS's console=serial0 must land on ttyAMA0 (QEMU routes
    # GPIO 14/15 to it in ALT0).  The PL011 does detect BREAK, so BREAK then
    # 'h' must reach magic SysRq (help), and a line typed after it must
    # still reach userspace.
    print("\n--- Boot 2: disable-bt, console on the PL011 (ttyAMA0) ---")
    pl011_text, pl011_stderr = boot_guest(
        ["-serial", "mon:stdio", "-serial", "null"],
        "console=serial0,115200 sysrq_always_enabled rdinit=/init",
        lambda send: (send(BREAK, 1.0), send("h", 2.0),
                      send(RX_LINE + "\n")),
        dtb=DTB_DISABLE_BT)


    checks = [
        ("Kernel boots",           "Booting Linux on physical CPU"),
        ("Power domains (#27)",    "Broadcom BCM2835 power domains driver"),
        ("DTB bootargs kept (#28)", "coherent_pool=1M 8250.nr_uarts=1"),
        ("console=serial0 -> ttyS0 (#28)", "console=ttyS0,115200 earlycon"),
        ("ttyS0 registered (#28)", "ttyS0 at MMIO 0x20215040"),
        ("Console on ttyS0",       "console [ttyS0] enabled"),
        ("Userspace console",      "Console: ttyS0"),
        ("RX over mini UART",      f"RX test: got [{RX_LINE}]"),
        ("dwc_otg FIQ FSM enabled", "FIQ FSM acceleration enabled"),
        ("usb-net NIC bound (#24)", "USB NIC: usb0 driver=cdc_ether"),
        # dwc_otg prints the core's GSNPSID and its TX FIFO architecture
        # (GHWCFG4.DED_FIFO_EN) while probing: the BCM2835's core is 2.80a
        # with dedicated TX FIFOs (#22).
        ("DWC2 core identity is the BCM2835's (#22)", "Core Release: 2.80a"),
        ("DWC2 has dedicated TX FIFOs (#22)", "Dedicated Tx FIFOs mode"),
        ("DHCP over usb-net (#24)", "lease of 10.0.2.15 obtained from 10.0.2.2"),
        ("Ping over usb-net (#24)", "3 packets transmitted, 3 packets received"),
        ("SD discard succeeds (#39)", "SD discard: rc=0 "),
        ("SD erased blocks read as zeroes (#39)", "SD erased nonzero bytes: 0\n"),
        ("cpuinfo Revision (#25)", "Revision: 920092"),
        ("cpuinfo Serial (#25)",   f"Serial: {BOARD_SERIAL}"),
        ("DT serial-number (#25)", f"DT serial-number: {BOARD_SERIAL}"),
        ("Test complete",          "=== raspi0 test complete ==="),
    ]
    negative_checks = [
        ("No external abort (#27)", "external abort"),
        ("No pinctrl failure (#28)", "20215040.serial: there is not valid maps"),
        ("No oops",                  "Internal error:"),
        # A detected BREAK would make the RX line's first byte ('p') a SysRq
        # key: show-registers, logged as "sysrq: Show Regs", and the RX
        # check above would see the line without it.
        ("BREAK ignored by mini UART (#23)", "sysrq: Show Regs"),
    ]
    pl011_checks = [
        ("disable-bt: console=serial0 -> ttyAMA0", "console=ttyAMA0,115200"),
        ("disable-bt: userspace console on PL011", "Console: ttyAMA0"),
        ("BREAK -> SysRq on PL011 (#23)", "sysrq: HELP"),
        ("RX over PL011 after SysRq", f"RX test: got [{RX_LINE}]"),
        ("disable-bt boot complete", "=== raspi0 test complete ==="),
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
        # The BREAK is only sent once the RX prompt appears; without it the
        # "ignored" check would pass vacuously.
        exercised = "RX test: READY" in text or "BREAK" not in name
        ok = exercised and not found
        all_pass &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
              + ("" if exercised else " (not exercised)"))
    m = re.search(r"SD discard: rc=\d+ in (\d+) s", text)
    ok = m is not None and int(m.group(1)) <= SD_DISCARD_MAX_S
    all_pass &= ok
    print(f"  [{'PASS' if ok else 'FAIL'}] SD discard of 1 GiB within "
          f"{SD_DISCARD_MAX_S} s (#39)"
          + (f": {m.group(1)} s" if m else ": no timing"))
    for name, ok in sd_card_checks():
        all_pass &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    SD_CARD.unlink()
    for name, pattern in pl011_checks:
        found = pattern in pl011_text
        all_pass &= found
        print(f"  [{'PASS' if found else 'FAIL'}] {name}")
    stderr_text += pl011_stderr

    if stderr_text.strip():
        print("\n  QEMU stderr:")
        for line in stderr_text.strip().split("\n")[:10]:
            print(f"    {line.rstrip()}")

    print()
    for line in text.split("\n"):
        s = line.strip()
        for kw in ["Linux version", "Kernel command line", "power domains",
                   "external abort", "PC is at", "ttyS0", "Cmdline:",
                   "Console:", "RX test:", "Revision:", "Serial:",
                   "USB NIC:", "lease of", "packets transmitted", "SD ",
                   "serial-number:", "raspi0 test complete"]:
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
