# DWC2 silicon identity + USB/IP server — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver PRs 1 and 2 of the #22 series: the BCM2835 DWC2 core reports its real identity/HWCFG values, and a new `usbip-server` QEMU device exports whatever QEMU USB device sits on its port to any USB/IP client (Linux `vhci-hcd`, the CI's Python client).

**Architecture:** `usbip-server` is a bus-less QEMU device that owns a one-port `USBBus` and speaks USB/IP over a chardev (the transport idiom `usbredir` uses). USB/IP messages are parsed from a receive buffer; each `CMD_SUBMIT` becomes a `USBPacket` handed to the device on the port, completing synchronously, asynchronously (`USB_RET_ASYNC`) or after NAK retries, and is answered with `RET_SUBMIT`; `CMD_UNLINK` cancels. Because `vhci-hcd` never sends `SET_ADDRESS` or a port reset, the server enumerates the device itself (port reset, `SET_ADDRESS`, device and configuration descriptors) before answering `OP_REQ_DEVLIST`/`OP_REQ_IMPORT`. The gadget (PR 3) will plug into this same port; it gets its own plan once this server is merged.

**Tech Stack:** QEMU v11.1.0 C (QOM/qdev, `hw/usb` core, chardev frontend), the repo's patch series in `ci/qemu-patches/`, Python 3 (stdlib only) for the client and tests, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-27-dwc2-gadget-usbip-design.md` (§3 for Task 1, §4.3/§6/§8 for Tasks 2–8).

## Global Constraints

