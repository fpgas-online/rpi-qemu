#!/usr/bin/env python3
"""
RPi Zero (raspi0) USB gadget test (rpi-qemu#22).

Boots the stock Raspberry Pi Zero W kernel on raspi0 with the upstream
dwc2 driver in peripheral mode (dtoverlay=dwc2, applied with fdtoverlay)
and a configfs gadget, while QEMU exports the gadget over USB/IP
(usbip-server + dwc2-gadget).  The harness is the USB host: it imports the
gadget with ci/usbip_client.py -- the protocol Linux's vhci-hcd speaks --
and drives each function the way a host's driver would:

  - CDC-ACM: a shell on the gadget's ttyGS0 runs a command;
  - CDC-ECM and CDC-NCM: ARP and ICMP echo with the gadget's usb0;
  - mass storage: SCSI over Bulk-Only reads the gadget's image and writes
    to it (the guest then checks its file);
  - a composite gadget with all of them;

and the #22 scenarios: no host at all, a host connected at boot, a host
connecting after boot, and detach/reattach.

Requirements:
  - QEMU with the rpi-qemu patches: qemu-rpi-system-aarch64 or QEMU_OVERRIDE
  - test-images/rpi0/kernel.img, bcm2708-rpi-zero-w-dwc2.dtb (the Zero W
    DTB with overlays/dwc2.dtbo applied)
  - test-images/test-initramfs-rpi0-gadget.cpio.gz
    (build-initramfs.py --target rpi0-gadget)

Usage: uv run run-rpi0-gadget-test.py [scenario ...]
"""
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
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
KERNEL = BASE / "test-images" / "rpi0" / "kernel.img"
DTB = BASE / "test-images" / "rpi0" / "bcm2708-rpi-zero-w-dwc2.dtb"
INITRD = BASE / "test-images" / "test-initramfs-rpi0-gadget.cpio.gz"
BOOTARGS = "console=serial0,115200 earlycon rdinit=/init"

