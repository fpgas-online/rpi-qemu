#!/usr/bin/env python3
"""A small USB/IP client: the protocol Linux's vhci-hcd speaks.

Protocol: https://docs.kernel.org/usb/usbip_protocol.html
Used by run-usbip-test.py and run-usbip-vhci-test.py.
"""
import select
import socket
import struct
import time
from dataclasses import dataclass, field

USBIP_VERSION = 0x0111
OP_REQ_DEVLIST, OP_REP_DEVLIST = 0x8005, 0x0005
OP_REQ_IMPORT, OP_REP_IMPORT = 0x8003, 0x0003
CMD_SUBMIT, CMD_UNLINK, RET_SUBMIT, RET_UNLINK = 1, 2, 3, 4
DIR_OUT, DIR_IN = 0, 1
HDR_LEN = 0x30
DEV_REC_LEN = 0x138
URB_SHORT_NOT_OK = 0x0001
URB_ISO_ASAP = 0x0002
# Linux errno values: the wire carries these on every host OS.
EPIPE, ENODEV, EPROTO, EOVERFLOW, ECONNRESET, EREMOTEIO = 32, 19, 71, 75, 104, 121

_OP_HDR = struct.Struct(">HHI")
_DEV_REC = struct.Struct(">256s32sIIIHHHBBBBBB")
_SUBMIT = struct.Struct(">IIIII iiiii 8s")
_RET_SUBMIT = struct.Struct(">IIIII iiiii 8x")
_UNLINK = struct.Struct(">IIIII I 24x")
_RET_UNLINK = struct.Struct(">IIIII i 24x")
_ISO_DESC = struct.Struct(">IIIi")
assert _DEV_REC.size == DEV_REC_LEN
assert _SUBMIT.size == _RET_SUBMIT.size == _UNLINK.size == _RET_UNLINK.size == HDR_LEN


class UsbipError(Exception):
    pass


@dataclass
class DeviceRecord:
    path: str
    busid: str
    busnum: int
    devnum: int
    speed: int
    id_vendor: int
    id_product: int
    bcd_device: int
    device_class: int
    device_subclass: int
    device_protocol: int
    configuration_value: int
    num_configurations: int
    num_interfaces: int
    interfaces: list = field(default_factory=list)

    @property
    def devid(self) -> int:
        return (self.busnum << 16) | self.devnum

    @classmethod
    def unpack(cls, raw: bytes) -> "DeviceRecord":
        f = list(_DEV_REC.unpack(raw))
        f[0] = f[0].split(b"\0", 1)[0].decode()
        f[1] = f[1].split(b"\0", 1)[0].decode()
        return cls(*f)

    def pack(self) -> bytes:
        return _DEV_REC.pack(
            self.path.encode(), self.busid.encode(), self.busnum, self.devnum,
            self.speed, self.id_vendor, self.id_product, self.bcd_device,
            self.device_class, self.device_subclass, self.device_protocol,
            self.configuration_value, self.num_configurations,
            self.num_interfaces)


@dataclass
class IsoPacket:
    offset: int
    length: int
    actual_length: int = 0
    status: int = 0


@dataclass
class RetSubmit:
    seqnum: int
    status: int
    actual_length: int
    start_frame: int
    number_of_packets: int
    error_count: int
    data: bytes
    iso: list


@dataclass
class RetUnlink:
    seqnum: int
    status: int


def pack_cmd_submit(seqnum, devid, direction, ep, transfer_flags, length,
                    start_frame, number_of_packets, interval, setup,
                    data=b"", iso=()):
    return (_SUBMIT.pack(CMD_SUBMIT, seqnum, devid, direction, ep,
                         transfer_flags, length, start_frame,
                         number_of_packets, interval, bytes(setup))
            + bytes(data)
            + b"".join(_ISO_DESC.pack(p.offset, p.length, p.actual_length,
                                      p.status) for p in iso))


