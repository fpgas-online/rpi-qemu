#!/usr/bin/env python3
"""Unit tests for ci/usbip_client.py against canned USB/IP wire bytes.

The CMD_SUBMIT vector is the "CmdIntrIN" capture from the kernel's
protocol document (Documentation/usb/usbip_protocol.rst, EXAMPLE).

Usage: uv run ci/test_usbip_client.py
"""
import socket
import struct
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.resolve()))
import usbip_client as u  # noqa: E402

DOC_CMD_INTR_IN = bytes.fromhex(
    "00000001 00000d05 0001000f 00000001 00000001 00000200 00000040"
    " ffffffff 00000000 00000004 00000000 00000000".replace(" ", ""))


def record(**kw):
    fields = dict(path="/sys/devices/qemu/usbip/usb1/1-1", busid="1-1",
                  busnum=1, devnum=1, speed=3, id_vendor=0x46f4,
                  id_product=0x0001, bcd_device=0, device_class=0,
                  device_subclass=0, device_protocol=0,
                  configuration_value=0, num_configurations=1,
                  num_interfaces=1)
    fields.update(kw)
    return u.DeviceRecord(**fields)


class Peer:
    """The server end of a socketpair: records what the client sent and
    answers with canned bytes."""

    def __init__(self, reply: bytes, expect: int):
        self.client_sock, self.sock = socket.socketpair()
        self.reply, self.expect, self.got = reply, expect, b""
        self.t = threading.Thread(target=self._run)
        self.t.start()

    def _run(self):
        while len(self.got) < self.expect:
            chunk = self.sock.recv(65536)
            if not chunk:
                break
            self.got += chunk
        self.sock.sendall(self.reply)

    def join(self):
        self.t.join(5)


class PackTest(unittest.TestCase):
    def test_cmd_submit_matches_protocol_document(self):
        got = u.pack_cmd_submit(seqnum=0xd05, devid=0x1000f,
                                direction=u.DIR_IN, ep=1,
                                transfer_flags=0x200, length=0x40,
                                start_frame=-1, number_of_packets=0,
                                interval=4, setup=bytes(8))
        self.assertEqual(got, DOC_CMD_INTR_IN)

    def test_device_record_is_0x138_bytes_and_round_trips(self):
        rec = record()
        raw = rec.pack()
        self.assertEqual(len(raw), u.DEV_REC_LEN)
        self.assertEqual(u.DeviceRecord.unpack(raw), rec)
        self.assertEqual(rec.devid, 0x10001)


class ClientTest(unittest.TestCase):
    def client_with(self, reply, expect):
        peer = Peer(reply, expect)
        self.addCleanup(peer.sock.close)
        c = u.UsbipClient(peer.client_sock)
        self.addCleanup(c.close)
        return c, peer

    def test_devlist_parses_records_and_interfaces(self):
        rec = record(num_interfaces=2)
        reply = (struct.pack(">HHI", 0x0111, u.OP_REP_DEVLIST, 0)
                 + struct.pack(">I", 1) + rec.pack()
                 + bytes([8, 6, 0x50, 0, 3, 1, 1, 0]))
        c, peer = self.client_with(reply, 8)
        devs = c.devlist()
        peer.join()
        self.assertEqual(peer.got, struct.pack(">HHI", 0x0111, u.OP_REQ_DEVLIST, 0))
        self.assertEqual(len(devs), 1)
        self.assertEqual(devs[0].interfaces, [(8, 6, 0x50), (3, 1, 1)])

    def test_import_sends_busid_and_sets_devid(self):
        reply = struct.pack(">HHI", 0x0111, u.OP_REP_IMPORT, 0) + record().pack()
        c, peer = self.client_with(reply, 40)
        rec = c.import_device("1-1")
        peer.join()
        self.assertEqual(peer.got[8:], b"1-1".ljust(32, b"\0"))
        self.assertEqual((rec.busid, c.devid), ("1-1", 0x10001))

    def test_refused_import_raises(self):
        reply = struct.pack(">HHI", 0x0111, u.OP_REP_IMPORT, 1)
        c, _ = self.client_with(reply, 40)
        with self.assertRaises(u.UsbipError):
            c.import_device("9-9")

    def test_ret_submit_in_carries_data(self):
        hdr = struct.pack(">IIIII iiiii 8x", u.RET_SUBMIT, 1, 0, 0, 0,
                          0, 4, 0, 0, 0)
        c, _ = self.client_with(hdr + b"\x01\x02\x03\x04", u.HDR_LEN)
        seq = c.submit(1, u.DIR_IN, 64)
        ret = c.wait(seq)
        self.assertEqual((ret.seqnum, ret.status, ret.data), (1, 0, b"\x01\x02\x03\x04"))

    def test_ret_submit_iso_in_unpadded_data_then_descriptors(self):
        hdr = struct.pack(">IIIII iiiii 8x", u.RET_SUBMIT, 1, 0, 0, 0,
                          0, 3, 7, 2, 1)
        descs = struct.pack(">IIIi", 0, 4, 2, 0) + struct.pack(">IIIi", 4, 4, 1, -u.EPROTO)
        c, _ = self.client_with(hdr + b"abc" + descs, u.HDR_LEN + 32)
        seq = c.submit(1, u.DIR_IN, 8, iso=[u.IsoPacket(0, 4), u.IsoPacket(4, 4)])
        ret = c.wait(seq)
        self.assertEqual(ret.data, b"abc")
        self.assertEqual(ret.iso, [u.IsoPacket(0, 4, 2, 0), u.IsoPacket(4, 4, 1, -u.EPROTO)])
        self.assertEqual(ret.error_count, 1)

    def test_unlink_reply(self):
        hdr = struct.pack(">IIIII i 24x", u.RET_UNLINK, 2, 0, 0, 0, -u.ECONNRESET)
        c, peer = self.client_with(hdr, 2 * u.HDR_LEN)
        victim = c.submit(1, u.DIR_IN, 8)
        seq = c.unlink(victim)
        ret = c.wait(seq)
        peer.join()
        self.assertEqual(struct.unpack_from(">I", peer.got, u.HDR_LEN + 0x14)[0], victim)
        self.assertEqual((ret.seqnum, ret.status), (2, -u.ECONNRESET))
        self.assertIsNone(c.poll(victim, 0.1))

    def test_closed_by_server(self):
        c, peer = self.client_with(b"", 0)
        peer.join()
        peer.sock.close()
        self.assertTrue(c.closed_by_server(2))


if __name__ == "__main__":
    unittest.main()