- QEMU base: v11.1.0 + `ci/qemu-patches/0001`–`0035`; new work is added as further patches (`0036`…), one patch per task, each applying with both `git apply` and `patch -p1 --fuzz=0`, each building and passing its tests on its own.
- Silicon values (Task 1), verbatim: GUID `0x2708A000`, GSNPSID `0x4F54280A`, GHWCFG1 `0x00000000`, GHWCFG2 `0x228DDD50`, GHWCFG3 `0x0FF000E8`, GHWCFG4 `0x1FF00020`.
- USB/IP wire format, verbatim from docs.kernel.org/usb/usbip_protocol.html: big-endian; version `0x0111`; `OP_REQ_DEVLIST 0x8005`/`OP_REP_DEVLIST 0x0005`; `OP_REQ_IMPORT 0x8003`/`OP_REP_IMPORT 0x0003`; `USBIP_CMD_SUBMIT 1`, `USBIP_CMD_UNLINK 2`, `USBIP_RET_SUBMIT 3`, `USBIP_RET_UNLINK 4`; 0x30-byte URB headers; 0x138-byte device record; 16-byte ISO packet descriptors.
- Status values on the wire are **Linux errno numbers** on every host OS (QEMU also runs on macOS/BSD, where `ECONNRESET` is 54): `EPIPE 32`, `ENODEV 19`, `EPROTO 71`, `EOVERFLOW 75`, `ECONNRESET 104`, `EREMOTEIO 121`. Never use `<errno.h>` constants for them.
- `RET_UNLINK` status is `-ECONNRESET` when the URB was withdrawn (no `RET_SUBMIT` follows), `0` when it had already completed.
- A client protocol error closes that client's connection only; the device and guest are unaffected. Nothing blocks QEMU's main loop.
- Repo rules: Python via `uv run` locally (`python3` inside CI, as the existing steps do); no files in `/tmp` (use the repo's `tmp/`); never `2>/dev/null`; small commits; one commit per fix; force pushes only via `git safe-force-push-lease <branch>`; a task is not done until its PR's GitHub Actions runs are verified green; never delete features/tests without asking.
- Commit trailers (repo commits and QEMU patches):
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01MSxgFokyz65AL7GTVbUaZS
  ```
- PR descriptions end with:
  ```
  🤖 Generated with [Claude Code](https://claude.com/claude-code)

  https://claude.ai/code/session_01MSxgFokyz65AL7GTVbUaZS
  ```

## Working environment (read once)

- **Scratch QEMU tree:** `tmp/qemu-src` in the main checkout (`/home/tim/github/fpgas-online/rpi-qemu/tmp/qemu-src`): v11.1.0 with `0001`–`0035` applied, committed as `baseline: v11.1.0 + ci/qemu-patches 0001-0035`. Each task makes **one commit** there; the patch is exported from that commit. Recreate it if missing:
  ```bash
  cd /home/tim/github/fpgas-online/rpi-qemu/tmp
  git clone -q --depth=1 --branch v11.1.0 https://gitlab.com/qemu-project/qemu.git qemu-src
  cd qemu-src
  for p in ../../ci/qemu-patches/*.patch; do git apply "$p"; done
  git add -A && git -c user.name=baseline -c user.email=baseline@invalid commit -qm "baseline: v11.1.0 + ci/qemu-patches 0001-0035"
  ```
- **Build:** meson build dirs are not relocatable, so build in place:
  ```bash
  cd /home/tim/github/fpgas-online/rpi-qemu/tmp/qemu-src
  mkdir -p build && cd build
  ../configure --target-list=aarch64-softmmu --enable-slirp --disable-docs
  ninja qemu-system-aarch64
  ```
  The binary is `tmp/qemu-src/build/qemu-system-aarch64`; tests take it through `QEMU_OVERRIDE`.
- **Export a patch** (from the scratch tree's HEAD commit, into the task's repo worktree):
  ```bash
  cd /home/tim/github/fpgas-online/rpi-qemu/tmp/qemu-src
  git format-patch -1 -N --start-number <NN> -o <worktree>/ci/qemu-patches/ HEAD
  ./scripts/checkpatch.pl <worktree>/ci/qemu-patches/00<NN>-*.patch
  ```
  Then verify the series reproduces the scratch tree exactly:
  ```bash
  cd /home/tim/github/fpgas-online/rpi-qemu/tmp
  rm -rf verify-src && git clone -q --depth=1 --branch v11.1.0 https://gitlab.com/qemu-project/qemu.git verify-src
  cd verify-src && for p in <worktree>/ci/qemu-patches/*.patch; do patch -p1 --fuzz=0 -s < "$p" || echo "FAIL $p"; done
  diff -r -q -x .git . ../qemu-src -x build && echo IDENTICAL
  cd .. && rm -rf verify-src
  ```
- **Repo worktrees:** `.worktrees/<name>` (already git-ignored). Create them with the superpowers:using-git-worktrees skill from `origin/main`.
- **raspi test images** (not in git; CI downloads them — reproduce locally before running the raspi tests):
  ```bash
  cd <worktree>
  mkdir -p test-images/rpi0/modules
  FW=https://raw.githubusercontent.com/raspberrypi/firmware/bead686816848038563a542dc854346ab13253a2
  wget -q -O test-images/rpi0/kernel.img "$FW/boot/kernel.img"
  wget -q -O test-images/rpi0/bcm2708-rpi-zero-w.dtb "$FW/boot/bcm2708-rpi-zero-w.dtb"
  wget -q -O test-images/rpi0/disable-bt.dtbo "$FW/boot/overlays/disable-bt.dtbo"
  for m in cdc_ether rndis_host; do wget -q -O "test-images/rpi0/modules/$m.ko.xz" "$FW/modules/6.18.52+/kernel/drivers/net/usb/$m.ko.xz"; done
  fdtoverlay -i test-images/rpi0/bcm2708-rpi-zero-w.dtb -o test-images/rpi0/bcm2708-rpi-zero-w-disable-bt.dtb test-images/rpi0/disable-bt.dtbo
  wget -q -O test-images/alpine-minirootfs-armhf.tar.gz https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/armhf/alpine-minirootfs-3.21.3-armhf.tar.gz
  uv run build-initramfs.py --target rpi0
  ```
  (and for raspi4b: the "Download RPi kernel and DTB", "Download Alpine and build initramfs" and U-Boot steps of `.github/workflows/rpi-boot-test.yml`.)

## File map

| File | Responsibility | Task |
|---|---|---|
| `ci/qemu-patches/0036-dwc2-report-the-BCM2835-core-s-identity-and-hardwar.patch` | GUID/GSNPSID/GHWCFG1–4 reset values | 1 |
| `run-rpi0-boot-test.py` | +2 log checks proving the new identity reaches the guest | 1 |
| `ci/usbip_client.py` | Python USB/IP client (devlist, import, submit/unlink, ISO), Bulk-Only mass-storage helper | 2 |
| `ci/test_usbip_client.py` | Unit tests of the client against canned wire bytes | 2 |
| `hw/usb/usbip-server.c` (QEMU, via patches 0037–0041) | The server device | 3–7 |
| `hw/usb/Kconfig`, `hw/usb/meson.build`, `hw/usb/trace-events` (QEMU) | Build/trace plumbing | 3 |
| `run-usbip-test.py` | End-to-end tests: QEMU `-M none` + `usbip-server` + QEMU USB devices, driven by the Python client | 3–7 |
| `run-usbip-vhci-test.py` | Interop: the host kernel's `vhci-hcd` imports the exported device (root) | 8 |
| `.github/workflows/rpi-boot-test.yml` | Runs the unit tests, `run-usbip-test.py`, `run-usbip-vhci-test.py` | 8 |
| `README.md` | "USB/IP export" section | 8 |

Out of scope for this plan (and why): `URB_ZERO_PACKET` handling — a QEMU `USBPacket` already carries a whole transfer to QEMU's own devices, so a trailing zero-length packet only matters once the guest's gadget sees individual packets (PR 3's plan). The dwc2 peripheral mode, `dwc2-gadget` and `otg-cable` are PR 3.

---

### Task 0: Publish the spec and this plan

**Files:**
- Modify: `docs/superpowers/specs/2026-09-27-dwc2-gadget-usbip-design.md` (already amended alongside this plan: chardev transport, ISO detection, vhci in CI)
- Create: `docs/superpowers/plans/2026-09-27-usbip-server.md` (this file)

- [ ] **Step 1: Commit on the spec branch**

```bash
cd /home/tim/github/fpgas-online/rpi-qemu/.worktrees/gadget-spec
git add docs/superpowers/specs/2026-09-27-dwc2-gadget-usbip-design.md docs/superpowers/plans/2026-09-27-usbip-server.md
git commit -m "docs: plan for the DWC2 identity values and the USB/IP server (#22)"
```

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin docs/dwc2-gadget-usbip-spec
gh pr create --title "docs: #22 design (DWC2 gadget over USB/IP) and plan for PRs 1-2" --body-file <file with summary + PR footer>
```

- [ ] **Step 3: Verify its CI is green** (`gh pr checks <n> --watch`), then merge (`gh pr merge <n> --merge`).

---

### Task 1: dwc2 reports the BCM2835 core's identity and hardware configuration

**Files:**
- Modify (QEMU scratch tree): `hw/usb/hcd-dwc2.c:1281-1295` (reset values in `dwc2_reset_enter`)
- Create: `ci/qemu-patches/0036-*.patch`
- Modify: `run-rpi0-boot-test.py` (checks list near line 199)
- Worktree/branch: `.worktrees/dwc2-identity`, branch `raspi0/dwc2-identity`

**Interfaces:**
- Consumes: nothing.
- Produces: `GHWCFG2/3/4` values that PR 3's device mode relies on (7 device endpoints, dedicated TX FIFOs, 4080-word FIFO RAM, internal DMA).

- [ ] **Step 1: Confirm the negative control with today's QEMU**

Build the scratch tree at the baseline (see Working environment), fetch the raspi0 images, then:

```bash
cd <worktree>
QEMU_OVERRIDE=/home/tim/github/fpgas-online/rpi-qemu/tmp/qemu-src/build/qemu-system-aarch64 uv run run-rpi0-boot-test.py > tmp/rpi0-before.log 2>&1; echo rc=$?
grep -n "Core Release\|Tx FIFO" tmp/rpi0-before.log
```

Expected: rc=0, and `Core Release: 2.94a` plus `Shared Tx FIFO mode` (dwc_otg prints both while probing). If either line is absent from the serial log, stop and find which dwc_otg identity lines the test's console does show before writing the checks. The checks must quote what the driver really prints.

- [ ] **Step 2: Add the failing checks**

In `run-rpi0-boot-test.py`'s checks list (next to the `usb-net NIC bound (#24)` entry), add:

```python
        # dwc_otg prints the core's GSNPSID and its TX FIFO architecture
        # (GHWCFG4.DED_FIFO_EN) while probing: the BCM2835's core is 2.80a
        # with dedicated TX FIFOs (#22).
        ("DWC2 core identity is the BCM2835's (#22)", "Core Release: 2.80a"),
        ("DWC2 has dedicated TX FIFOs (#22)", "Dedicated Tx FIFOs mode"),
```

- [ ] **Step 3: Run it to verify it fails**

```bash
QEMU_OVERRIDE=.../tmp/qemu-src/build/qemu-system-aarch64 uv run run-rpi0-boot-test.py; echo rc=$?
```

Expected: the two new checks FAIL (the log says 2.94a / Shared); rc≠0.

- [ ] **Step 4: Implement the silicon values**

In `tmp/qemu-src/hw/usb/hcd-dwc2.c`, `dwc2_reset_enter`, replace the block from `s->guid = 0;` through `s->ghwcfg4 = 0;` with:

```c
    /*
     * Identity and hardware configuration of the BCM2835's core, as read
     * from a Raspberry Pi Zero W (identical on the BCM2711): core 2.80a,
     * 8 host channels, 7 device endpoints + EP0, internal DMA, dedicated
     * TX FIFOs, 4080 words of FIFO RAM, no descriptor DMA.
     */
    s->guid = 0x2708a000;
    s->gsnpsid = 0x4f54280a;
    s->ghwcfg1 = 0x00000000;
    s->ghwcfg2 = 0x228ddd50;
    s->ghwcfg3 = 0x0ff000e8;
    s->ghwcfg4 = 0x1ff00020;
```

Check that the new GHWCFG2 keeps what the host model needs: `NUM_HOST_CHAN` = bits 17:14 = `(0x228ddd50 >> 14) & 0xf` = 7 → 8 channels = `DWC2_NB_CHAN`; `ARCHITECTURE` = bits 4:3 = 2 = internal DMA; `DYNAMIC_FIFO` (bit 19) set. Put this arithmetic in the commit message.

- [ ] **Step 5: Build and run the raspi0 test**

```bash
cd tmp/qemu-src/build && ninja qemu-system-aarch64
cd <worktree> && QEMU_OVERRIDE=.../qemu-system-aarch64 uv run run-rpi0-boot-test.py; echo rc=$?
```

Expected: all checks PASS, including the two new ones and the usb-net DHCP/ping checks (host mode over dwc_otg unchanged).

- [ ] **Step 6: Run the raspi4b regressions**

```bash
QEMU_OVERRIDE=.../qemu-system-aarch64 uv run run-rpi-boot-test.py; echo rc=$?
QEMU_OVERRIDE=.../qemu-system-aarch64 uv run run-rpi-socket-boot-test.py; echo rc=$?
QEMU_OVERRIDE=.../qemu-system-aarch64 uv run run-rpi-socket-network-test.py; echo rc=$?
```

Expected: rc=0 for each.

- [ ] **Step 7: Commit in the scratch tree, export 0036, verify the series**

```bash
cd tmp/qemu-src
git commit -am "dwc2: report the BCM2835 core's identity and hardware configuration" \
  -m "<body: the values, their source (rpiz-usbdev /dev/mem dump, dwc_otg 'EPs: 8, dedicated fifos, 4080 entries in SPRAM'), the GHWCFG2 arithmetic from Step 4, the test evidence, trailers>"
git format-patch -1 -N --start-number 36 -o <worktree>/ci/qemu-patches/ HEAD
./scripts/checkpatch.pl <worktree>/ci/qemu-patches/0036-*.patch
```

Then run the series-reproduction check (Working environment). Expected: `IDENTICAL`, checkpatch reports no errors.

- [ ] **Step 8: Commit in the repo, push, PR, green CI, merge**

```bash
cd <worktree>
git add ci/qemu-patches/0036-*.patch run-rpi0-boot-test.py
git commit -m "qemu-patches: 0036 dwc2: report the BCM2835 core's identity and hardware configuration"
git push -u origin raspi0/dwc2-identity
gh pr create --title "dwc2: report the BCM2835 core's identity and hardware configuration (#22 part 1)" --body-file <file>
gh pr checks <n> --watch
```

Merge only when every check is green (`gh pr merge <n> --merge`). Then confirm that the post-merge `main` runs (the package build, the smoke test) are green too.

---

### Task 2: Python USB/IP client with unit tests

**Files:**
- Create: `ci/usbip_client.py`
- Create: `ci/test_usbip_client.py`
- Worktree/branch (Tasks 2–8): `.worktrees/usbip-server`, branch `raspi0/usbip-server`, from `origin/main` after Task 1 has merged.

**Interfaces:**
- Produces (used by Tasks 3–8):
  - constants `USBIP_VERSION, OP_REQ_DEVLIST, OP_REP_DEVLIST, OP_REQ_IMPORT, OP_REP_IMPORT, CMD_SUBMIT, CMD_UNLINK, RET_SUBMIT, RET_UNLINK, DIR_OUT, DIR_IN, HDR_LEN, DEV_REC_LEN, URB_SHORT_NOT_OK, URB_ISO_ASAP, EPIPE, ENODEV, EPROTO, EOVERFLOW, ECONNRESET, EREMOTEIO`
  - `DeviceRecord` (fields `path, busid, busnum, devnum, speed, id_vendor, id_product, bcd_device, device_class, device_subclass, device_protocol, configuration_value, num_configurations, num_interfaces, interfaces: list[tuple[int,int,int]]`, property `devid`, `unpack(raw) -> DeviceRecord`, `pack() -> bytes`)
  - `IsoPacket(offset, length, actual_length=0, status=0)`, `RetSubmit(seqnum, status, actual_length, start_frame, number_of_packets, error_count, data, iso)`, `RetUnlink(seqnum, status)`
  - `pack_cmd_submit(seqnum, devid, direction, ep, transfer_flags, length, start_frame, number_of_packets, interval, setup, data=b"", iso=()) -> bytes`
  - `UsbipError`, `UsbipClient(sock)`, `UsbipClient.connect(host, port, timeout=10.0)`, methods `close()`, `devlist() -> list[DeviceRecord]`, `import_device(busid) -> DeviceRecord`, `submit(ep, direction, length=0, data=b"", setup=bytes(8), flags=0, iso=None, start_frame=0, interval=0) -> int`, `unlink(victim) -> int`, `poll(seq, timeout) -> RetSubmit|RetUnlink|None`, `wait(seq, timeout=10.0)`, `closed_by_server(timeout) -> bool`, `control(bm_request_type, b_request, w_value, w_index, data_or_length) -> RetSubmit`, `get_descriptor(dtype, index, length)`, `set_configuration(value)`, `set_interface(iface, alt)`
  - `BulkOnlyStorage(client, ep_in=1, ep_out=2)` with `command(cdb, data_in_len=0, data_out=b"") -> (bytes, int)`, `inquiry()`, `ready()`, `read_capacity() -> (blocks, block_size)`, `read10(lba, blocks, block_size=512)`, `write10(lba, data, block_size=512)`

- [ ] **Step 1: Write the failing tests**

`ci/test_usbip_client.py`:

```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run ci/test_usbip_client.py`
Expected: `ModuleNotFoundError: No module named 'usbip_client'`.

- [ ] **Step 3: Implement the client**

`ci/usbip_client.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run ci/test_usbip_client.py`
Expected: `Ran 9 tests ... OK`.

- [ ] **Step 5: Commit**

```bash
git add ci/usbip_client.py ci/test_usbip_client.py
git commit -m "ci: add a USB/IP client for testing the USB/IP server (#22)"
```

---

### Task 3: `usbip-server` device skeleton answering an empty device list (patch 0037)

**Files:**
- Create (QEMU): `hw/usb/usbip-server.c`
- Modify (QEMU): `hw/usb/Kconfig` (append), `hw/usb/meson.build` (after the `hcd-dwc3.c` line), `hw/usb/trace-events` (append)
- Create: `run-usbip-test.py`
- Create: `ci/qemu-patches/0037-hw-usb-add-a-USB-IP-server-device.patch`

**Interfaces:**
- Consumes: `UsbipClient` (Task 2).
- Produces (C, used by Tasks 4–7): `USBIPServerState` (fields below), `usbip_send()`, `usbip_send_op_header()`, `usbip_reset_connection()`, `usbip_close_client()`, `usbip_drop_client()`, `usbip_parse()`, `usbip_msg_len()`, `usbip_dispatch()`, `usbip_handle_op()`, `usbip_reply_devlist()`, `usbip_device_present()`, the constants block. Python: `run-usbip-test.py` with `QemuUsbip(devices)` context manager (attributes `.port`, `.proc`, `.qmp`), `TESTS` list of `(name, function)`, `main()`.

- [ ] **Step 1: Write the failing end-to-end test**

`run-usbip-test.py`:

```python
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


TESTS = [
    ("Empty port: OP_REQ_DEVLIST lists no devices", test_empty_port_lists_no_devices),
    ("usbip-server refuses to start without a chardev", test_chardev_is_required),
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `QEMU_OVERRIDE=/home/tim/github/fpgas-online/rpi-qemu/tmp/qemu-src/build/qemu-system-aarch64 uv run run-usbip-test.py`
Expected: both FAIL. QEMU reports `'usbip-server' is not a valid device model name`.

- [ ] **Step 3: Add the build plumbing (QEMU scratch tree)**

Append to `hw/usb/Kconfig`:

```
config USBIP_SERVER
    bool
    default y
    depends on USB
```

In `hw/usb/meson.build`, after `system_ss.add(when: 'CONFIG_USB_DWC3', if_true: files('hcd-dwc3.c'))`:

```meson
system_ss.add(when: 'CONFIG_USBIP_SERVER', if_true: files('usbip-server.c'))
```

Append to `hw/usb/trace-events`:

```
# usbip-server.c
usbip_server_connection(const char *event) "%s"
usbip_server_op(uint16_t code) "code=0x%04x"
usbip_server_submit(uint32_t seqnum, uint8_t ep, int in, uint32_t len, int32_t npackets) "seqnum=%u ep=%u in=%d len=%u iso_packets=%d"
usbip_server_complete(uint32_t seqnum, int32_t status, uint32_t actual) "seqnum=%u status=%d actual=%u"
usbip_server_unlink(uint32_t seqnum, uint32_t victim, int32_t status) "seqnum=%u victim=%u status=%d"
usbip_server_enumerate(int step, int status) "step=%d status=%d"
```

- [ ] **Step 4: Write the skeleton device**

`hw/usb/usbip-server.c`:

```c
/*
 * USB/IP server: exports the QEMU USB device on its port over USB/IP
 *
 * Protocol: https://docs.kernel.org/usb/usbip_protocol.html
 *
 * The server is a bus-less device owning a one-port USB bus.  A client
 * (Linux's vhci-hcd via "usbip attach", or any USB/IP client) connects to
 * its chardev, lists or imports the device, then submits URBs, which
 * become USBPackets on the device.
 *
 * Copyright (c) 2026 Tim 'mithro' Ansell <me@mith.ro>
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/units.h"
#include "qemu/bswap.h"
#include "qemu/cutils.h"
#include "qemu/error-report.h"
#include "qemu/main-loop.h"
#include "qemu/timer.h"
#include "qapi/error.h"
#include "chardev/char-fe.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/qdev-properties-system.h"
#include "hw/usb/usb.h"
#include "system/reset.h"
#include "trace.h"

#define TYPE_USBIP_SERVER "usbip-server"
OBJECT_DECLARE_SIMPLE_TYPE(USBIPServerState, USBIP_SERVER)

#define USBIP_VERSION           0x0111
#define USBIP_OP_REQ_DEVLIST    0x8005
#define USBIP_OP_REP_DEVLIST    0x0005
#define USBIP_OP_REQ_IMPORT     0x8003
#define USBIP_OP_REP_IMPORT     0x0003
#define USBIP_CMD_SUBMIT        1
#define USBIP_CMD_UNLINK        2
#define USBIP_RET_SUBMIT        3
#define USBIP_RET_UNLINK        4
#define USBIP_DIR_OUT           0
#define USBIP_DIR_IN            1

#define USBIP_OP_HDR_LEN        8
#define USBIP_BUSID_LEN         32
#define USBIP_HDR_LEN           0x30
#define USBIP_DEV_REC_LEN       0x138
#define USBIP_ISO_DESC_LEN      16
#define USBIP_MAX_XFER          (16 * MiB)
#define USBIP_MAX_ISO_PACKETS   1024

#define USBIP_URB_SHORT_NOT_OK  0x0001

/* The one exported device: bus 1, port 1, address 1. */
#define USBIP_PATH              "/sys/devices/qemu/usbip/usb1/1-1"
#define USBIP_BUSID             "1-1"
#define USBIP_BUSNUM            1
#define USBIP_DEVNUM            1

/* Linux errno values: the wire carries these on every host OS. */
#define USBIP_ENODEV            19
#define USBIP_EPIPE             32
#define USBIP_EPROTO            71
#define USBIP_EOVERFLOW         75
#define USBIP_ECONNRESET        104
#define USBIP_EREMOTEIO         121

/* Per-endpoint URB queues: key 0 is EP0, 1..15 OUT, 17..31 IN. */
#define USBIP_NUM_KEYS          32

typedef enum USBIPConn {
    USBIP_CONN_NONE,            /* no client */
    USBIP_CONN_OP,              /* connected, expecting OP_REQ_* */
    USBIP_CONN_ENUMERATING,     /* enumerating the device to answer an op */
    USBIP_CONN_ATTACHED,        /* imported: URB traffic */
} USBIPConn;

typedef struct USBIPIsoDesc {
    uint32_t offset;
    uint32_t length;
    uint32_t actual_length;
    int32_t status;
} USBIPIsoDesc;

typedef enum USBIPURBState {
    USBIP_URB_WAITING,          /* NAKed, or queued behind a NAKed URB */
    USBIP_URB_INFLIGHT,         /* the device holds it (USB_RET_ASYNC) */
} USBIPURBState;

typedef struct USBIPURB {
    USBPacket packet;
    uint32_t seqnum;
    uint8_t epnum;
    bool in;
    bool internal;              /* the server's own enumeration request */
    USBIPURBState state;
    uint32_t flags;
    int32_t start_frame;
    int32_t number_of_packets;  /* >= 1 for isochronous URBs, else 0 */
    uint8_t setup[8];
    uint8_t *buf;
    uint32_t len;
    USBIPIsoDesc *iso;
    QTAILQ_ENTRY(USBIPURB) next;
} USBIPURB;

struct USBIPServerState {
    DeviceState parent_obj;

    CharFrontend chr;
    USBBus bus;
    USBPort port;

    USBIPConn conn;
    bool parsing;
    GByteArray *rx;
    GByteArray *tx;
    guint watch;
    QTAILQ_HEAD(, USBIPURB) inflight;
    QTAILQ_HEAD(, USBIPURB) waiting[USBIP_NUM_KEYS];
    QEMUTimer *retry_timer;
    QEMUBH *retry_bh;
    bool port_resetting;

    /* Self-enumeration: vhci-hcd never sends SET_ADDRESS or a reset. */
    uint16_t enum_op;           /* the OP_REQ_* waiting for it, or 0 */
    int enum_step;
    bool enumerated;
    uint8_t dev_desc[18];
    uint8_t *cfg_desc;
    uint16_t cfg_len;
};

static bool usbip_device_present(USBIPServerState *s)
{
    return s->port.dev && s->port.dev->attached;
}

/* Output */

static void usbip_flush(USBIPServerState *s);

static gboolean usbip_tx_ready(void *do_not_use, GIOCondition cond,
                               void *opaque)
{
    USBIPServerState *s = opaque;

    s->watch = 0;
    usbip_flush(s);
    return G_SOURCE_REMOVE;
}

static void usbip_flush(USBIPServerState *s)
{
    while (s->tx->len) {
        int r = qemu_chr_fe_write(&s->chr, s->tx->data,
                                  MIN(s->tx->len, 64 * KiB));
        if (r <= 0) {
            if (!s->watch) {
                s->watch = qemu_chr_fe_add_watch(&s->chr, G_IO_OUT | G_IO_HUP,
                                                 usbip_tx_ready, s);
            }
            return;
        }
        g_byte_array_remove_range(s->tx, 0, r);
    }
}

static void usbip_send(USBIPServerState *s, const void *data, size_t len)
{
    if (s->conn == USBIP_CONN_NONE) {
        return;
    }
    g_byte_array_append(s->tx, data, len);
    usbip_flush(s);
}

static void usbip_send_op_header(USBIPServerState *s, uint16_t code,
                                 uint32_t status)
{
    uint8_t h[USBIP_OP_HDR_LEN];

    stw_be_p(h, USBIP_VERSION);
    stw_be_p(h + 2, code);
    stl_be_p(h + 4, status);
    usbip_send(s, h, sizeof(h));
}

/* Connection lifecycle */

static void usbip_reset_connection(USBIPServerState *s)
{
    trace_usbip_server_connection("reset");
    s->conn = USBIP_CONN_NONE;
    s->enum_op = 0;
    g_byte_array_set_size(s->rx, 0);
    g_byte_array_set_size(s->tx, 0);
    if (s->watch) {
        g_source_remove(s->watch);
        s->watch = 0;
    }
}

static void usbip_close_client(USBIPServerState *s)
{
    usbip_reset_connection(s);
    qemu_chr_fe_disconnect(&s->chr);
}

static void G_GNUC_PRINTF(2, 3) usbip_drop_client(USBIPServerState *s,
                                                  const char *fmt, ...)
{
    g_autofree char *msg = NULL;
    va_list ap;

    va_start(ap, fmt);
    msg = g_strdup_vprintf(fmt, ap);
    va_end(ap);
    warn_report("usbip-server: %s; closing the client connection", msg);
    usbip_close_client(s);
}

/* Operations (before import) */

static void usbip_reply_devlist(USBIPServerState *s)
{
    uint8_t n[4];

    usbip_send_op_header(s, USBIP_OP_REP_DEVLIST, 0);
    stl_be_p(n, 0);
    usbip_send(s, n, sizeof(n));
}

static void usbip_handle_op(USBIPServerState *s, const uint8_t *d)
{
    uint16_t code = lduw_be_p(d + 2);

    trace_usbip_server_op(code);
    switch (code) {
    case USBIP_OP_REQ_DEVLIST:
        usbip_reply_devlist(s);
        break;
    case USBIP_OP_REQ_IMPORT:
        usbip_send_op_header(s, USBIP_OP_REP_IMPORT, 1);
        break;
    default:
        g_assert_not_reached();     /* usbip_msg_len() rejected it */
    }
}

/* Input */

/*
 * Length of the message at the head of the receive buffer: 0 while its
 * header is incomplete, -1 if it is malformed.
 */
static ssize_t usbip_msg_len(USBIPServerState *s, const uint8_t *d, size_t n)
{
    if (s->conn == USBIP_CONN_OP) {
        if (n < USBIP_OP_HDR_LEN) {
            return 0;
        }
        if (lduw_be_p(d) != USBIP_VERSION) {
            return -1;
        }
        switch (lduw_be_p(d + 2)) {
        case USBIP_OP_REQ_DEVLIST:
            return USBIP_OP_HDR_LEN;
        case USBIP_OP_REQ_IMPORT:
            return USBIP_OP_HDR_LEN + USBIP_BUSID_LEN;
        default:
            return -1;
        }
    }
    return -1;
}

static void usbip_dispatch(USBIPServerState *s, const uint8_t *d, size_t n)
{
    if (s->conn == USBIP_CONN_OP) {
        usbip_handle_op(s, d);
    }
}

static void usbip_parse(USBIPServerState *s)
{
    if (s->parsing) {
        /* re-entered from a synchronous completion: the outer loop goes on */
        return;
    }
    s->parsing = true;
    while (s->conn == USBIP_CONN_OP || s->conn == USBIP_CONN_ATTACHED) {
        ssize_t need = usbip_msg_len(s, s->rx->data, s->rx->len);
        g_autofree uint8_t *msg = NULL;

        if (need < 0) {
            usbip_drop_client(s, "malformed message");
            break;
        }
        if (need == 0 || need > s->rx->len) {
            break;
        }
        msg = g_memdup2(s->rx->data, need);
        g_byte_array_remove_range(s->rx, 0, need);
        usbip_dispatch(s, msg, need);
    }
    s->parsing = false;
}

static int usbip_can_read(void *opaque)
{
    USBIPServerState *s = opaque;

    return s->conn == USBIP_CONN_NONE ? 0 : 64 * KiB;
}

static void usbip_read(void *opaque, const uint8_t *buf, int size)
{
    USBIPServerState *s = opaque;

    g_byte_array_append(s->rx, buf, size);
    usbip_parse(s);
}

static void usbip_event(void *opaque, QEMUChrEvent event)
{
    USBIPServerState *s = opaque;

    switch (event) {
    case CHR_EVENT_OPENED:
        usbip_reset_connection(s);
        s->conn = USBIP_CONN_OP;
        trace_usbip_server_connection("opened");
        break;
    case CHR_EVENT_CLOSED:
        usbip_reset_connection(s);
        trace_usbip_server_connection("closed");
        break;
    default:
        break;
    }
}

/* The USB port */

static void usbip_port_attach(USBPort *port)
{
}

static void usbip_port_detach(USBPort *port)
{
}

static void usbip_port_child_detach(USBPort *port, USBDevice *child)
{
}

static void usbip_port_wakeup(USBPort *port)
{
}

static void usbip_port_complete(USBPort *port, USBPacket *p)
{
}

static USBPortOps usbip_port_ops = {
    .attach = usbip_port_attach,
    .detach = usbip_port_detach,
    .child_detach = usbip_port_child_detach,
    .wakeup = usbip_port_wakeup,
    .complete = usbip_port_complete,
};

static USBBusOps usbip_bus_ops = {
};

/* The device */

static void usbip_server_realize(DeviceState *dev, Error **errp)
{
    USBIPServerState *s = USBIP_SERVER(dev);
    int i;

    if (!qemu_chr_fe_backend_connected(&s->chr)) {
        error_setg(errp, "usbip-server: the 'chardev' property is required");
        return;
    }

    s->rx = g_byte_array_new();
    s->tx = g_byte_array_new();
    QTAILQ_INIT(&s->inflight);
    for (i = 0; i < USBIP_NUM_KEYS; i++) {
        QTAILQ_INIT(&s->waiting[i]);
    }

    usb_bus_new(&s->bus, sizeof(s->bus), &usbip_bus_ops, dev);
    usb_register_port(&s->bus, &s->port, s, 0, &usbip_port_ops,
                      USB_SPEED_MASK_LOW | USB_SPEED_MASK_FULL |
                      USB_SPEED_MASK_HIGH);

    qemu_chr_fe_set_handlers(&s->chr, usbip_can_read, usbip_read,
                             usbip_event, NULL, s, NULL, true);
}

static const Property usbip_server_properties[] = {
    DEFINE_PROP_CHR("chardev", USBIPServerState, chr),
};

static void usbip_server_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = usbip_server_realize;
    dc->hotpluggable = false;
    dc->desc = "USB/IP server exporting the USB device on its port";
    set_bit(DEVICE_CATEGORY_USB, dc->categories);
    device_class_set_props(dc, usbip_server_properties);
}

static const TypeInfo usbip_server_info = {
    .name          = TYPE_USBIP_SERVER,
    .parent        = TYPE_DEVICE,
    .instance_size = sizeof(USBIPServerState),
    .class_init    = usbip_server_class_init,
};

static void usbip_server_register_types(void)
{
    type_register_static(&usbip_server_info);
}

type_init(usbip_server_register_types)
```

(`OP_REQ_IMPORT` is refused in this task because there is no enumeration yet; Task 4 replaces `usbip_handle_op`.)

- [ ] **Step 5: Build and run the test**

```bash
cd /home/tim/github/fpgas-online/rpi-qemu/tmp/qemu-src/build && ninja qemu-system-aarch64
cd <worktree> && QEMU_OVERRIDE=.../qemu-system-aarch64 uv run run-usbip-test.py
```

Expected: both PASS, `ALL TESTS PASSED`. If `-M none` rejects a bus-less `-device`, stop and investigate (the `loader` device is bus-less and user-creatable the same way).

- [ ] **Step 6: Commit (scratch tree and repo)**

```bash
cd tmp/qemu-src && git add -A hw/usb && git commit -m "hw/usb: add a USB/IP server device" -m "<body + trailers>"
cd <worktree> && git add run-usbip-test.py && git commit -m "run-usbip-test: USB/IP server end-to-end test (#22)"
```

(The patch is exported in Task 8, after Tasks 4–7 have each added their commit.)

---

### Task 4: Enumeration, import, and URB submit/complete (patch 0038)

**Files:**
- Modify (QEMU): `hw/usb/usbip-server.c`
- Modify: `run-usbip-test.py` (new tests + `TESTS` entries)

**Interfaces:**
- Consumes: Task 3's state, output and connection helpers.
- Produces: `usbip_urb_new(seqnum, len)`, `usbip_urb_free(u)`, `usbip_urb_key(u)`, `usbip_urb_try(s, u) -> bool` (false = NAKed), `usbip_urb_submit(s, u)`, `usbip_urb_complete(s, u)`, `usbip_send_ret_submit(s, u)`, `usbip_urb_status(u) -> int32_t`, `usbip_arm_retry(s)`, `usbip_cancel_all(s)`, `usbip_enumerate(s, op)`, `usbip_send_device_record(s, with_interfaces)`. Python: `storage_image(blocks)`, `with_storage()`.

- [ ] **Step 1: Write the failing tests**

Add to `run-usbip-test.py` (before `TESTS`):

```python
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
```

Replace the `TESTS` list with:

```python
TESTS = [
    ("Empty port: OP_REQ_DEVLIST lists no devices", test_empty_port_lists_no_devices),
    ("usbip-server refuses to start without a chardev", test_chardev_is_required),
    ("usb-storage is listed with its IDs, speed and interface", test_storage_is_listed),
    ("Import of an unknown busid is refused", test_import_unknown_busid_is_refused),
    ("List then import on separate connections", test_list_then_import_on_new_connections),
    ("Control transfers: descriptors, SET_CONFIGURATION", test_control_transfers),
    ("Bulk: mass storage INQUIRY/READ CAPACITY/READ(10)/WRITE(10)", test_bulk_mass_storage),
]
```

- [ ] **Step 2: Run to verify the new tests fail**

Expected: the five new tests FAIL (devlist returns `[]`; import is refused).

- [ ] **Step 3: Implement URBs, enumeration and import**

In `hw/usb/usbip-server.c`:

(a) After `usbip_send_op_header`, add the URB core:

```c
/* URBs */

static USBIPURB *usbip_urb_new(uint32_t seqnum, uint32_t len)
{
    USBIPURB *u = g_new0(USBIPURB, 1);

    u->seqnum = seqnum;
    u->len = len;
    u->buf = g_malloc0(MAX(len, 1));
    usb_packet_init(&u->packet);
    return u;
}

static void usbip_urb_free(USBIPURB *u)
{
    usb_packet_cleanup(&u->packet);
    g_free(u->iso);
    g_free(u->buf);
    g_free(u);
}

/* EP0 is one queue in both directions, like QEMU's ep_ctl. */
static unsigned usbip_urb_key(const USBIPURB *u)
{
    return u->epnum == 0 ? 0 : u->epnum + (u->in ? 16 : 0);
}

static int32_t usbip_urb_status(const USBIPURB *u)
{
    const USBPacket *p = &u->packet;

    switch (p->status) {
    case USB_RET_SUCCESS:
        if ((u->flags & USBIP_URB_SHORT_NOT_OK) && u->in &&
            p->actual_length < u->len) {
            return -USBIP_EREMOTEIO;
        }
        return 0;
    case USB_RET_STALL:
        return -USBIP_EPIPE;
    case USB_RET_BABBLE:
        return -USBIP_EOVERFLOW;
    case USB_RET_NODEV:
        return -USBIP_ENODEV;
    default:
        return -USBIP_EPROTO;
    }
}

static void usbip_send_ret_submit(USBIPServerState *s, USBIPURB *u)
{
    uint8_t h[USBIP_HDR_LEN] = { 0 };
    uint32_t actual = u->packet.actual_length;
    int32_t status = usbip_urb_status(u);

    trace_usbip_server_complete(u->seqnum, status, actual);
    stl_be_p(h + 0x00, USBIP_RET_SUBMIT);
    stl_be_p(h + 0x04, u->seqnum);
    /* devid, direction and ep are 0 in replies */
    stl_be_p(h + 0x14, status);
    stl_be_p(h + 0x18, actual);
    usbip_send(s, h, sizeof(h));
    if (u->in && actual) {
        usbip_send(s, u->buf, actual);
    }
}

static void usbip_enum_done(USBIPServerState *s, USBIPURB *u);

/* u is on no list here. */
static void usbip_urb_complete(USBIPServerState *s, USBIPURB *u)
{
    if (u->internal) {
        usbip_enum_done(s, u);
        return;
    }
    usbip_send_ret_submit(s, u);
    usbip_urb_free(u);
}

static void usbip_arm_retry(USBIPServerState *s)
{
    if (!timer_pending(s->retry_timer)) {
        timer_mod(s->retry_timer,
                  qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + SCALE_MS);
    }
}

/* Hand an URB to the device.  Returns false if the device NAKed it. */
static bool usbip_urb_try(USBIPServerState *s, USBIPURB *u)
{
    USBDevice *dev = s->port.dev;
    USBEndpoint *ep;
    int pid;

    if (u->epnum == 0) {
        pid = (u->setup[0] & USB_DIR_IN) ? USB_TOKEN_IN : USB_TOKEN_OUT;
    } else {
        pid = u->in ? USB_TOKEN_IN : USB_TOKEN_OUT;
    }
    ep = usb_ep_get(dev, pid, u->epnum);
    usb_packet_setup(&u->packet, pid, ep, 0, u->seqnum,
                     (u->flags & USBIP_URB_SHORT_NOT_OK) != 0, false);
    usb_packet_addbuf(&u->packet, u->buf, u->len);
    if (u->epnum == 0) {
        u->packet.parameter = ldq_le_p(u->setup);
    }
    usb_handle_packet(dev, &u->packet);

    switch (u->packet.status) {
    case USB_RET_NAK:
        return false;
    case USB_RET_ASYNC:
        u->state = USBIP_URB_INFLIGHT;
        QTAILQ_INSERT_TAIL(&s->inflight, u, next);
        return true;
    default:
        usbip_urb_complete(s, u);
        return true;
    }
}

/* Submit in order: behind any URB of the same endpoint still waiting. */
static void usbip_urb_submit(USBIPServerState *s, USBIPURB *u)
{
    unsigned key = usbip_urb_key(u);

    if (!QTAILQ_EMPTY(&s->waiting[key]) || !usbip_urb_try(s, u)) {
        u->state = USBIP_URB_WAITING;
        QTAILQ_INSERT_TAIL(&s->waiting[key], u, next);
        usbip_arm_retry(s);
    }
}

/* Take an URB off its list, cancelling it on the device if it holds it. */
static void usbip_urb_unlink(USBIPServerState *s, USBIPURB *u)
{
    if (u->state == USBIP_URB_INFLIGHT) {
        QTAILQ_REMOVE(&s->inflight, u, next);
        usb_cancel_packet(&u->packet);
    } else {
        QTAILQ_REMOVE(&s->waiting[usbip_urb_key(u)], u, next);
    }
}

static void usbip_cancel_all(USBIPServerState *s)
{
    USBIPURB *u;
    int i;

    while ((u = QTAILQ_FIRST(&s->inflight))) {
        usbip_urb_unlink(s, u);
        usbip_urb_free(u);
    }
    for (i = 0; i < USBIP_NUM_KEYS; i++) {
        while ((u = QTAILQ_FIRST(&s->waiting[i]))) {
            usbip_urb_unlink(s, u);
            usbip_urb_free(u);
        }
    }
}

static void usbip_retry(USBIPServerState *s)
{
    int key;

    for (key = 0; key < USBIP_NUM_KEYS; key++) {
        USBIPURB *u;

        while ((u = QTAILQ_FIRST(&s->waiting[key]))) {
            QTAILQ_REMOVE(&s->waiting[key], u, next);
            if (!usbip_urb_try(s, u)) {
                QTAILQ_INSERT_HEAD(&s->waiting[key], u, next);
                break;
            }
        }
    }
    for (key = 0; key < USBIP_NUM_KEYS; key++) {
        if (!QTAILQ_EMPTY(&s->waiting[key])) {
            usbip_arm_retry(s);
            break;
        }
    }
}

static void usbip_retry_timer(void *opaque)
{
    usbip_retry(opaque);
}

static void usbip_retry_bh(void *opaque)
{
    usbip_retry(opaque);
}
```

(b) Make `usbip_reset_connection` drop URBs and the enumeration — replace it with:

```c
static void usbip_reset_connection(USBIPServerState *s)
{
    trace_usbip_server_connection("reset");
    usbip_cancel_all(s);
    timer_del(s->retry_timer);
    s->conn = USBIP_CONN_NONE;
    s->enum_op = 0;
    s->enumerated = false;
    g_byte_array_set_size(s->rx, 0);
    g_byte_array_set_size(s->tx, 0);
    if (s->watch) {
        g_source_remove(s->watch);
        s->watch = 0;
    }
}
```

(Move `usbip_reset_connection`, `usbip_close_client` and `usbip_drop_client` below the URB core so the call to `usbip_cancel_all` needs no forward declaration.)

(c) Replace the "Operations" section (`usbip_reply_devlist` and `usbip_handle_op`) with the device record, enumeration, devlist and import:

```c
/* The device record of OP_REP_DEVLIST and OP_REP_IMPORT */

static uint32_t usbip_speed(int speed)
{
    switch (speed) {
    case USB_SPEED_LOW:
        return 1;
    case USB_SPEED_FULL:
        return 2;
    case USB_SPEED_HIGH:
        return 3;
    default:
        g_assert_not_reached();     /* the port's speedmask stops at high */
    }
}

/* Interface descriptors of the first configuration, alternate setting 0. */
static int usbip_count_interfaces(USBIPServerState *s, bool send)
{
    int i, n = 0;

    for (i = 0; i + 2 <= s->cfg_len && s->cfg_desc[i] >= 2;
         i += s->cfg_desc[i]) {
        const uint8_t *d = s->cfg_desc + i;

        if (d[1] == USB_DT_INTERFACE && d[0] >= 9 && i + 9 <= s->cfg_len &&
            d[3] == 0) {
            uint8_t iface[4] = { d[5], d[6], d[7], 0 };

            if (send) {
                usbip_send(s, iface, sizeof(iface));
            }
            n++;
        }
    }
    return n;
}

static void usbip_send_device_record(USBIPServerState *s,
                                     bool with_interfaces)
{
    uint8_t rec[USBIP_DEV_REC_LEN] = { 0 };
    const uint8_t *dd = s->dev_desc;

    pstrcpy((char *)rec + 0x000, 256, USBIP_PATH);
    pstrcpy((char *)rec + 0x100, USBIP_BUSID_LEN, USBIP_BUSID);
    stl_be_p(rec + 0x120, USBIP_BUSNUM);
    stl_be_p(rec + 0x124, USBIP_DEVNUM);
    stl_be_p(rec + 0x128, usbip_speed(s->port.dev->speed));
    stw_be_p(rec + 0x12c, lduw_le_p(dd + 8));       /* idVendor */
    stw_be_p(rec + 0x12e, lduw_le_p(dd + 10));      /* idProduct */
    stw_be_p(rec + 0x130, lduw_le_p(dd + 12));      /* bcdDevice */
    rec[0x132] = dd[4];                             /* bDeviceClass */
    rec[0x133] = dd[5];                             /* bDeviceSubClass */
    rec[0x134] = dd[6];                             /* bDeviceProtocol */
    rec[0x135] = 0;         /* bConfigurationValue: the client configures it */
    rec[0x136] = dd[17];                            /* bNumConfigurations */
    rec[0x137] = usbip_count_interfaces(s, false);  /* bNumInterfaces */
    usbip_send(s, rec, sizeof(rec));
    if (with_interfaces) {
        usbip_count_interfaces(s, true);
    }
}

static void usbip_reply_devlist(USBIPServerState *s)
{
    uint8_t n[4];
    bool listed = usbip_device_present(s) && s->enumerated;

    usbip_send_op_header(s, USBIP_OP_REP_DEVLIST, 0);
    stl_be_p(n, listed ? 1 : 0);
    usbip_send(s, n, sizeof(n));
    if (listed) {
        usbip_send_device_record(s, true);
    }
}

static void usbip_reply_import(USBIPServerState *s, bool ok)
{
    if (!ok) {
        usbip_send_op_header(s, USBIP_OP_REP_IMPORT, 1);
        return;
    }
    usbip_send_op_header(s, USBIP_OP_REP_IMPORT, 0);
    usbip_send_device_record(s, false);
    s->conn = USBIP_CONN_ATTACHED;
    trace_usbip_server_connection("attached");
}

/*
 * Self-enumeration.  vhci-hcd never sends SET_ADDRESS or a port reset, so
 * the server does what a host's hub driver would before handing over the
 * device: reset the port, give the device an address, read its device and
 * configuration descriptors (for the device record).
 */

static const uint8_t usbip_enum_setup[3][8] = {
    { 0x00, USB_REQ_SET_ADDRESS, USBIP_DEVNUM, 0, 0, 0, 0, 0 },
    { 0x80, USB_REQ_GET_DESCRIPTOR, 0, USB_DT_DEVICE, 0, 0, 18, 0 },
    { 0x80, USB_REQ_GET_DESCRIPTOR, 0, USB_DT_CONFIG, 0, 0, 9, 0 },
};

static void usbip_enum_issue(USBIPServerState *s)
{
    uint8_t setup[8];
    USBIPURB *u;

    memcpy(setup, usbip_enum_setup[MIN(s->enum_step, 2)], sizeof(setup));
    if (s->enum_step == 3) {
        stw_le_p(setup + 6, s->cfg_len);    /* the whole configuration */
    }
    u = usbip_urb_new(0, lduw_le_p(setup + 6));
    u->internal = true;
    u->epnum = 0;
    u->in = setup[0] & USB_DIR_IN;
    memcpy(u->setup, setup, sizeof(setup));
    usbip_urb_submit(s, u);
}

static void usbip_enum_finish(USBIPServerState *s, bool ok)
{
    uint16_t op = s->enum_op;

    s->enum_op = 0;
    s->conn = USBIP_CONN_OP;
    if (op == USBIP_OP_REQ_DEVLIST) {
        usbip_reply_devlist(s);
    } else {
        usbip_reply_import(s, ok);
    }
    usbip_parse(s);             /* anything the client sent meanwhile */
}

static void usbip_enum_done(USBIPServerState *s, USBIPURB *u)
{
    bool ok = u->packet.status == USB_RET_SUCCESS;
    int actual = u->packet.actual_length;

    trace_usbip_server_enumerate(s->enum_step, u->packet.status);
    switch (s->enum_step) {
    case 1:
        ok = ok && actual == 18 && u->buf[1] == USB_DT_DEVICE;
        if (ok) {
            memcpy(s->dev_desc, u->buf, sizeof(s->dev_desc));
        }
        break;
    case 2:
        ok = ok && actual == 9 && u->buf[1] == USB_DT_CONFIG &&
             lduw_le_p(u->buf + 2) >= 9;
        if (ok) {
            s->cfg_len = lduw_le_p(u->buf + 2);
        }
        break;
    case 3:
        ok = ok && actual == s->cfg_len;
        if (ok) {
            g_free(s->cfg_desc);
            s->cfg_desc = g_memdup2(u->buf, actual);
        }
        break;
    }
    usbip_urb_free(u);

    if (!ok) {
        usbip_enum_finish(s, false);
    } else if (++s->enum_step == 4) {
        s->enumerated = true;
        usbip_enum_finish(s, true);
    } else {
        usbip_enum_issue(s);
    }
}

static void usbip_enumerate(USBIPServerState *s, uint16_t op)
{
    s->conn = USBIP_CONN_ENUMERATING;
    s->enum_op = op;
    s->enum_step = 0;
    s->enumerated = false;
    s->port_resetting = true;
    usb_port_reset(&s->port);
    s->port_resetting = false;
    usbip_enum_issue(s);
}

static void usbip_handle_op(USBIPServerState *s, const uint8_t *d)
{
    uint16_t code = lduw_be_p(d + 2);
    char busid[USBIP_BUSID_LEN + 1];

    trace_usbip_server_op(code);
    switch (code) {
    case USBIP_OP_REQ_DEVLIST:
        if (usbip_device_present(s)) {
            usbip_enumerate(s, USBIP_OP_REQ_DEVLIST);
        } else {
            usbip_reply_devlist(s);
        }
        break;
    case USBIP_OP_REQ_IMPORT:
        memcpy(busid, d + USBIP_OP_HDR_LEN, USBIP_BUSID_LEN);
        busid[USBIP_BUSID_LEN] = '\0';
        if (usbip_device_present(s) && !strcmp(busid, USBIP_BUSID)) {
            usbip_enumerate(s, USBIP_OP_REQ_IMPORT);
        } else {
            usbip_reply_import(s, false);
        }
        break;
    default:
        g_assert_not_reached();     /* usbip_msg_len() rejected it */
    }
}
```

(d) URB messages. Replace `usbip_msg_len` and `usbip_dispatch` with:

```c
static ssize_t usbip_msg_len(USBIPServerState *s, const uint8_t *d, size_t n)
{
    if (s->conn == USBIP_CONN_OP) {
        if (n < USBIP_OP_HDR_LEN) {
            return 0;
        }
        if (lduw_be_p(d) != USBIP_VERSION) {
            return -1;
        }
        switch (lduw_be_p(d + 2)) {
        case USBIP_OP_REQ_DEVLIST:
            return USBIP_OP_HDR_LEN;
        case USBIP_OP_REQ_IMPORT:
            return USBIP_OP_HDR_LEN + USBIP_BUSID_LEN;
        default:
            return -1;
        }
    }

    if (n < USBIP_HDR_LEN) {
        return 0;
    }
    switch (ldl_be_p(d)) {
    case USBIP_CMD_SUBMIT: {
        uint32_t dir = ldl_be_p(d + 0x0c), ep = ldl_be_p(d + 0x10);
        int32_t len = ldl_be_p(d + 0x18);
        ssize_t total = USBIP_HDR_LEN;

        if (dir > USBIP_DIR_IN || ep > 15 || len < 0 || len > USBIP_MAX_XFER) {
            return -1;
        }
        if (dir == USBIP_DIR_OUT) {
            total += len;
        }
        return total;
    }
    case USBIP_CMD_UNLINK:
        return USBIP_HDR_LEN;
    default:
        return -1;
    }
}

static void usbip_handle_submit(USBIPServerState *s, const uint8_t *d)
{
    USBIPURB *u = usbip_urb_new(ldl_be_p(d + 0x04), ldl_be_p(d + 0x18));

    u->in = ldl_be_p(d + 0x0c) == USBIP_DIR_IN;
    u->epnum = ldl_be_p(d + 0x10);
    u->flags = ldl_be_p(d + 0x14);
    u->start_frame = ldl_be_p(d + 0x1c);
    memcpy(u->setup, d + 0x28, sizeof(u->setup));
    if (!u->in) {
        memcpy(u->buf, d + USBIP_HDR_LEN, u->len);
    }
    trace_usbip_server_submit(u->seqnum, u->epnum, u->in, u->len, 0);
    usbip_urb_submit(s, u);
}

static void usbip_dispatch(USBIPServerState *s, const uint8_t *d, size_t n)
{
    if (s->conn == USBIP_CONN_OP) {
        usbip_handle_op(s, d);
    } else if (ldl_be_p(d) == USBIP_CMD_SUBMIT) {
        usbip_handle_submit(s, d);
    }
}
```

(`CMD_UNLINK` is accepted but not acted on until Task 5.)

(e) Port and bus ops: make `usbip_port_detach` respect the self-enumeration's own reset, and complete asynchronous URBs:

```c
static void usbip_port_detach(USBPort *port)
{
    USBIPServerState *s = port->opaque;

    if (s->port_resetting) {
        return;                 /* our own port reset, not an unplug */
    }
}

static void usbip_port_complete(USBPort *port, USBPacket *p)
{
    USBIPServerState *s = port->opaque;
    USBIPURB *u = container_of(p, USBIPURB, packet);

    QTAILQ_REMOVE(&s->inflight, u, next);
    if (p->status == USB_RET_REMOVE_FROM_QUEUE) {
        /*
         * An earlier URB on this endpoint stalled.  Keep this one queued
         * (the client recovers with CLEAR_FEATURE(HALT) and decides) —
         * retried once the stall has been reported.
         */
        usb_cancel_packet(p);
        u->state = USBIP_URB_WAITING;
        QTAILQ_INSERT_TAIL(&s->waiting[usbip_urb_key(u)], u, next);
        qemu_bh_schedule(s->retry_bh);
        return;
    }
    usbip_urb_complete(s, u);
}
```

(f) In `usbip_server_realize`, before `usb_bus_new`:

```c
    s->retry_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, usbip_retry_timer, s);
    s->retry_bh = qemu_bh_new_guarded(usbip_retry_bh, s,
                                      &dev->mem_reentrancy_guard);
```

- [ ] **Step 4: Build and run**

Expected: all seven tests PASS.

- [ ] **Step 5: Commit (scratch tree and repo)**

```bash
cd tmp/qemu-src && git commit -am "usbip-server: enumerate, import and pass URBs to the device" -m "<body + trailers>"
cd <worktree> && git commit -am "run-usbip-test: listing, import, control and bulk (mass storage)"
```

---

### Task 5: NAK retry, device wakeup and `CMD_UNLINK` (patch 0039)

**Files:**
- Modify (QEMU): `hw/usb/usbip-server.c`
- Modify: `run-usbip-test.py`

**Interfaces:**
- Consumes: `usbip_retry`, `usbip_urb_unlink`, `usbip_urb_key` (Task 4).
- Produces: `usbip_find_urb(s, seqnum)`, `usbip_handle_unlink(s, seqnum, victim)`; bus op `wakeup_endpoint`; port op `wakeup`.

- [ ] **Step 1: Write the failing tests**

Add to `run-usbip-test.py`:

```python
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
```

Append to `TESTS`:

```python
    ("Interrupt IN waits (NAK) and completes on a key press", test_interrupt_in_waits_for_data),
    ("CMD_UNLINK of a pending URB: -ECONNRESET, no RET_SUBMIT", test_unlink_pending_urb),
    ("CMD_UNLINK of a completed URB: status 0", test_unlink_completed_urb),
```

- [ ] **Step 2: Run to verify they fail**

Expected: `test_interrupt_in_waits_for_data` fails or passes only by the 1 ms timer poll (see Step 3). The unlink tests time out, because no `RET_UNLINK` is sent.

- [ ] **Step 3: Implement**

(a) After `usbip_handle_submit`, add:

```c
static USBIPURB *usbip_find_urb(USBIPServerState *s, uint32_t seqnum)
{
    USBIPURB *u;
    int i;

    QTAILQ_FOREACH(u, &s->inflight, next) {
        if (!u->internal && u->seqnum == seqnum) {
            return u;
        }
    }
    for (i = 0; i < USBIP_NUM_KEYS; i++) {
        QTAILQ_FOREACH(u, &s->waiting[i], next) {
            if (!u->internal && u->seqnum == seqnum) {
                return u;
            }
        }
    }
    return NULL;
}

/*
 * -ECONNRESET if the URB was still pending (no RET_SUBMIT will follow),
 * 0 if it had already completed.
 */
static void usbip_handle_unlink(USBIPServerState *s, uint32_t seqnum,
                                uint32_t victim)
{
    uint8_t h[USBIP_HDR_LEN] = { 0 };
    USBIPURB *u = usbip_find_urb(s, victim);
    int32_t status = 0;

    if (u) {
        usbip_urb_unlink(s, u);
        usbip_urb_free(u);
        status = -USBIP_ECONNRESET;
    }
    trace_usbip_server_unlink(seqnum, victim, status);
    stl_be_p(h + 0x00, USBIP_RET_UNLINK);
    stl_be_p(h + 0x04, seqnum);
    stl_be_p(h + 0x14, status);
    usbip_send(s, h, sizeof(h));
}
```

(b) In `usbip_dispatch`, add the unlink branch:

```c
static void usbip_dispatch(USBIPServerState *s, const uint8_t *d, size_t n)
{
    if (s->conn == USBIP_CONN_OP) {
        usbip_handle_op(s, d);
    } else if (ldl_be_p(d) == USBIP_CMD_SUBMIT) {
        usbip_handle_submit(s, d);
    } else {
        usbip_handle_unlink(s, ldl_be_p(d + 0x04), ldl_be_p(d + 0x14));
    }
}
```

(c) Wakeups retry at once instead of on the next 1 ms poll:

```c
static void usbip_port_wakeup(USBPort *port)
{
    USBIPServerState *s = port->opaque;

    qemu_bh_schedule(s->retry_bh);
}

static void usbip_wakeup_endpoint(USBBus *bus, USBEndpoint *ep,
                                  unsigned int stream)
{
    USBIPServerState *s = container_of(bus, USBIPServerState, bus);

    qemu_bh_schedule(s->retry_bh);
}

static USBBusOps usbip_bus_ops = {
    .wakeup_endpoint = usbip_wakeup_endpoint,
};
```

- [ ] **Step 4: Build and run**

Expected: all ten tests PASS.

- [ ] **Step 5: Commit (scratch tree and repo)**

```bash
cd tmp/qemu-src && git commit -am "usbip-server: retry NAKed URBs and handle CMD_UNLINK" -m "<body + trailers>"
cd <worktree> && git commit -am "run-usbip-test: interrupt IN, unlink"
```

---

### Task 6: Isochronous URBs (patch 0040)

**Files:**
- Modify (QEMU): `hw/usb/usbip-server.c`
- Modify: `run-usbip-test.py`

**Interfaces:**
- Consumes: `usbip_urb_try`, `usbip_send_ret_submit`, `usbip_msg_len`, `usbip_handle_submit` (replaced here in full).
- Produces: `usbip_urb_iso(s, u)`. An URB is isochronous iff its `number_of_packets` is in `[1, USBIP_MAX_ISO_PACKETS]`. Linux's vhci sends 0 for other URBs; the protocol document says `0xffffffff`; both mean "not ISO".

- [ ] **Step 1: Write the failing tests**

Add to `run-usbip-test.py`:

```python
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
```

Append to `TESTS`:

```python
    ("Isochronous OUT: per-packet lengths and status", test_isochronous_out),
    ("Isochronous to a disabled stream: each packet stalls, stream in sync",
     test_isochronous_to_disabled_stream_stalls_each_packet),
```

- [ ] **Step 2: Run to verify they fail**

Expected: both FAIL. The descriptors after the data are parsed as a new command, so the server drops the client as malformed.

- [ ] **Step 3: Implement**

(a) In `usbip_msg_len`, `USBIP_CMD_SUBMIT` case, add the descriptors:

```c
    case USBIP_CMD_SUBMIT: {
        uint32_t dir = ldl_be_p(d + 0x0c), ep = ldl_be_p(d + 0x10);
        int32_t len = ldl_be_p(d + 0x18), np = ldl_be_p(d + 0x20);
        ssize_t total = USBIP_HDR_LEN;

        if (dir > USBIP_DIR_IN || ep > 15 || len < 0 || len > USBIP_MAX_XFER) {
            return -1;
        }
        if (dir == USBIP_DIR_OUT) {
            total += len;
        }
        if (np > USBIP_MAX_ISO_PACKETS) {
            return -1;
        }
        if (np > 0) {
            total += (ssize_t)np * USBIP_ISO_DESC_LEN;
        }
        return total;
    }
```

(b) Replace `usbip_handle_submit`:

```c
static void usbip_handle_submit(USBIPServerState *s, const uint8_t *d)
{
    USBIPURB *u = usbip_urb_new(ldl_be_p(d + 0x04), ldl_be_p(d + 0x18));
    int32_t np = ldl_be_p(d + 0x20);
    const uint8_t *desc;
    int i;

    u->in = ldl_be_p(d + 0x0c) == USBIP_DIR_IN;
    u->epnum = ldl_be_p(d + 0x10);
    u->flags = ldl_be_p(d + 0x14);
    u->start_frame = ldl_be_p(d + 0x1c);
    memcpy(u->setup, d + 0x28, sizeof(u->setup));
    if (!u->in) {
        memcpy(u->buf, d + USBIP_HDR_LEN, u->len);
    }
    if (np > 0) {
        u->number_of_packets = np;
        u->iso = g_new0(USBIPIsoDesc, np);
        desc = d + USBIP_HDR_LEN + (u->in ? 0 : u->len);
        for (i = 0; i < np; i++, desc += USBIP_ISO_DESC_LEN) {
            u->iso[i].offset = ldl_be_p(desc);
            u->iso[i].length = ldl_be_p(desc + 4);
            if (u->iso[i].offset > u->len ||
                u->iso[i].length > u->len - u->iso[i].offset) {
                usbip_urb_free(u);
                usbip_drop_client(s, "ISO packet outside its transfer buffer");
                return;
            }
        }
    }
    trace_usbip_server_submit(u->seqnum, u->epnum, u->in, u->len,
                              u->number_of_packets);
    usbip_urb_submit(s, u);
}
```

(c) Before `usbip_urb_try`, add the isochronous path, and call it first thing in `usbip_urb_try`:

```c
/*
 * Isochronous URBs: one USBPacket per ISO packet, all synchronous (QEMU
 * devices never answer ISO asynchronously).  A NAK is a frame without
 * data: an empty packet.
 */
static void usbip_urb_iso(USBIPServerState *s, USBIPURB *u)
{
    USBDevice *dev = s->port.dev;
    int pid = u->in ? USB_TOKEN_IN : USB_TOKEN_OUT;
    USBEndpoint *ep = usb_ep_get(dev, pid, u->epnum);
    int i;

    for (i = 0; i < u->number_of_packets; i++) {
        USBIPIsoDesc *d = &u->iso[i];

        usb_packet_setup(&u->packet, pid, ep, 0, u->seqnum, false, false);
        usb_packet_addbuf(&u->packet, u->buf + d->offset, d->length);
        usb_handle_packet(dev, &u->packet);
        d->actual_length = 0;
        d->status = 0;
        if (u->packet.status == USB_RET_SUCCESS) {
            d->actual_length = u->packet.actual_length;
        } else if (u->packet.status != USB_RET_NAK) {
            d->status = usbip_urb_status(u);
        }
    }
    usbip_urb_complete(s, u);
}
```

and at the top of `usbip_urb_try`, after the declarations:

```c
    if (u->number_of_packets) {
        usbip_urb_iso(s, u);
        return true;
    }
```

(d) Replace `usbip_send_ret_submit` so it handles both kinds:

```c
static void usbip_send_ret_submit(USBIPServerState *s, USBIPURB *u)
{
    uint8_t h[USBIP_HDR_LEN] = { 0 };
    uint32_t actual = 0, errors = 0;
    int32_t status = 0;
    int i;

    if (u->number_of_packets) {
        for (i = 0; i < u->number_of_packets; i++) {
            actual += u->iso[i].actual_length;
            errors += u->iso[i].status != 0;
        }
    } else {
        actual = u->packet.actual_length;
        status = usbip_urb_status(u);
    }

    trace_usbip_server_complete(u->seqnum, status, actual);
    stl_be_p(h + 0x00, USBIP_RET_SUBMIT);
    stl_be_p(h + 0x04, u->seqnum);
    /* devid, direction and ep are 0 in replies */
    stl_be_p(h + 0x14, status);
    stl_be_p(h + 0x18, actual);
    stl_be_p(h + 0x1c, u->number_of_packets ? u->start_frame : 0);
    stl_be_p(h + 0x20, u->number_of_packets);
    stl_be_p(h + 0x24, errors);
    usbip_send(s, h, sizeof(h));

    if (u->in) {
        if (u->number_of_packets) {
            /* ISO IN data goes without the gaps between packets */
            for (i = 0; i < u->number_of_packets; i++) {
                usbip_send(s, u->buf + u->iso[i].offset,
                           u->iso[i].actual_length);
            }
        } else if (actual) {
            usbip_send(s, u->buf, actual);
        }
    }
    for (i = 0; i < u->number_of_packets; i++) {
        uint8_t desc[USBIP_ISO_DESC_LEN];

        stl_be_p(desc, u->iso[i].offset);
        stl_be_p(desc + 4, u->iso[i].length);
        stl_be_p(desc + 8, u->iso[i].actual_length);
        stl_be_p(desc + 12, u->iso[i].status);
        usbip_send(s, desc, sizeof(desc));
    }
}
```

- [ ] **Step 4: Build and run**

Expected: all twelve tests PASS.

- [ ] **Step 5: Commit (scratch tree and repo)**

```bash
cd tmp/qemu-src && git commit -am "usbip-server: isochronous URBs" -m "<body + trailers>"
cd <worktree> && git commit -am "run-usbip-test: isochronous OUT (usb-audio)"
```

---

### Task 7: Connection lifecycle: unplug, system reset, malformed input (patch 0041)

**Files:**
- Modify (QEMU): `hw/usb/usbip-server.c`
- Modify: `run-usbip-test.py`

**Interfaces:**
- Consumes: `usbip_close_client`, `usbip_drop_client`, `usbip_reset_connection`.
- Produces: `usbip_server_reset_hold(obj, type)`. The server is registered with `qemu_register_resettable()`, so it and the devices on its bus are reset with the machine.

- [ ] **Step 1: Write the failing tests**

Add to `run-usbip-test.py`:

```python
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
```

Append to `TESTS`:

```python
    ("Client vanishing with an URB pending; reconnect works", test_reconnect_after_client_vanishes),
    ("Malformed OP request closes only that connection", test_malformed_op_closes_only_that_connection),
    ("Malformed command closes the attached client", test_malformed_command_closes_attached_client),
    ("Device unplug closes the connection; device no longer listed", test_device_unplug_closes_the_connection),
    ("System reset closes the connection; re-import works", test_system_reset_closes_the_connection),
```

- [ ] **Step 2: Run to verify which fail**

Expected: `test_device_unplug_closes_the_connection` and `test_system_reset_closes_the_connection` FAIL: the connection stays open, and the busless server gets no machine reset. The first three may already pass, because of Tasks 3–4's reset and malformed-message handling. They stay as regression tests; say so in the commit message.

- [ ] **Step 3: Implement**

(a) `usbip_port_detach`:

```c
static void usbip_port_detach(USBPort *port)
{
    USBIPServerState *s = port->opaque;

    if (s->port_resetting) {
        return;                 /* our own port reset, not an unplug */
    }
    s->enumerated = false;
    /*
     * A USB/IP exporter reports that its device went away by closing the
     * connection, as Linux's usbip-host does.
     */
    if (s->conn == USBIP_CONN_ATTACHED || s->conn == USBIP_CONN_ENUMERATING) {
        trace_usbip_server_connection("device detached");
        usbip_close_client(s);
    }
}
```

(b) Reset: the server is bus-less, so it is not in the machine's reset tree; register it, and drop the client on reset (the exported device is reset under it):

```c
static void usbip_server_reset_hold(Object *obj, ResetType type)
{
    USBIPServerState *s = USBIP_SERVER(obj);

    if (s->conn != USBIP_CONN_NONE) {
        trace_usbip_server_connection("system reset");
        usbip_close_client(s);
    }
}
```

At the end of `usbip_server_realize`:

```c
    qemu_register_resettable(OBJECT(dev));
```

In `usbip_server_class_init`:

```c
    ResettableClass *rc = RESETTABLE_CLASS(klass);
    ...
    rc->phases.hold = usbip_server_reset_hold;
```

- [ ] **Step 4: Build and run**

Expected: all seventeen tests PASS. Also run the whole suite 5 times in a row (`for i in 1 2 3 4 5; do ... || break; done`) and check that it passes every time, so the timing-sensitive tests are shown not to be flaky.

- [ ] **Step 5: Commit (scratch tree and repo)**

```bash
cd tmp/qemu-src && git commit -am "usbip-server: close the connection on device unplug and machine reset" -m "<body + trailers>"
cd <worktree> && git commit -am "run-usbip-test: connection lifecycle"
```

---

### Task 8: Export patches, vhci interop, CI, docs, PR

**Files:**
- Create: `ci/qemu-patches/0037`–`0041-*.patch`
- Create: `run-usbip-vhci-test.py`
- Modify: `.github/workflows/rpi-boot-test.yml` (after the "Run RPi Zero (raspi0) boot test" step)
- Modify: `README.md` (new "USB/IP export" section after the raspi0 section)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Export and verify the patches**

```bash
cd /home/tim/github/fpgas-online/rpi-qemu/tmp/qemu-src
git format-patch -5 -N --start-number 37 -o <worktree>/ci/qemu-patches/ HEAD
./scripts/checkpatch.pl <worktree>/ci/qemu-patches/003[7-9]-*.patch <worktree>/ci/qemu-patches/004[01]-*.patch
```

Run the series-reproduction check (Working environment). Expected: checkpatch reports no errors; `IDENTICAL`. Also build each prefix of the series, 0037 alone, then 0037–0038 and so on, and run `run-usbip-test.py` on each: every patch must build, and must pass the tests that exist at its point in the series.

- [ ] **Step 2: Write the vhci interop test**

`run-usbip-vhci-test.py`:

```python
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
```

(`006` is `VDEV_ST_USED`, the state of an attached port. Check that value against the local kernel's `drivers/usb/usbip/usbip_common.h` enum before relying on it.)

- [ ] **Step 3: Run the interop test locally**

Loading a kernel module and installing a package change the host system, so **ask the user first**, naming exactly what will run: `sudo modprobe vhci-hcd` and, for the tool variant, `sudo apt-get install usbip`. With approval:

```bash
sudo modprobe vhci-hcd
sudo QEMU_OVERRIDE=.../qemu-system-aarch64 uv run run-usbip-vhci-test.py --tool sysfs
sudo QEMU_OVERRIDE=.../qemu-system-aarch64 uv run run-usbip-vhci-test.py --tool usbip
```

Expected: three PASS lines each. Keep the output for the PR description. If `sudo uv` is unavailable, use `sudo --preserve-env=QEMU_OVERRIDE python3 run-usbip-vhci-test.py`, because the script uses only the stdlib.

- [ ] **Step 4: Wire CI**

In `.github/workflows/rpi-boot-test.yml`, after the "Run RPi Zero (raspi0) boot test" step:

```yaml
      - name: USB/IP client unit tests
        run: python3 ci/test_usbip_client.py

      - name: Run USB/IP server test
        run: |
          QEMU_OVERRIDE=$(which qemu-rpi-system-aarch64) python3 run-usbip-test.py

      # The kernel's own USB/IP host (vhci-hcd) importing the exported
      # device: the interop the server exists for.
      - name: Run USB/IP interop test with the kernel's vhci-hcd
        run: |
          sudo apt-get install -y "linux-modules-extra-$(uname -r)"
          sudo modprobe vhci-hcd
          sudo QEMU_OVERRIDE=$(which qemu-rpi-system-aarch64) python3 run-usbip-vhci-test.py
```

Whether GitHub's hosted runner can load `vhci-hcd` is established by this PR's first CI run. If it cannot, report the exact error to the user and ask how to proceed: a self-hosted runner, or local-only interop recorded in the PR. Do not quietly drop the step.

- [ ] **Step 5: README section**

Add after the raspi0 section:

````markdown
### USB/IP export

`usbip-server` exports the QEMU USB device on its port over
[USB/IP](https://docs.kernel.org/usb/usbip_protocol.html), so another
machine's USB stack uses it as if it were plugged in:

```bash
qemu-rpi-system-aarch64 ... \
  -chardev socket,id=usbipchr,host=127.0.0.1,port=3240,server=on,wait=off \
  -device usbip-server,id=usbip0,chardev=usbipchr \
  -drive if=none,id=disk0,format=raw,file=disk.img \
  -device usb-storage,bus=usbip0.0,drive=disk0

# on a Linux host (vhci-hcd):
usbip list -r 127.0.0.1
sudo usbip attach -r 127.0.0.1 -b 1-1
```

One client at a time; closing the connection unplugs the device from
that client. Tested in CI with `run-usbip-test.py` (control, bulk,
interrupt, isochronous, unlink, reconnection) and `run-usbip-vhci-test.py`
(the kernel's vhci-hcd with its usb-storage driver).
````

- [ ] **Step 6: Run everything locally once more**

`uv run ci/test_usbip_client.py`, `run-usbip-test.py`, `run-rpi0-boot-test.py`, `run-rpi-boot-test.py`, all against the fully patched build. Expected: all green.

- [ ] **Step 7: Commit, push, PR, review, green CI, merge**

```bash
cd <worktree>
git add ci/qemu-patches/003[7-9]-*.patch ci/qemu-patches/004[01]-*.patch
git commit -m "qemu-patches: 0037-0041 USB/IP server"
git add run-usbip-vhci-test.py && git commit -m "run-usbip-vhci-test: interop with the kernel's vhci-hcd"
git add .github/workflows/rpi-boot-test.yml && git commit -m "ci: run the USB/IP tests"
git add README.md && git commit -m "README: USB/IP export"
git push -u origin raspi0/usbip-server
gh pr create --title "USB/IP server exporting QEMU USB devices (#22 part 2)" --body-file <file: summary, test evidence incl. local vhci output, footer>
```

Have a reviewer subagent review the PR diff. Give it a time limit, and it must use copies of test images only. Fix confirmed findings with one commit per fix, regenerating the affected patch. Watch `gh pr checks <n> --watch`; merge only when every check is green; then verify the post-merge `main` runs (package build, APT publish, smoke test) are green.

---

## After this plan

Write the PR 3 plan (dwc2 peripheral mode + `dwc2-gadget` on this server's port: VBUS/session from the client connection, `URB_ZERO_PACKET`, ACM/ECM/NCM/mass-storage/composite gadget CI on raspi0), then PR 4 (isochronous gadget endpoints) and PR 5 (docs, close #22), per spec §9.
