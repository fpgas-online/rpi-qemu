#!/usr/bin/env python3
"""
USB/IP interop with the Linux kernel's vhci-hcd (#22).

Exports a usb-storage device from QEMU and attaches it through vhci-hcd's
sysfs interface, which is what `usbip attach` does after its import
request (or, with --tool usbip, with the real `usbip` command).  Then:

  - the host kernel's USB core must enumerate it (descriptors and the
    manufacturer/product strings, fetched over USB/IP);
  - usbfs (/dev/bus/usb, part of usbcore) runs SCSI over Bulk-Only on it:
    INQUIRY and READ(10) of the image -- kernel URBs through vhci-hcd, no
    class driver needed, so this works on kernels without usb-storage
    (e.g. GitHub's Azure runner kernel);
  - with --check disk, the kernel's usb-storage driver must bind and the
    image be readable as a block device;
  - detaching removes the device, and QEMU keeps running.

Requirements: root, the vhci-hcd module loaded, QEMU with the rpi-qemu
patches (QEMU_OVERRIDE or qemu-rpi-system-aarch64); --tool usbip also
needs the usbip userspace tool, --check disk the usb-storage module.

Usage: sudo QEMU_OVERRIDE=... python3 run-usbip-vhci-test.py \\
           [--tool sysfs|usbip] [--check usbfs|disk]
"""
import argparse
import ctypes
import fcntl
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "ci"))
from importlib import import_module  # noqa: E402

rt = import_module("run-usbip-test")
VHCI = Path("/sys/devices/platform/vhci_hcd.0")
VID, PID = "46f4", "0001"                     # QEMU usb-storage


# usbfs (linux/usbdevice_fs.h)

class BulkTransfer(ctypes.Structure):
    _fields_ = [("ep", ctypes.c_uint), ("len", ctypes.c_uint),
                ("timeout", ctypes.c_uint), ("data", ctypes.c_void_p)]


class UsbfsIoctl(ctypes.Structure):
    _fields_ = [("ifno", ctypes.c_int), ("ioctl_code", ctypes.c_int),
                ("data", ctypes.c_void_p)]


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("U") << 8) | nr


USBDEVFS_BULK = _ioc(3, 2, ctypes.sizeof(BulkTransfer))
USBDEVFS_CLAIMINTERFACE = _ioc(2, 15, 4)
USBDEVFS_RELEASEINTERFACE = _ioc(2, 16, 4)
USBDEVFS_IOCTL = _ioc(3, 18, ctypes.sizeof(UsbfsIoctl))
USBDEVFS_DISCONNECT = _ioc(0, 22, 0)


class UsbfsBulkOnly:
    """SCSI over Bulk-Only through usbfs: kernel URBs via vhci-hcd."""

    def __init__(self, devnode, ifno=0, ep_in=0x81, ep_out=0x02):
        self.fd = os.open(devnode, os.O_RDWR)
        self.ifno, self.ep_in, self.ep_out, self.tag = ifno, ep_in, ep_out, 0
        req = UsbfsIoctl(ifno, USBDEVFS_DISCONNECT, None)
        try:                        # detach usb-storage if it bound
            fcntl.ioctl(self.fd, USBDEVFS_IOCTL, req)
        except OSError as e:
            if e.errno != 61:       # ENODATA: no driver bound
                raise
        fcntl.ioctl(self.fd, USBDEVFS_CLAIMINTERFACE, struct.pack("I", ifno))

    def close(self):
        fcntl.ioctl(self.fd, USBDEVFS_RELEASEINTERFACE,
                    struct.pack("I", self.ifno))
        os.close(self.fd)

    def _bulk(self, ep, buf):
        cbuf = (ctypes.c_char * len(buf)).from_buffer_copy(buf)
        xfer = BulkTransfer(ep, len(buf), 5000, ctypes.addressof(cbuf))
        n = fcntl.ioctl(self.fd, USBDEVFS_BULK, xfer)
        return bytes(cbuf)[:n]

    def command(self, cdb, data_in_len):
        self.tag += 1
        cbw = struct.pack("<IIIBBB16s", 0x43425355, self.tag, data_in_len,
                          0x80, 0, len(cdb), bytes(cdb).ljust(16, b"\0"))
        self._bulk(self.ep_out, cbw)
        data = self._bulk(self.ep_in, bytes(data_in_len))
        csw = self._bulk(self.ep_in, bytes(13))
        sig, tag, _residue, status = struct.unpack("<IIIB", csw)
        if sig != 0x53425355 or tag != self.tag:
            raise RuntimeError(f"bad CSW {csw.hex()}")
        return data, status


# The imported device in the host kernel

def free_hs_port():
    for line in (VHCI / "status").read_text().splitlines()[1:]:
        f = line.split()
        if f[0] == "hs" and f[2] == "004":          # VDEV_ST_NULL
            return int(f[1])
    raise RuntimeError("no free high-speed vhci port")


