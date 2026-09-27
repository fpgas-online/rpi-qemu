#!/usr/bin/env python3
"""
The Raspberry Pi Zero's USB gadget on a real Linux USB host (#22).

The emulated Zero (raspi0, stock kernel, dwc2 in peripheral mode) runs a
CDC-ACM gadget with a shell on ttyGS0; QEMU exports it over USB/IP, and
this host's kernel imports it through vhci-hcd (its sysfs attach
interface -- what `usbip attach` does after its import request).  Then:

  - the host kernel enumerates and configures the gadget (its strings are
    read over USB/IP; the guest sees "configured");
  - through usbfs (kernel URBs through vhci-hcd, no class driver needed),
    a command runs in the Zero's ttyGS0 shell and its answer comes back;
  - detaching removes the device from the host and the Zero sees the
    session end.

Requirements: root, the vhci-hcd module loaded, and the raspi0 gadget
test images (see run-rpi0-gadget-test.py).

Usage: sudo QEMU_OVERRIDE=... python3 run-rpi0-gadget-vhci-test.py
"""
import ctypes
import errno
import fcntl
import os
import struct
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE))
from importlib import import_module  # noqa: E402

vt = import_module("run-usbip-vhci-test")
gt = import_module("run-rpi0-gadget-test")


class CtrlTransfer(ctypes.Structure):
    _fields_ = [("bRequestType", ctypes.c_uint8), ("bRequest", ctypes.c_uint8),
                ("wValue", ctypes.c_uint16), ("wIndex", ctypes.c_uint16),
                ("wLength", ctypes.c_uint16), ("timeout", ctypes.c_uint32),
                ("data", ctypes.c_void_p)]


USBDEVFS_CONTROL = vt._ioc(3, 0, ctypes.sizeof(CtrlTransfer))


class UsbfsAcm:
    """A CDC-ACM function driven through usbfs."""

    def __init__(self, devnode, comm=0, data=1, ep_in=0x81, ep_out=0x01):
        self.fd = os.open(devnode, os.O_RDWR)
        self.ifaces, self.ep_in, self.ep_out = (comm, data), ep_in, ep_out
        for ifno in self.ifaces:
            try:                        # detach cdc_acm if it bound
                fcntl.ioctl(self.fd, vt.USBDEVFS_IOCTL,
                            vt.UsbfsIoctl(ifno, vt.USBDEVFS_DISCONNECT, None))
            except OSError as e:
                if e.errno != errno.ENODATA:
                    raise
            fcntl.ioctl(self.fd, vt.USBDEVFS_CLAIMINTERFACE,
                        struct.pack("I", ifno))
        # DTR and RTS on, as cdc_acm does when the tty is opened
        fcntl.ioctl(self.fd, USBDEVFS_CONTROL,
                    CtrlTransfer(0x21, 0x22, 3, comm, 0, 1000, None))

    def close(self):
        for ifno in self.ifaces:
            fcntl.ioctl(self.fd, vt.USBDEVFS_RELEASEINTERFACE,
                        struct.pack("I", ifno))
        os.close(self.fd)

    def _bulk(self, ep, buf, timeout_ms):
        cbuf = (ctypes.c_char * len(buf)).from_buffer_copy(buf)
        xfer = vt.BulkTransfer(ep, len(buf), timeout_ms, ctypes.addressof(cbuf))
        try:
            n = fcntl.ioctl(self.fd, vt.USBDEVFS_BULK, xfer)
        except OSError as e:
            if e.errno == errno.ETIMEDOUT:
                return b""
            raise
        return bytes(cbuf)[:n]

    def roundtrip(self, timeout=60):
        got, deadline, next_cmd = b"", time.monotonic() + timeout, 0.0
        while b"ACM-42-OK" not in got and time.monotonic() < deadline:
            if time.monotonic() >= next_cmd:
                self._bulk(self.ep_out, b"echo ACM-$((6*7))-OK\n", 1000)
                next_cmd = time.monotonic() + 5
            got += self._bulk(self.ep_in, bytes(512), 500)
        return got


def main():
    if os.geteuid() != 0 or not vt.VHCI.exists():
        print("needs root and the vhci-hcd module (modprobe vhci-hcd)")
        return 1
    missing = [str(p) for p in (gt.QEMU, gt.KERNEL, gt.DTB, gt.INITRD)
               if not p.exists()]
    if missing:
        print("Missing prerequisites:\n  " + "\n  ".join(missing))
        return 1
    print("raspi0 USB gadget on this host's kernel (vhci-hcd) (#22)")
    g = gt.Guest("acm")
    results = []
    try:
        results.append(vt.report(g.wait_for("GADGET: READY", 240),
                                 "the Zero's gadget is bound"))
        c = g.client()
        rec = gt.wait_for_device(c, 60)
        c.import_device(rec.busid)
        port = vt.free_hs_port()
        (vt.VHCI / "attach").write_text(
            f"{port} {c.sock.fileno()} {rec.devid} {rec.speed}")
        c.close()                       # the kernel holds its own reference

        dev = vt.find_usb_device(30, "1d6b", "0104")
        results.append(vt.report(dev is not None,
                                 "host kernel enumerates the gadget"))
        if dev is None:
            vt.diagnostics()
            return 1
        strings = [(dev / f).read_text().strip()
                   for f in ("manufacturer", "product", "serial")]
        results.append(vt.report(
            strings == ["rpi-qemu", "Raspberry Pi Zero gadget", "rpi-qemu-0001"],
            f"gadget strings {strings}"))
        results.append(vt.report(g.wait_for("UDC state: configured", 30),
                                 "the host configured the gadget"))
        drv = dev.parent / f"{dev.name}:1.0" / "driver"
        print(f"  (host driver on the ACM interface: "
              f"{drv.resolve().name if drv.exists() else 'none'})")

        acm = UsbfsAcm(f"/dev/bus/usb/{int((dev / 'busnum').read_text()):03d}/"
                       f"{int((dev / 'devnum').read_text()):03d}")
        try:
            got = acm.roundtrip()
        finally:
            acm.close()
        results.append(vt.report(b"ACM-42-OK" in got,
                                 "a command runs in the Zero's ttyGS0 shell"))

        mark = len(g.output())
        (vt.VHCI / "detach").write_text(str(port))
        time.sleep(1)
        results.append(vt.report(vt.find_usb_device(0.1, "1d6b", "0104") is None,
                                 "gadget removed from the host after detach"))
        results.append(vt.report(g.wait_for("UDC state: not attached", 30,
                                            after=mark),
                                 "the Zero sees the host go"))
        results.append(vt.report(g.proc.poll() is None, "QEMU still running"))
        if not all(results):
            vt.diagnostics()
    finally:
        out = g.finish()
        if not all(results):
            print("--- guest serial (tail) ---")
            print("".join(out.splitlines(True)[-40:]))
        if g.proc.poll() is None:
            g.proc.kill()
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
