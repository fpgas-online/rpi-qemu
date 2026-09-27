#!/usr/bin/env python3
"""
USB/IP interop with the Linux kernel's vhci-hcd (#22).

Exports a usb-storage device from QEMU and attaches it through vhci-hcd's
sysfs interface, which is what `usbip attach` does after its import
request: the kernel's own usb-storage driver must bind and read the image.
With --tool usbip, uses the real `usbip` command instead.

Requirements: root, the vhci-hcd module loaded, QEMU with the rpi-qemu
patches (QEMU_OVERRIDE or qemu-rpi-system-aarch64); --tool usbip also
needs the usbip userspace tool.

Usage: sudo QEMU_OVERRIDE=... python3 run-usbip-vhci-test.py [--tool sysfs|usbip]
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "ci"))
import usbip_client as u  # noqa: E402
from importlib import import_module  # noqa: E402

rt = import_module("run-usbip-test")
VHCI = Path("/sys/devices/platform/vhci_hcd.0")


def free_hs_port():
    for line in (VHCI / "status").read_text().splitlines()[1:]:
        f = line.split()
        if f[0] == "hs" and f[2] == "004":          # VDEV_ST_NULL
            return int(f[1])
    raise RuntimeError("no free high-speed vhci port")


def find_disk(timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for dev in Path("/sys/block").glob("sd*"):
            real = str(dev.resolve())
            vendor = dev / "device" / "vendor"
            if "vhci_hcd" in real and vendor.exists() and vendor.read_text().strip() == "QEMU":
                return Path("/dev") / dev.name
        time.sleep(0.2)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", choices=["sysfs", "usbip"], default="sysfs")
    args = ap.parse_args()
    if os.geteuid() != 0 or not VHCI.exists():
        print("needs root and the vhci-hcd module (modprobe vhci-hcd)")
        return 1
    img = rt.storage_image()
    with rt.QemuUsbip(rt.storage_args(img)) as q:
        if args.tool == "usbip":
            out = subprocess.run(["usbip", "--tcp-port", str(q.port), "list", "-r", "127.0.0.1"],
                                 capture_output=True, text=True, check=True).stdout
            print(out)
            if "46f4:0001" not in out:
                print("FAIL: usbip list does not show the device")
                return 1
            subprocess.run(["usbip", "--tcp-port", str(q.port), "attach", "-r", "127.0.0.1", "-b", "1-1"],
                           check=True)
            port = None
        else:
            c = q.client()
            rec = c.import_device("1-1")
            port = free_hs_port()
            (VHCI / "attach").write_text(f"{port} {c.sock.fileno()} {rec.devid} {rec.speed}")
            c.close()                   # the kernel holds its own reference
        disk = find_disk(30)
        if disk is None:
            print("FAIL: no QEMU disk appeared behind vhci_hcd")
            return 1
        with open(disk, "rb") as f:
            first = f.read(4096)
        ok = all(first[i * 512:i * 512 + 4] == i.to_bytes(4, "little") for i in range(8))
        print(f"  {'PASS' if ok else 'FAIL'}  kernel usb-storage reads the image via {disk}")
        if port is None:
            status = (VHCI / "status").read_text().splitlines()
            port = next(int(l.split()[1]) for l in status[1:] if l.split()[2] == "006")
        (VHCI / "detach").write_text(str(port))
        time.sleep(1)
        gone = find_disk(0.1) is None
        print(f"  {'PASS' if gone else 'FAIL'}  disk removed after detach")
        alive = q.alive()
        print(f"  {'PASS' if alive else 'FAIL'}  QEMU still running")
    shutil.rmtree(rt.WORK, ignore_errors=True)      # root-owned files
    return 0 if ok and gone and alive else 1


if __name__ == "__main__":
    sys.exit(main())