class UsbipClient:
    def __init__(self, sock: socket.socket, timeout: float = 10.0):
        self.sock = sock
        self.sock.settimeout(timeout)
        self.devid = 0
        self._seq = 0
        self._pending = {}   # seqnum -> (direction, iso packet count) or None
        self._replies = {}

    @classmethod
    def connect(cls, host, port, timeout=10.0):
        return cls(socket.create_connection((host, port), timeout=timeout),
                   timeout)

    def close(self):
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("USB/IP server closed the connection")
            buf += chunk
        return bytes(buf)

    def _op(self, code, payload=b""):
        self.sock.sendall(_OP_HDR.pack(USBIP_VERSION, code, 0) + payload)
        version, reply, status = _OP_HDR.unpack(self._recv_exact(_OP_HDR.size))
        if version != USBIP_VERSION:
            raise UsbipError(f"server speaks USB/IP version {version:#06x}")
        return reply, status

    def devlist(self):
        reply, status = self._op(OP_REQ_DEVLIST)
        if reply != OP_REP_DEVLIST or status != 0:
            raise UsbipError(f"bad devlist reply {reply:#06x} status {status}")
        (n,) = struct.unpack(">I", self._recv_exact(4))
        devs = []
        for _ in range(n):
            rec = DeviceRecord.unpack(self._recv_exact(DEV_REC_LEN))
            for _ in range(rec.num_interfaces):
                cls_, sub, proto, _pad = self._recv_exact(4)
                rec.interfaces.append((cls_, sub, proto))
            devs.append(rec)
        return devs

    def import_device(self, busid):
        reply, status = self._op(OP_REQ_IMPORT,
                                 busid.encode().ljust(32, b"\0"))
        if reply != OP_REP_IMPORT:
            raise UsbipError(f"bad import reply {reply:#06x}")
        if status != 0:
            raise UsbipError(f"import of {busid} refused (status {status})")
        rec = DeviceRecord.unpack(self._recv_exact(DEV_REC_LEN))
        self.devid = rec.devid
        return rec

    def submit(self, ep, direction, length=0, data=b"", setup=bytes(8),
               flags=0, iso=None, start_frame=0, interval=0):
        self._seq += 1
        seq = self._seq
        if direction == DIR_OUT and iso is None:
            length = len(data)
        npackets = len(iso) if iso is not None else 0
        self.sock.sendall(pack_cmd_submit(
            seq, self.devid, direction, ep, flags, length, start_frame,
            npackets, interval, setup,
            data if direction == DIR_OUT else b"", iso or ()))
        self._pending[seq] = (direction, npackets)
        return seq

    def unlink(self, victim):
        self._seq += 1
        seq = self._seq
        self.sock.sendall(_UNLINK.pack(CMD_UNLINK, seq, self.devid, 0, 0,
                                       victim))
        self._pending[seq] = None
        return seq

    def _read_one(self):
        hdr = self._recv_exact(HDR_LEN)
        cmd, seq = struct.unpack_from(">II", hdr)
        if cmd == RET_SUBMIT:
            (_, _, _, _, _, status, actual, start_frame, npackets,
             errors) = _RET_SUBMIT.unpack(hdr)
            direction, sent = self._pending.pop(seq)
            data = self._recv_exact(actual) if direction == DIR_IN else b""
            iso = [IsoPacket(*_ISO_DESC.unpack(self._recv_exact(_ISO_DESC.size)))
                   for _ in range(npackets if sent else 0)]
            self._replies[seq] = RetSubmit(seq, status, actual, start_frame,
                                           npackets, errors, data, iso)
        elif cmd == RET_UNLINK:
            status = _RET_UNLINK.unpack(hdr)[5]
            self._pending.pop(seq)
            self._replies[seq] = RetUnlink(seq, status)
        else:
            raise UsbipError(f"unexpected USB/IP command {cmd:#x}")

    def poll(self, seq, timeout):
        """The reply to seq if it arrives within timeout, else None."""
        deadline = time.monotonic() + timeout
        while seq not in self._replies:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([self.sock], [], [], remaining)
            if ready:
                self._read_one()
        return self._replies.pop(seq)

    def wait(self, seq, timeout=10.0):
        reply = self.poll(seq, timeout)
        if reply is None:
            raise TimeoutError(f"no USB/IP reply to seqnum {seq}")
        return reply

    def closed_by_server(self, timeout):
        ready, _, _ = select.select([self.sock], [], [], timeout)
        return bool(ready) and self.sock.recv(1, socket.MSG_PEEK) == b""

    def control(self, bm_request_type, b_request, w_value, w_index,
                data_or_length):
        if bm_request_type & 0x80:
            direction, length, data = DIR_IN, data_or_length, b""
        else:
            direction, data = DIR_OUT, bytes(data_or_length)
            length = len(data)
        setup = struct.pack("<BBHHH", bm_request_type, b_request, w_value,
                            w_index, length)
        return self.wait(self.submit(0, direction, length, data, setup))

    def get_descriptor(self, dtype, index, length):
        return self.control(0x80, 0x06, (dtype << 8) | index, 0, length)

    def set_configuration(self, value):
        return self.control(0x00, 0x09, value, 0, b"")

    def set_interface(self, iface, alt):
        return self.control(0x01, 0x0b, alt, iface, b"")