HOST_MAC = bytes.fromhex("020000000001")
HOST_IP = bytes([192, 168, 7, 1])
GUEST_IP = bytes([192, 168, 7, 2])


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Guest:
    """The raspi0 guest with a gadget exported by usbip-server."""

    def __init__(self, funcs):
        self.port = free_port()
        self.proc = subprocess.Popen(
            [str(QEMU), "-M", "raspi0",
             "-kernel", str(KERNEL), "-dtb", str(DTB), "-initrd", str(INITRD),
             "-append", f"{BOOTARGS} gadget={funcs}",
             "-serial", "null", "-serial", "stdio",
             "-display", "none", "-monitor", "none", "-no-reboot",
             "-chardev", f"socket,id=usbipchr,host=127.0.0.1,port={self.port},"
                         "server=on,wait=off",
             "-device", "usbip-server,id=usbip0,chardev=usbipchr",
             "-device", "dwc2-gadget,bus=usbip0.0",
             *os.environ.get("GADGET_QEMU_ARGS", "").split()],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        self.out, self.err = [], []
        for stream, sink in ((self.proc.stdout, self.out),
                             (self.proc.stderr, self.err)):
            threading.Thread(target=lambda s=stream, k=sink:
                             [k.append(line) for line in iter(s.readline, "")],
                             daemon=True).start()

    def output(self):
        return "".join(self.out)

    def wait_for(self, text, timeout, after=0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if text in self.output()[after:]:
                return True
            if self.proc.poll() is not None:
                break
            time.sleep(0.2)
        return text in self.output()[after:]

    def client(self):
        # QEMU opens the listening socket as it starts
        deadline = time.monotonic() + 20
        while True:
            try:
                return u.UsbipClient.connect("127.0.0.1", self.port)
            except ConnectionRefusedError:
                if time.monotonic() > deadline or self.proc.poll() is not None:
                    raise
                time.sleep(0.1)

    def finish(self):
        """Tell the init script to wrap up; return the serial output."""
        if self.proc.poll() is None:
            self.proc.stdin.write("done\n")
            self.proc.stdin.flush()
            self.wait_for("=== raspi0 gadget test complete ===", 60)
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        return self.output()


def expect(cond, what):
    if not cond:
        raise AssertionError(what)


# USB host side

def parse_config(raw):
    """Interfaces of a configuration descriptor, with their endpoints."""
    ifaces, cur, i = [], None, 0
    while i + 2 <= len(raw) and raw[i] >= 2:
        length, dtype = raw[i], raw[i + 1]
        if dtype == 4:
            cur = dict(num=raw[i + 2], alt=raw[i + 3], cls=raw[i + 5],
                       sub=raw[i + 6], proto=raw[i + 7], eps=[])
            ifaces.append(cur)
        elif dtype == 5 and cur is not None:
            cur["eps"].append(raw[i + 2])
            cur.setdefault("types", {})[raw[i + 2]] = raw[i + 3] & 3
        i += length
    return ifaces


def bulk_eps(iface):
    ep_in = next(e & 0x0f for e in iface["eps"] if e & 0x80)
    ep_out = next(e for e in iface["eps"] if not e & 0x80)
    return ep_in, ep_out


def wait_for_device(c, timeout):
    """OP_REQ_DEVLIST until the gadget shows up (the guest pulls up)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        devs = c.devlist()
        if devs:
            return devs[0]
        time.sleep(1)
    raise AssertionError(f"no device exported within {timeout}s")


def attach(g, timeout=60):
    """Connect, import the gadget and configure it like a host would."""
    c = g.client()
    rec = wait_for_device(c, timeout)
    expect((rec.id_vendor, rec.id_product) == (0x1d6b, 0x0104),
           f"gadget VID:PID {rec.id_vendor:04x}:{rec.id_product:04x}")
    c.import_device(rec.busid)
    dd = c.get_descriptor(1, 0, 18)
    expect(dd.status == 0 and dd.actual_length == 18, f"device descriptor {dd}")
    head = c.get_descriptor(2, 0, 9)
    cfg = c.get_descriptor(2, 0, int.from_bytes(head.data[2:4], "little"))
    expect(cfg.status == 0, f"configuration descriptor {cfg}")
    expect(c.set_configuration(1).status == 0, "SET_CONFIGURATION")
    return c, rec, parse_config(cfg.data)


def acm_roundtrip(c, ifaces, timeout=60):
    comm = next(i for i in ifaces if (i["cls"], i["sub"]) == (2, 2))
    data = next(i for i in ifaces if i["cls"] == 10 and i["num"] == comm["num"] + 1)
    ep_in, ep_out = bulk_eps(data)
    # 115200 8N1, DTR and RTS on: what a host's cdc_acm does on open.
    c.control(0x21, 0x20, 0, comm["num"], struct.pack("<IBBB", 115200, 0, 0, 8))
    c.control(0x21, 0x22, 3, comm["num"], b"")
    got, seq = b"", c.submit(ep_in, u.DIR_IN, 512)
    deadline = time.monotonic() + timeout
    next_cmd = 0.0
    while b"ACM-42-OK" not in got:
        now = time.monotonic()
        expect(now < deadline, f"no answer on ttyGS0; got {got[-200:]!r}")
        if now >= next_cmd:        # (re)send until the shell is there
            r = c.wait(c.submit(ep_out, u.DIR_OUT, data=b"echo ACM-$((6*7))-OK\n"))
            expect(r.status == 0, f"bulk OUT status {r.status}")
            next_cmd = now + 5
        r = c.poll(seq, 0.5)
        if r is not None:
            expect(r.status == 0, f"bulk IN status {r.status}")
            got += r.data
            seq = c.submit(ep_in, u.DIR_IN, 512)
    return got


def checksum(data):
    if len(data) % 2:
        data += b"\0"
    s = sum(struct.unpack(f"!{len(data) // 2}H", data))
    s = (s >> 16) + (s & 0xffff)
    s += s >> 16
    return ~s & 0xffff


def arp_request():
    arp = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1, HOST_MAC, HOST_IP,
                      bytes(6), GUEST_IP)
    return (b"\xff" * 6 + HOST_MAC + b"\x08\x06" + arp).ljust(60, b"\0")


def icmp_echo(guest_mac, seq):
    payload = b"rpi-qemu-usb-gadget-ping" * 4
    icmp = struct.pack("!BBHHH", 8, 0, 0, 0x2222, seq) + payload
    icmp = icmp[:2] + struct.pack("!H", checksum(icmp)) + icmp[4:]
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(icmp), seq, 0, 64, 1,
                     0, HOST_IP, GUEST_IP)
    ip = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:]
    return guest_mac + HOST_MAC + b"\x08\x00" + ip + icmp, payload


def ncm_wrap(frame, seq):
    """One Ethernet frame in an NCM transfer block (NTH16 + NDP16)."""
    nth_len, ndp_len = 12, 16
    dgram = nth_len + ndp_len
    block = dgram + len(frame)
    nth = struct.pack("<4sHHHH", b"NCMH", nth_len, seq, block, nth_len)
    ndp = struct.pack("<4sHHHHHH", b"NCM0", ndp_len, 0, dgram, len(frame), 0, 0)
    return nth + ndp + frame


def ncm_unwrap(ntb):
    """The Ethernet frames of an NCM transfer block."""
    if len(ntb) < 12 or ntb[:4] != b"NCMH":
        return []
    ndp = struct.unpack_from("<H", ntb, 10)[0]
    frames = []
    while ndp and ndp + 8 <= len(ntb):
        sig, length, nxt = struct.unpack_from("<4sHH", ntb, ndp)
        if sig[:3] != b"NCM":
            break
        for off in range(ndp + 8, ndp + length, 4):
            index, dlen = struct.unpack_from("<HH", ntb, off)
            if not index:
                break
            frames.append(ntb[index:index + dlen])
        ndp = nxt
    return frames


def net_ping(c, ifaces, ncm, timeout=60):
    """ARP then ICMP echo with the gadget's usb0; returns the reply count."""
    sub = 0x0d if ncm else 0x06
    comm = next(i for i in ifaces if (i["cls"], i["sub"]) == (2, sub))
    data = next(i for i in ifaces
                if i["cls"] == 10 and i["alt"] == 1 and i["eps"]
                and i["num"] == comm["num"] + 1)
    ep_in, ep_out = bulk_eps(data)
    expect(c.set_interface(data["num"], 1).status == 0, "SET_INTERFACE data 1")
    in_len = 16384 if ncm else 1536
    ntb_seq = [0]

    def send(frame):
        ntb_seq[0] += 1
        blob = ncm_wrap(frame, ntb_seq[0]) if ncm else frame
        r = c.wait(c.submit(ep_out, u.DIR_OUT, data=blob))
        expect(r.status == 0, f"bulk OUT status {r.status}")

    pending = [c.submit(ep_in, u.DIR_IN, in_len)]

    def frames(wait):
        r = c.poll(pending[0], wait)
        if r is None:
            return []
        expect(r.status == 0, f"bulk IN status {r.status}")
        pending[0] = c.submit(ep_in, u.DIR_IN, in_len)
        return ncm_unwrap(r.data) if ncm else [r.data]

    guest_mac, deadline = None, time.monotonic() + timeout
    next_arp = 0.0
    while guest_mac is None:
        expect(time.monotonic() < deadline, "no ARP reply from the gadget")
        if time.monotonic() >= next_arp:
            send(arp_request())
            next_arp = time.monotonic() + 2
        for f in frames(0.5):
            if f[12:14] == b"\x08\x06" and f[20:22] == b"\x00\x02" and \
               f[28:32] == GUEST_IP:
                guest_mac = f[22:28]
    replies = 0
    for seq in range(1, 4):
        frame, payload = icmp_echo(guest_mac, seq)
        send(frame)
        end = time.monotonic() + 10
        got = False
        while not got and time.monotonic() < end:
            for f in frames(0.5):
                if f[12:14] == b"\x08\x00" and f[23] == 1 and f[34] == 0 and \
                   struct.unpack_from("!H", f, 40)[0] == seq and \
                   f[42:42 + len(payload)] == payload:
                    got = True
        expect(got, f"no ICMP echo reply {seq} from the gadget")
        replies += 1
    return replies


def mass_storage(c, ifaces):
    ms = next(i for i in ifaces if (i["cls"], i["sub"], i["proto"]) == (8, 6, 0x50))
    ep_in, ep_out = bulk_eps(ms)
    bot = u.BulkOnlyStorage(c, ep_in=ep_in, ep_out=ep_out)
    inq = bot.inquiry()
    expect(len(inq) == 36, f"INQUIRY {inq!r}")
    bot.ready()
    expect(bot.read_capacity() == (2048, 512), "READ CAPACITY")
    first = bot.read10(0, 128)                       # 64 KiB in one transfer
    expect(first.startswith(b"RPI-QEMU-GADGET-MS"), f"image LBA 0 {first[:32]!r}")
    block = b"WRITTEN-BY-USBIP-HOST".ljust(512, b"\xa5")
    bot.write10(100, block)
    expect(bot.read10(100, 1) == block, "read-back of LBA 100")


SS_PATTERN = bytes(j % 63 for j in range(4096))     # SourceSink pattern=1


def source_sink_iso(c, ifaces, rounds=20, depth=3):
    """SourceSink with isochronous endpoints (alternate setting 1), streamed
    the way a host's audio or video driver does it, with several URBs in
    flight: after the first two URBs, at least 85% of the IN packets carry
    the pattern and 85% of the OUT packets are taken (the guest's sink
    checks every byte), with no packet errors.  (A busy guest may miss an
    occasional frame, as on hardware.)"""
    alt1 = next(i for i in ifaces if i["cls"] == 0xff and i["alt"] == 1)
    iso = [e for e, t in alt1["types"].items() if t == 1]
    iso_in = next(e & 0x0f for e in iso if e & 0x80)
    iso_out = next(e for e in iso if not e & 0x80)
    expect(c.set_interface(alt1["num"], 1).status == 0,
           "SET_INTERFACE SourceSink 1")
    packets = [u.IsoPacket(k * 1024, 1024) for k in range(8)]
    out = SS_PATTERN[:1024] * 8

    def stream(submit):
        pending = [submit() for _ in range(depth)]
        done = []
        while len(done) < rounds:
            done.append(c.wait(pending.pop(0)))
            pending.append(submit())
        for seq in pending:
            c.wait(seq)
        return done

    urbs = stream(lambda: c.submit(iso_in, u.DIR_IN, 8 * 1024, iso=packets,
                                   flags=u.URB_ISO_ASAP, interval=8))
    full = 0
    for n, r in enumerate(urbs):
        expect(r.status == 0 and r.error_count == 0,
               f"ISO IN URB {n}: status {r.status}, errors {r.error_count}")
        off = 0
        for pk in r.iso:
            data = r.data[off:off + pk.actual_length]
            off += pk.actual_length
            expect(data == SS_PATTERN[:len(data)],
                   f"ISO IN URB {n} packet {data[:16].hex()}...")
            full += n >= 2 and pk.actual_length == 1024
    total = (rounds - 2) * len(packets)
    expect(full >= 0.85 * total, f"ISO IN: {full} of {total} packets carried data")
    urbs = stream(lambda: c.submit(iso_out, u.DIR_OUT, len(out), out,
                                   iso=packets, flags=u.URB_ISO_ASAP,
                                   interval=8))
    taken = 0
    for n, r in enumerate(urbs):
        expect(r.status == 0 and r.error_count == 0,
               f"ISO OUT URB {n}: status {r.status}, errors {r.error_count}")
        taken += n >= 2 and sum(pk.actual_length == 1024 for pk in r.iso)
    expect(taken >= 0.85 * total, f"ISO OUT: {taken} of {total} packets taken")
    return full, taken


# Scenarios

def scenario(funcs):
    def wrap(fn):
        fn.funcs = funcs
        return fn
    return wrap


@scenario("acm")
def no_host(g):
    """No host: the Zero boots, the gadget binds and stays unattached."""
    expect(g.wait_for("GADGET: READY", 240), "gadget never became ready")
    time.sleep(5)
    out = g.finish()
    expect("UDC state: not attached" in out, "UDC not 'not attached'")
    expect("UDC state: configured" not in out, "configured without a host")


@scenario("acm")
def host_at_boot(g):
    """A host is on the cable from power-on: the gadget enumerates when the
    guest pulls up, with no waiting for it at boot."""
    c = g.client()                      # VBUS on before the kernel runs
    rec = wait_for_device(c, 240)
    c.import_device(rec.busid)
    head = c.get_descriptor(2, 0, 9)
    cfg = c.get_descriptor(2, 0, int.from_bytes(head.data[2:4], "little"))
    expect(c.set_configuration(1).status == 0, "SET_CONFIGURATION")
    acm_roundtrip(c, parse_config(cfg.data))
    expect(g.wait_for("UDC state: configured", 30), "UDC never configured")
    c.close()
    g.finish()


@scenario("acm")
def host_after_boot_and_reattach(g):
    """A host connects after boot; detaching removes the gadget from it (the
    guest sees the session end) and attaching again works."""
    expect(g.wait_for("GADGET: READY", 240), "gadget never became ready")
    c, _, ifaces = attach(g)
    acm_roundtrip(c, ifaces)
    expect(g.wait_for("UDC state: configured", 30), "UDC never configured")
    mark = len(g.output())
    c.close()
    expect(g.wait_for("UDC state: not attached", 30, after=mark),
           "guest did not see the host go away")
    c, _, ifaces = attach(g)
    acm_roundtrip(c, ifaces)
    c.close()
    g.finish()


@scenario("ecm")
def ecm(g):
    expect(g.wait_for("GADGET: READY", 240), "gadget never became ready")
    c, _, ifaces = attach(g)
    expect(net_ping(c, ifaces, ncm=False) == 3, "ECM ping")
    c.close()
    out = g.finish()
    expect("usb0: rx_packets=" in out, "usb0 statistics missing")


@scenario("ncm")
def ncm(g):
    expect(g.wait_for("GADGET: READY", 240), "gadget never became ready")
    c, _, ifaces = attach(g)
    expect(net_ping(c, ifaces, ncm=True) == 3, "NCM ping")
    c.close()
    g.finish()


@scenario("ms")
def mass_storage_rw(g):
    expect(g.wait_for("GADGET: READY", 240), "gadget never became ready")
    c, _, ifaces = attach(g)
    mass_storage(c, ifaces)
    c.close()
    out = g.finish()
    expect("MS image LBA 100: [WRITTEN-BY-USBIP-HOST]" in out,
           "guest's image does not hold the host's write")


@scenario("acm,ecm,ms")
def composite(g):
    expect(g.wait_for("GADGET: READY", 240), "gadget never became ready")
    c, _, ifaces = attach(g)
    acm_roundtrip(c, ifaces)
    expect(net_ping(c, ifaces, ncm=False) == 3, "ECM ping")
    mass_storage(c, ifaces)
    c.close()
    out = g.finish()
    expect("MS image LBA 100: [WRITTEN-BY-USBIP-HOST]" in out,
           "guest's image does not hold the host's write")


@scenario("sslb")
def isochronous(g):
    expect(g.wait_for("GADGET: READY", 240), "gadget never became ready")
    c, _, ifaces = attach(g)
    source_sink_iso(c, ifaces)
    c.close()
    g.finish()


SCENARIOS = [
    ("No host: boots, gadget unattached", no_host),
    ("Host at boot: enumerates, ACM shell", host_at_boot),
    ("Host after boot: ACM shell; detach, reattach", host_after_boot_and_reattach),
    ("CDC-ECM: ARP + ping with usb0", ecm),
    ("CDC-NCM: ARP + ping with usb0 (NTB framing)", ncm),
    ("Mass storage: read, write, guest sees the write", mass_storage_rw),
    ("Composite ACM + ECM + mass storage", composite),
    ("Isochronous IN and OUT (SourceSink pattern)", isochronous),
]

GUEST_FAILURES = ("Oops", "WARNING:", "BUG:", "Kernel panic",
                  "Invalid parameter", "insmod", "HANG", "bad OUT byte")
# the dwc2 driver's complaints about the core (timeouts waiting for a
# bit the core should have set, failed requests)
DWC2_COMPLAINT = re.compile(r"dwc2 \S+: .*(timeout|failed|HANG)", re.I)


def main():
    wanted = sys.argv[1:]
    missing = [str(p) for p in (QEMU, KERNEL, DTB, INITRD) if not p.exists()]
    if missing:
        print("Missing prerequisites:\n  " + "\n  ".join(missing))
        return 1
    print("RPi Zero (raspi0) USB gadget test (#22)")
    print(f"  QEMU: {QEMU}")
    failed = 0
    for name, fn in SCENARIOS:
        if wanted and fn.__name__ not in wanted:
            continue
        g = Guest(fn.funcs)
        start = time.monotonic()
        try:
            fn(g)
            bad = [line.strip() for line in g.output().splitlines()
                   if any(f in line for f in GUEST_FAILURES) or
                   DWC2_COMPLAINT.search(line)]
            expect(not bad, f"guest reported: {bad[:5]}")
            print(f"  PASS  {name} ({time.monotonic() - start:.0f}s)")
        except Exception as e:  # noqa: BLE001 - report every failure
            failed += 1
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            print("".join(traceback.format_exception(e)[-4:]))
            print("  --- guest serial (tail) ---")
            print("".join(g.out[-60:]))
            err = "".join(g.err).strip()
            if err:
                print(f"  --- QEMU stderr ---\n{err[-3000:]}")
        finally:
            if g.proc.poll() is None:
                g.proc.kill()
                g.proc.wait()
    print("\n  ALL TESTS PASSED" if not failed else f"\n  {failed} TEST(S) FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
