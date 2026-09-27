#!/usr/bin/env python3
"""
USB/IP server test (#22).

Runs QEMU with no machine (-M none), a usbip-server and QEMU's own USB
devices on its port, and drives it with ci/usbip_client.py — the protocol
Linux's vhci-hcd speaks.

Requirements:
  - QEMU with the rpi-qemu patches: qemu-rpi-system-aarch64 or QEMU_OVERRIDE

Usage: uv run run-usbip-test.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE / "ci"))
import usbip_client as u  # noqa: E402

_qemu_override = os.environ.get("QEMU_OVERRIDE")
if _qemu_override:
    QEMU = Path(_qemu_override)
else:
    QEMU = Path(shutil.which("qemu-rpi-system-aarch64") or
                "/usr/bin/qemu-rpi-system-aarch64")
WORK = BASE / "tmp" / "usbip-test"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Qmp:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(str(path))
        self.f = self.sock.makefile("rw")
        json.loads(self.f.readline())             # greeting
        self.cmd("qmp_capabilities")

    def cmd(self, name, **args):
        self.f.write(json.dumps({"execute": name, "arguments": args}) + "\n")
        self.f.flush()
        while True:
            msg = json.loads(self.f.readline())
            if "return" in msg:
                return msg["return"]
            if "error" in msg:
                raise RuntimeError(f"QMP {name}: {msg['error']}")

    def close(self):
        self.sock.close()


class QemuUsbip:
    """QEMU -M none with a usbip-server; `devices` are extra QEMU args."""

    def __init__(self, devices=()):
        self.devices = list(devices)

    def __enter__(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.port = free_port()
        qmp_path = WORK / f"qmp-{self.port}.sock"
        self.proc = subprocess.Popen(
            [str(QEMU), "-M", "none", "-nodefaults", "-display", "none",
             "-chardev", f"socket,id=usbipchr,host=127.0.0.1,port={self.port},"
                         "server=on,wait=off",
             "-device", "usbip-server,id=usbip0,chardev=usbipchr",
             "-qmp", f"unix:{qmp_path},server=on,wait=off", *self.devices],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 20
        while not qmp_path.exists():
            if self.proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"QEMU did not start: {self.proc.communicate()}")
            time.sleep(0.05)
        self.qmp = Qmp(qmp_path)
        return self

    def client(self):
        return u.UsbipClient.connect("127.0.0.1", self.port)

    def alive(self):
        return self.proc.poll() is None

    def __exit__(self, *exc):
        self.qmp.close()
        self.proc.terminate()
        try:
            _, err = self.proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            _, err = self.proc.communicate()
        # QEMU announcing our own SIGTERM is expected; show anything else
        err = "\n".join(l for l in err.splitlines()
                         if "terminating on signal 15" not in l)
        if err.strip():
            print(f"    QEMU stderr: {err.strip()}")


def expect(cond, what):
    if not cond:
        raise AssertionError(what)


def test_empty_port_lists_no_devices():
    with QemuUsbip() as q, q.client() as c:
        expect(c.devlist() == [], "devlist of an empty port is not empty")
        expect(q.alive(), "QEMU exited")


def test_chardev_is_required():
    r = subprocess.run([str(QEMU), "-M", "none", "-nodefaults", "-display",
                        "none", "-device", "usbip-server"],
                       capture_output=True, text=True, timeout=30)
    expect(r.returncode != 0 and "chardev" in r.stderr,
           f"usbip-server without chardev: rc={r.returncode} {r.stderr!r}")


STORAGE_BLOCKS = 2048                     # 1 MiB image


def storage_image():
    """A raw image whose every 512-byte block starts with its LBA."""
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / "storage.img"
    with open(path, "wb") as f:
        for lba in range(STORAGE_BLOCKS):
            f.write(lba.to_bytes(4, "little") + bytes([lba & 0xff]) * 508)
    return path


def storage_args(path):
    return ["-drive", f"if=none,id=disk0,format=raw,file={path}",
            "-device", "usb-storage,bus=usbip0.0,drive=disk0"]


def test_storage_is_listed():
    with QemuUsbip(storage_args(storage_image())) as q, q.client() as c:
        devs = c.devlist()
        expect(len(devs) == 1, f"expected one device, got {devs}")
        d = devs[0]
        expect((d.busid, d.busnum, d.devnum) == ("1-1", 1, 1), f"ids {d}")
        expect(d.speed == 3, f"usb-storage should be high speed (3), got {d.speed}")
        expect((d.id_vendor, d.id_product) == (0x46f4, 0x0001), f"VID/PID {d}")
        expect(d.interfaces == [(8, 6, 0x50)], f"interfaces {d.interfaces}")


def test_import_unknown_busid_is_refused():
    with QemuUsbip(storage_args(storage_image())) as q, q.client() as c:
        try:
            c.import_device("9-9")
        except u.UsbipError:
            return
        raise AssertionError("import of 9-9 succeeded")


def test_list_then_import_on_new_connections():
    """What `usbip list -r` followed by `usbip attach` does."""
    with QemuUsbip(storage_args(storage_image())) as q:
        with q.client() as c:
            expect(len(c.devlist()) == 1, "devlist")
        with q.client() as c:
            rec = c.import_device("1-1")
            expect(rec.id_product == 0x0001, f"import record {rec}")


def test_control_transfers():
    with QemuUsbip(storage_args(storage_image())) as q, q.client() as c:
        c.import_device("1-1")
        dd = c.get_descriptor(1, 0, 18)
        expect(dd.status == 0 and dd.actual_length == 18, f"device descriptor {dd}")
        expect(dd.data[8:12] == bytes([0xf4, 0x46, 0x01, 0x00]), f"VID/PID {dd.data.hex()}")
        cfg = c.get_descriptor(2, 0, 9)
        total = int.from_bytes(cfg.data[2:4], "little")
        full = c.get_descriptor(2, 0, total)
        expect(full.actual_length == total, f"config descriptor {full}")
        expect(c.set_configuration(1).status == 0, "SET_CONFIGURATION")


def test_bulk_mass_storage():
    img = storage_image()
    pattern = os.urandom(8 * 512)
    with QemuUsbip(storage_args(img)) as q, q.client() as c:
        c.import_device("1-1")
        expect(c.set_configuration(1).status == 0, "SET_CONFIGURATION")
        bot = u.BulkOnlyStorage(c)
        expect(bot.inquiry()[8:16] == b"QEMU    ", "INQUIRY vendor")
        bot.ready()
        expect(bot.read_capacity() == (STORAGE_BLOCKS, 512), "READ CAPACITY")
        data = bot.read10(0, 128)                     # one 64 KiB transfer
        for lba in range(128):
            blk = data[lba * 512:(lba + 1) * 512]
            expect(blk[:4] == lba.to_bytes(4, "little"), f"LBA {lba} content")
        bot.write10(100, pattern)
        expect(bot.read10(100, 8) == pattern, "read-back of written blocks")
    with open(img, "rb") as f:
        f.seek(100 * 512)
        expect(f.read(len(pattern)) == pattern, "image file lacks the written data")


KBD_ARGS = ["-device", "usb-kbd,bus=usbip0.0,id=kbd0"]


def kbd_attached(q):
    c = q.client()
    c.import_device("1-1")
    expect(c.set_configuration(1).status == 0, "SET_CONFIGURATION")
    return c


def test_interrupt_in_waits_for_data():
    """A HID interrupt IN URB stays pending while the device NAKs, and
    completes when a key is pressed (the device wakes the endpoint)."""
    with QemuUsbip(KBD_ARGS) as q, kbd_attached(q) as c:
        seq = c.submit(1, u.DIR_IN, 8)
        expect(c.poll(seq, 0.5) is None, "interrupt IN completed with no key pressed")
        q.qmp.cmd("send-key", keys=[{"type": "qcode", "data": "a"}])
        ret = c.wait(seq)
        expect(ret.status == 0 and ret.data[2] == 0x04,
               f"expected usage 0x04 (a) in the report, got {ret}")


def test_unlink_pending_urb():
    with QemuUsbip(KBD_ARGS) as q, kbd_attached(q) as c:
        victim = c.submit(1, u.DIR_IN, 8)
        expect(c.poll(victim, 0.3) is None, "URB completed before the unlink")
        ret = c.wait(c.unlink(victim))
        expect(ret.status == -u.ECONNRESET, f"RET_UNLINK status {ret.status}")
        q.qmp.cmd("send-key", keys=[{"type": "qcode", "data": "b"}])
        expect(c.poll(victim, 0.5) is None, "RET_SUBMIT arrived for an unlinked URB")
        seq = c.submit(1, u.DIR_IN, 8)              # the key press is still there
        expect(c.wait(seq).data[2] == 0x05, "the next URB did not get the report")


def test_unlink_completed_urb():
    with QemuUsbip(KBD_ARGS) as q, kbd_attached(q) as c:
        seq = c.submit(0, u.DIR_IN, 18, setup=bytes([0x80, 6, 0, 1, 0, 0, 18, 0]))
        expect(c.wait(seq).status == 0, "GET_DESCRIPTOR")
        ret = c.wait(c.unlink(seq))
        expect(ret.status == 0, f"RET_UNLINK after completion: status {ret.status}")


AUDIO_ARGS = ["-audiodev", "none,id=snd0",
              "-device", "usb-audio,bus=usbip0.0,audiodev=snd0"]
AUDIO_PACKET = 192          # 48 kHz, 16-bit stereo, 1 ms


def iso_out(c, packets):
    iso = [u.IsoPacket(i * AUDIO_PACKET, AUDIO_PACKET) for i in range(packets)]
    data = bytes(AUDIO_PACKET * packets)
    return c.wait(c.submit(1, u.DIR_OUT, len(data), data, flags=u.URB_ISO_ASAP,
                           iso=iso, interval=1))


def test_isochronous_out():
    with QemuUsbip(AUDIO_ARGS) as q, q.client() as c:
        rec = c.import_device("1-1")
        expect(rec.speed == 2, f"usb-audio is full speed (2), got {rec.speed}")
        expect(c.set_configuration(1).status == 0, "SET_CONFIGURATION")
        expect(c.set_interface(1, 1).status == 0, "SET_INTERFACE 1/1")
        ret = iso_out(c, 8)
        expect(ret.status == 0 and ret.error_count == 0, f"URB {ret}")
        expect(ret.number_of_packets == 8 and ret.actual_length == 8 * AUDIO_PACKET,
               f"totals {ret}")
        expect(all(p.status == 0 and p.actual_length == AUDIO_PACKET for p in ret.iso),
               f"packets {ret.iso}")


def test_isochronous_to_disabled_stream_stalls_each_packet():
    """Alternate setting 0 has no endpoint: every packet stalls, and the
    stream stays in sync (the descriptors after the data were consumed)."""
    with QemuUsbip(AUDIO_ARGS) as q, q.client() as c:
        c.import_device("1-1")
        expect(c.set_configuration(1).status == 0, "SET_CONFIGURATION")
        ret = iso_out(c, 4)
        expect(ret.error_count == 4 and all(p.status == -u.EPIPE for p in ret.iso),
               f"packets {ret.iso}")
        expect(c.get_descriptor(1, 0, 18).status == 0, "stream out of sync after ISO")


def test_reconnect_after_client_vanishes():
    """Closing the socket with an URB pending leaves QEMU healthy and the
    device importable again (freshly enumerated)."""
    with QemuUsbip(KBD_ARGS) as q:
        c = kbd_attached(q)
        c.submit(1, u.DIR_IN, 8)
        c.close()
        with q.client() as c2:
            expect(len(c2.devlist()) == 1, "devlist after reconnect")
        with kbd_attached(q) as c3:
            expect(c3.get_descriptor(1, 0, 18).status == 0, "descriptor after reconnect")
        expect(q.alive(), "QEMU exited")


def test_malformed_op_closes_only_that_connection():
    with QemuUsbip(KBD_ARGS) as q:
        with q.client() as c:
            c.sock.sendall(bytes.fromhex("0200800500000000"))   # version 0x0200
            expect(c.closed_by_server(5), "server kept a bad-version client")
        with q.client() as c:
            expect(len(c.devlist()) == 1, "devlist on a new connection")


def test_malformed_command_closes_attached_client():
    with QemuUsbip(KBD_ARGS) as q:
        with kbd_attached(q) as c:
            c.sock.sendall(bytes([0, 0, 0, 0x99]) + bytes(u.HDR_LEN - 4))
            expect(c.closed_by_server(5), "server kept a client sending command 0x99")
        with kbd_attached(q) as c:
            expect(c.get_descriptor(1, 0, 18).status == 0, "import after the bad client")


def test_device_unplug_closes_the_connection():
    """A USB/IP exporter reports a removed device by closing the connection
    (as Linux's usbip-host does)."""
    with QemuUsbip(KBD_ARGS) as q:
        with kbd_attached(q) as c:
            c.submit(1, u.DIR_IN, 8)
            q.qmp.cmd("device_del", id="kbd0")
            expect(c.closed_by_server(5), "connection open after device_del")
        deadline = time.monotonic() + 5
        while True:
            with q.client() as c:
                if c.devlist() == []:
                    break
            expect(time.monotonic() < deadline, "unplugged device still listed")
            time.sleep(0.1)


def test_system_reset_closes_the_connection():
    with QemuUsbip(KBD_ARGS) as q:
        with kbd_attached(q) as c:
            q.qmp.cmd("system_reset")
            expect(c.closed_by_server(5), "connection open after system_reset")
        with kbd_attached(q) as c:
            expect(c.get_descriptor(1, 0, 18).status == 0, "import after reset")


TESTS = [
    ("Empty port: OP_REQ_DEVLIST lists no devices", test_empty_port_lists_no_devices),
    ("usbip-server refuses to start without a chardev", test_chardev_is_required),
    ("usb-storage is listed with its IDs, speed and interface", test_storage_is_listed),
    ("Import of an unknown busid is refused", test_import_unknown_busid_is_refused),
    ("List then import on separate connections", test_list_then_import_on_new_connections),
    ("Control transfers: descriptors, SET_CONFIGURATION", test_control_transfers),
    ("Bulk: mass storage INQUIRY/READ CAPACITY/READ(10)/WRITE(10)", test_bulk_mass_storage),
    ("Interrupt IN waits (NAK) and completes on a key press", test_interrupt_in_waits_for_data),
    ("CMD_UNLINK of a pending URB: -ECONNRESET, no RET_SUBMIT", test_unlink_pending_urb),
    ("CMD_UNLINK of a completed URB: status 0", test_unlink_completed_urb),
    ("Isochronous OUT: per-packet lengths and status", test_isochronous_out),
    ("Isochronous to a disabled stream: each packet stalls, stream in sync",
     test_isochronous_to_disabled_stream_stalls_each_packet),
    ("Client vanishing with an URB pending; reconnect works", test_reconnect_after_client_vanishes),
    ("Malformed OP request closes only that connection", test_malformed_op_closes_only_that_connection),
    ("Malformed command closes the attached client", test_malformed_command_closes_attached_client),
    ("Device unplug closes the connection; device no longer listed", test_device_unplug_closes_the_connection),
    ("System reset closes the connection; re-import works", test_system_reset_closes_the_connection),
]


def main():
    print("USB/IP server test (#22)")
    print(f"  QEMU: {QEMU}")
    failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001 - report every failure
            failed += 1
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
    shutil.rmtree(WORK, ignore_errors=True)
    print("\n  ALL TESTS PASSED" if not failed else f"\n  {failed} TEST(S) FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