class BulkOnlyStorage:
    """USB mass storage, Bulk-Only Transport, over a UsbipClient."""

    CBW_SIG, CSW_SIG = 0x43425355, 0x53425355

    def __init__(self, client, ep_in=1, ep_out=2):
        self.c, self.ep_in, self.ep_out, self.tag = client, ep_in, ep_out, 0

    def _ok(self, ret, what):
        if ret.status != 0:
            raise UsbipError(f"{what}: URB status {ret.status}")
        return ret

    def command(self, cdb, data_in_len=0, data_out=b""):
        """Run one SCSI command; returns (data, CSW status)."""
        self.tag += 1
        length = data_in_len or len(data_out)
        cbw = struct.pack("<IIIBBB16s", self.CBW_SIG, self.tag, length,
                          0x80 if data_in_len else 0x00, 0, len(cdb),
                          bytes(cdb).ljust(16, b"\0"))
        self._ok(self.c.wait(self.c.submit(self.ep_out, DIR_OUT, data=cbw)), "CBW")
        data = b""
        if data_in_len:
            data = self._ok(self.c.wait(self.c.submit(self.ep_in, DIR_IN, data_in_len)), "data-in").data
        elif data_out:
            self._ok(self.c.wait(self.c.submit(self.ep_out, DIR_OUT, data=data_out)), "data-out")
        csw = self._ok(self.c.wait(self.c.submit(self.ep_in, DIR_IN, 13)), "CSW").data
        sig, tag, _residue, status = struct.unpack("<IIIB", csw)
        if sig != self.CSW_SIG or tag != self.tag:
            raise UsbipError(f"bad CSW {csw.hex()}")
        return data, status

    def inquiry(self):
        data, status = self.command(bytes([0x12, 0, 0, 0, 36, 0]), 36)
        if status:
            raise UsbipError(f"INQUIRY failed (CSW status {status})")
        return data

    def ready(self):
        """TEST UNIT READY, clearing the power-on unit attention the way a
        host does (REQUEST SENSE) — at most three rounds."""
        for _ in range(3):
            _, status = self.command(bytes(6))
            if status == 0:
                return
            self.command(bytes([0x03, 0, 0, 0, 18, 0]), 18)
        raise UsbipError("unit never became ready")

    def read_capacity(self):
        data, status = self.command(bytes([0x25]) + bytes(9), 8)
        if status:
            raise UsbipError(f"READ CAPACITY failed (CSW status {status})")
        last_lba, block = struct.unpack(">II", data)
        return last_lba + 1, block

    def read10(self, lba, blocks, block_size=512):
        data, status = self.command(
            struct.pack(">BBIBHB", 0x28, 0, lba, 0, blocks, 0),
            blocks * block_size)
        if status:
            raise UsbipError(f"READ(10) failed (CSW status {status})")
        return data

    def write10(self, lba, data, block_size=512):
        _, status = self.command(
            struct.pack(">BBIBHB", 0x2a, 0, lba, 0, len(data) // block_size, 0),
            data_out=data)
        if status:
            raise UsbipError(f"WRITE(10) failed (CSW status {status})")