def find_usb_device(timeout):
    """The imported device's sysfs directory (under vhci_hcd)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for dev in Path("/sys/bus/usb/devices").iterdir():
            try:
                ids = ((dev / "idVendor").read_text().strip(),
                       (dev / "idProduct").read_text().strip())
            except OSError:
                continue
            if ids == (VID, PID) and "vhci_hcd" in str(dev.resolve()):
                return dev
        time.sleep(0.2)
    return None


def find_disk(timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for dev in Path("/sys/block").glob("sd*"):
            vendor = dev / "device" / "vendor"
            if ("vhci_hcd" in str(dev.resolve()) and vendor.exists() and
                    vendor.read_text().strip() == "QEMU"):
                return Path("/dev") / dev.name
        time.sleep(0.2)
    return None


def diagnostics():
    """What the host kernel made of the imported device."""
    print("--- vhci status ---")
    print((VHCI / "status").read_text())
    print("--- USB devices ---")
    for dev in sorted(Path("/sys/bus/usb/devices").iterdir()):
        ids = [(dev / f).read_text().strip() for f in ("idVendor", "idProduct")
               if (dev / f).exists()]
        drv = dev / "driver"
        print(f"  {dev.name} {':'.join(ids)} driver="
              f"{drv.resolve().name if drv.exists() else '-'}")
    print("--- dmesg (tail) ---")
    print(subprocess.run(["dmesg"], capture_output=True, text=True).stdout[-6000:])


def report(ok, what):
    print(f"  {'PASS' if ok else 'FAIL'}  {what}")
    return ok


def run(tool, check):
    img = rt.storage_image()
    trace = ["-trace", "enable=usbip_server*"]
    results = []
    with rt.QemuUsbip(rt.storage_args(img) + trace) as q:
        if tool == "usbip":
            out = subprocess.run(["usbip", "--tcp-port", str(q.port), "list",
                                  "-r", "127.0.0.1"],
                                 capture_output=True, text=True, check=True).stdout
            print(out)
            results.append(report(f"{VID}:{PID}" in out,
                                  "usbip list shows the exported device"))
            subprocess.run(["usbip", "--tcp-port", str(q.port), "attach",
                            "-r", "127.0.0.1", "-b", "1-1"], check=True)
        else:
            c = q.client()
            rec = c.import_device("1-1")
            port = free_hs_port()
            (VHCI / "attach").write_text(
                f"{port} {c.sock.fileno()} {rec.devid} {rec.speed}")
            c.close()                   # the kernel holds its own reference

        dev = find_usb_device(30)
        results.append(report(dev is not None,
                              "host kernel enumerates the device"))
        if dev is None:
            diagnostics()
            return False
        strings = [(dev / f).read_text().strip()
                   for f in ("manufacturer", "product")]
        results.append(report(strings == ["QEMU", "QEMU USB HARDDRIVE"],
                              f"string descriptors {strings}"))
        busnum = int((dev / "busnum").read_text())
        devnum = int((dev / "devnum").read_text())
        port = int((dev / "devpath").read_text()) - 1   # vhci port

        bot = UsbfsBulkOnly(f"/dev/bus/usb/{busnum:03d}/{devnum:03d}")
        try:
            inq, status = bot.command(bytes([0x12, 0, 0, 0, 36, 0]), 36)
            results.append(report(status == 0 and inq[8:16] == b"QEMU    ",
                                  "usbfs Bulk-Only INQUIRY"))
            data, status = bot.command(
                struct.pack(">BBIBHB", 0x28, 0, 0, 0, 128, 0), 128 * 512)
            bad = [i for i in range(len(data) // 512)
                   if data[i * 512:i * 512 + 4] != i.to_bytes(4, "little")]
            results.append(report(
                status == 0 and len(data) == 128 * 512 and not bad,
                f"usbfs Bulk-Only READ(10) of 64 KiB matches the image "
                f"(got {len(data)} bytes, CSW status {status}, "
                f"mismatched blocks {bad[:8]}, first {data[:8].hex()})"))
        finally:
            bot.close()

        if check == "disk":
            with open(dev / "authorized", "w") as f:    # rebind usb-storage
                f.write("0")
            with open(dev / "authorized", "w") as f:
                f.write("1")
            disk = find_disk(30)
            ok = False
            if disk is not None:
                with open(disk, "rb") as f:
                    first = f.read(4096)
                ok = all(first[i * 512:i * 512 + 4] == i.to_bytes(4, "little")
                         for i in range(8))
            results.append(report(ok, f"usb-storage block device {disk} "
                                      "reads the image"))

        (VHCI / "detach").write_text(str(port))
        time.sleep(1)
        results.append(report(find_usb_device(0.1) is None,
                              "device removed after detach"))
        results.append(report(q.alive(), "QEMU still running"))
        if not all(results):
            diagnostics()
    return all(results)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", choices=["sysfs", "usbip"], default="sysfs")
    ap.add_argument("--check", choices=["usbfs", "disk"], default="usbfs")
    args = ap.parse_args()
    if os.geteuid() != 0 or not VHCI.exists():
        print("needs root and the vhci-hcd module (modprobe vhci-hcd)")
        return 1
    try:
        return 0 if run(args.tool, args.check) else 1
    finally:
        shutil.rmtree(rt.WORK, ignore_errors=True)      # root-owned files


if __name__ == "__main__":
    sys.exit(main())
