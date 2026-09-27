# DWC2 USB gadget mode for the emulated Raspberry Pi Zero, exported over USB/IP

- **Date:** 2026-09-27
- **Status:** design, awaiting review
- **Issue:** [#22](https://github.com/fpgas-online/rpi-qemu/issues/22) — raspi0: DWC2 device/OTG mode absent, so a USB gadget serial console cannot be boot-tested

## 1. Goal

The Linux USB gadget stack runs correctly on the emulated Raspberry Pi Zero (`-M raspi0`), using the **stock Raspberry Pi Zero kernel**, and the resulting gadget appears as a **virtual USB device** on other systems through the standard **USB/IP** protocol.

Guiding principle (from the issue owner): *everything that works on real hardware works, the same way real hardware does it.* No gadget function, driver or image may need changes to run under emulation.

### Success criteria

1. With `dtoverlay=dwc2` (upstream `dwc2` driver, `dr_mode=peripheral` or `otg`) the stock kernel probes the controller in peripheral mode without warnings that do not also occur on silicon.
2. These gadgets enumerate on a USB/IP host and work end to end, each covered by CI:
   - CDC-ACM serial (`g_serial` / configfs `acm`) — a getty login on `ttyGS0` from the host side;
   - CDC-ECM and CDC-NCM networking (`g_ether` / configfs `ecm`, `ncm`) — IP traffic both ways;
   - mass storage (`g_mass_storage` / configfs `mass_storage`) — the host reads and writes the exported image;
   - a composite configfs gadget combining the above.
3. #22's three scenarios behave as on a real board:
   - **no host attached** — the Zero boots normally and the gadget stays unattached (no stall waiting for a host);
   - **host attached at boot** — the gadget enumerates and the console/network/storage work;
   - **host attached after boot** — attaching the USB/IP client later makes the gadget appear (hot-plug), and detaching removes it.
4. A real Linux host can `usbip attach` the exported device and its own drivers (`cdc_acm`, `cdc_ether`/`cdc_ncm`, `usb-storage`) bind — verified locally with the `usbip` tool, and in CI through vhci-hcd's sysfs interface (whether GitHub's hosted runner can load `vhci-hcd` is established by the first CI run of the server PR).
5. Host mode is unchanged: every existing boot test (raspi4b boot/socket/PXE, raspi0 with `usb-net`) keeps passing.

### Non-goals (this design)

- **Host-role USB/IP client** (the Zero *importing* a remote device, e.g. one exported by Renode or `usbipd`). It is a separate, later sub-project; nothing here precludes it.
- **Descriptor DMA** — the BCM2835 core does not implement it (`GHWCFG4.DESC_DMA = 0`), so the stock driver never uses it.
- **OTG protocol negotiation (SRP/HNP/ADP)** — not used by the Raspberry Pi kernel's `dwc2` configuration; the registers read as the silicon's reset values.
- **USB 2.0 LPM and bus suspend/resume initiated by the remote host** — USB/IP carries no suspend signalling; the device-side suspend interrupts simply never fire.

## 2. Background and prior art

- **QEMU today.** `hw/usb/hcd-dwc2.c` models host mode only: *"Gadget-mode registers, just return 0 for now"*; its reset state advertises a B-device (`GOTGCTL_CONID_B`) while reporting host mode (`GINTSTS_CURMODE_HOST`), and `GHWCFG2` reports zero device endpoints. QEMU has **no USB/IP support** (usbredir, its closest relative, is a different protocol that `usbip attach` does not speak).
- **Linux `usbip-vudc`** (`drivers/usb/usbip/vudc_*`), written *"to improve phone emulation in qemu environment"*: a virtual UDC that exports a gadget over USB/IP. Host URBs wait on a per-endpoint queue until a gadget request exists (*"if there's no request queued, the device is NAKing"*); `CMD_UNLINK` returns `-ECONNRESET` for a pending URB, status 0 if it already completed; isochronous is unsupported (`-EXDEV`). Its pitfall: a submit to a non-existent endpoint tears down the connection.
- **Linux `vhci-hcd`** (the importing host) never sends `SET_ADDRESS` or port reset over the wire — the exporter must enumerate the device itself.
- **Renode** exports devices (including emulated SoC USB controllers) as a USB/IP **server only** — it cannot import — and handles one URB at a time, ignoring unlink.
- **Upstream QEMU ASPEED UDC** (Jamin Lin, v5, Sept 2026, reviewed by Cédric Le Goater and Philippe Mathieu-Daudé) models a device-mode controller as **two objects** — the MMIO controller and a separate `USBDevice` gadget that plugs into any QEMU host controller — because *"a single object cannot be both a SysBusDevice and a USBDevice"*. Host packets wait with `USB_RET_ASYNC` *"instead of NAK. A NAK would make the host retry slowly."* This design follows that pattern.
- **USB/IP protocol** — [docs.kernel.org/usb/usbip_protocol.html](https://docs.kernel.org/usb/usbip_protocol.html): big-endian, version `0x0111`; `OP_REQ_DEVLIST`/`OP_REP_DEVLIST`, `OP_REQ_IMPORT`/`OP_REP_IMPORT`, then `USBIP_CMD_SUBMIT`/`RET_SUBMIT`/`CMD_UNLINK`/`RET_UNLINK` with 0x30-byte headers; isochronous packet descriptors follow the payload. Port 3240 is the de-facto default (Renode, Zephyr, usbipd).

## 3. Hardware facts the model must report

The stock `dwc2` driver derives its device-mode configuration from the core's identity and hardware-configuration registers, so these are reported as the silicon does.

| Register | Value | Meaning |
|---|---|---|
| GUID | `0x2708A000` | |
| GSNPSID | `0x4F54280A` | core 2.80a |
| GHWCFG1 | `0x00000000` | |
| GHWCFG2 | `0x228DDD50` | internal DMA, 7 device endpoints + EP0, 8 host channels |
| GHWCFG3 | `0x0FF000E8` | 4080-word FIFO RAM |
| GHWCFG4 | `0x1FF00020` | dedicated TX FIFOs, 7 IN endpoints, no descriptor DMA |

**Source — real silicon:** a read-only `/dev/mem` dump of `rpiz-usbdev` (Raspberry Pi Zero W Rev 1.1, revision `9000c1`, stock `6.18.50+rpt-rpi-v6`, running a high-speed configfs gadget), taken for this design by the Zero-fleet session; the stock driver on that board logs `dwc2 20980000.usb: EPs: 8, dedicated fifos, 4080 entries in SPRAM`. The identical values appear for the BCM2711 (Pi 4) in TinyUSB's `dwc2_info.py`, so all raspi machines report them.

Runtime reference values from the same board in peripheral mode (not reset values — its `config.txt` stacks several `dwc2` overlays and a gadget was bound): `GOTGCTL 0x000D0000` (B-session valid), `GUSBCFG 0x40402407` (force-device), `GAHBCFG 0x00000031`, `GRXFSIZ 0x22E`, `GNPTXFSIZ 0x0020022E`, `DPTXFSIZ1 0x0200024E`, `DCFG 0x00040060`, `DSTS 0x0033ED00` (high speed, SOF frame number counting). A Zero W in host mode reads `GUSBCFG 0x20001700` (force-host, stock `dr_mode=host`). Reset values for a bare `dtoverlay=dwc2` board can be sampled from `rpiz-new-f2db2f` at implementation time.

With these values the stock driver selects **buffer DMA** for gadget endpoints (`params.c`: `p->g_dma = dma_capable; p->g_dma_desc = hw->dma_desc_enable;`), so endpoint data moves through `DIEPDMAn`/`DOEPDMAn`, not PIO FIFO accesses.

Changing GSNPSID from QEMU's current `0x4F54294A` (2.94a) to 2.80a and the GHWCFG values also changes what the **host-mode** drivers see (`dwc_otg` on raspi0–3, `dwc2` on raspi4b, U-Boot). That is intended (fidelity) but is a regression risk, covered in §8.

## 4. Architecture

Three units, each with one job and a narrow interface:

```
guest (stock kernel: dwc2 gadget driver + gadget functions)
        │ MMIO / IRQ / DMA
┌───────▼──────────────────────────┐
│ dwc2  (hw/usb/hcd-dwc2.c)        │  host mode (existing) + peripheral mode (new):
│   role: ID pin / force bits      │  device registers, endpoint state, buffer DMA,
│   VBUS/session → GOTGCTL/GOTGINT │  device interrupts (USBRST, ENUMDONE, IEP/OEP)
└───────┬──────────────────────────┘
        │ internal calls (dwc2 ⇄ its gadget)
┌───────▼──────────────────────────┐
│ dwc2-gadget  (USBDevice)         │  presents the guest's gadget to a QEMU USB bus:
│   handle_reset / handle_control  │  host packets park with USB_RET_ASYNC until the
│   handle_data / cancel_packet    │  guest arms the endpoint; completes them from DMA
└───────┬──────────────────────────┘
        │ QEMU USBBus / USBPacket
┌───────▼──────────────────────────┐
│ usbip-server  (virtual HCD)      │  one-port USB bus exported over TCP with the
│   OP_REQ_DEVLIST / OP_REQ_IMPORT │  USB/IP protocol; enumerates the device itself,
│   CMD_SUBMIT / CMD_UNLINK        │  maps URBs ⇄ USBPackets asynchronously
└───────┬──────────────────────────┘
        │ TCP (default 3240)
   Linux `usbip attach` (vhci-hcd) · CI Python client · any USB/IP client
```

### 4.1 `dwc2`: peripheral mode

- **Identity registers** — §3 values.
- **Role selection** as on silicon: `GUSBCFG.FORCEHSTMODE` / `FORCEDEVMODE` force the role; otherwise the OTG ID pin decides (`GOTGCTL.CONIDSTS`), with `GINTSTS.CURMODE` and `GINTSTS.CONIDSTSCHNG` following. The reset state becomes self-consistent (a B-device reports device mode until forced otherwise).
- **Cable model** — new property `otg-cable = auto | host | device`:
  - `host` (A-plug, ID grounded): today's behaviour; devices on the controller's own bus are downstream devices;
  - `device` (B-plug, ID floating): the controller's gadget is presented to a host; VBUS/session-valid follow whether that host is connected;
  - `auto` (default): `device` when a `dwc2-gadget` exists for this controller, otherwise `host` — existing command lines behave exactly as before.
- **Session/VBUS** in device role: host connected → `GOTGCTL.BSESVLD`/`ASESVLD` set, `GINTSTS.SESSREQINT`; disconnected → `GOTGINT.SESENDDET`, `GINTSTS.DISCONNINT` (as the core signals a session end).
- **Device registers**: `DCFG`, `DCTL` (soft disconnect `SFTDISCON` = pull-up; global NAK controls), `DSTS` (enumerated speed, SOF frame number), `DIEPMSK`/`DOEPMSK`, `DAINT`/`DAINTMSK`, `DIEPEMPMSK`, and per endpoint `DIEPCTLn`/`DOEPCTLn`, `DIEPINTn`/`DOEPINTn`, `DIEPTSIZn`/`DOEPTSIZn`, `DIEPDMAn`/`DOEPDMAn`, `DTXFSTSn`; FIFO sizing registers `GRXFSIZ`, `GNPTXFSIZ`, `DPTXFSIZn` hold what the guest programs and bound what the model accepts.
- **Interrupts**: `GINTSTS.USBRST`, `ENUMDONE`, `IEPINT`, `OEPINT`, `SOF`, `GOUTNAKEFF`/`GINNAKEFF`, per-endpoint `XFERCOMPL`, `SETUP`, `STSPHSERCVD`, `EPDISBLD`, `INTKNTXFEMP`, `NAKEFF`, `B2BSETUP`, masked by `DAINTMSK`/`DxEPMSK` into the existing IRQ line (and the FIQ, which the Pi's `dwc2` gadget path does not use).
- **Soft disconnect / pull-up**: the gadget is visible to a host only while `DCTL.SFTDISCON` is clear, the role is device and a session is valid — exactly the conditions under which a real host would see the pull-up.

### 4.2 `dwc2-gadget`: the gadget as a QEMU `USBDevice`

- Created by `-device dwc2-gadget,bus=<usb-bus>`; links to the machine's DWC2 (property `controller`, defaulting to the unique DWC2 in the machine). Plugging it into a bus is "a host connected"; unplugging (or the bus's port detaching) is "host disconnected".
- **Attach/detach** follow the pull-up: `usb_device_attach()` when the controller's pull-up becomes visible, detach when it goes away (soft disconnect, role change, reset).
- **Reset and speed**: `handle_reset` → the controller raises `USBRST`, then `ENUMDONE` with `DSTS.ENUMSPD = high speed` (the Zero's OTG port is high-speed; the device's `speedmask` advertises HS/FS as the guest's `DCFG.DEVSPD` allows).
- **Control transfers**: a SETUP is latched and delivered when EP0 OUT is armed (`DOEPTSIZ0.SUPCNT`, `DOEPDMA0`), raising `DOEPINT0.SETUP`; the data stage uses EP0 IN/OUT like any endpoint; the host's control packet completes after the guest's status stage (`usb_generic_async_ctrl_complete`). `SET_ADDRESS` is forwarded to the guest (it programs `DCFG.DEVADDR`), as on silicon. A guest `STALL` (`DxEPCTLn.STALL`) completes the packet with `USB_RET_STALL`.
- **Data transfers** (bulk, interrupt, isochronous; IN and OUT): a host packet to endpoint *n* waits (`USB_RET_ASYNC`) until the guest arms that endpoint (`DxEPCTLn.EPENA` with `DxEPTSIZn`/`DxEPDMAn`); the model then copies between the packet and guest memory by DMA, honouring max-packet size, packet count and transfer size exactly as the core does: a short packet or zero-length packet ends a transfer; OUT data beyond the guest's buffer is an overflow; `XFERCOMPL` is raised when the guest's programmed transfer completes. Multiple host packets per endpoint queue in order (pipelined bulk IN/OUT).
- **Isochronous**: host packets carry per-frame packets; the model delivers them against the guest's even/odd frame programming (`DxEPCTLn.SETD0PID/SETD1PID`, `DSTS.SOFFN`) and advances the frame number from its SOF timer.
- **Cancellation**: `cancel_packet` withdraws a waiting host packet without touching data the guest has armed or already received (host unlink never corrupts the guest's view).
- **Endpoint validity**: a packet to an endpoint the guest has not activated (`DxEPCTLn.USBACTEP` clear) completes with `USB_RET_STALL` — never tears down the connection.

### 4.3 `usbip-server`: exporting a QEMU USB device over USB/IP

- Created by `-chardev socket,id=<chr>,host=127.0.0.1,port=3240,server=on,wait=off -device usbip-server,id=<id>,chardev=<chr>`: a virtual host controller with **one port** (bus `<id>.0`); it exports whatever `USBDevice` occupies that port — the `dwc2-gadget`, or any other QEMU USB device. The transport is a chardev, as for `usbredir`, so TCP or Unix sockets, listening address and reconnection come from QEMU's standard socket backend.
- **Discovery/import**: answers `OP_REQ_DEVLIST` and `OP_REQ_IMPORT` with the device record (bus id, speed, VID/PID, class, configuration and interface triples). Because `vhci` never sends `SET_ADDRESS` or port reset, the server enumerates the device itself when a client imports it: port reset, `SET_ADDRESS`, then caches the device and configuration descriptors for the device record.
- **URB traffic**: `USBIP_CMD_SUBMIT` → a `USBPacket` on the requested endpoint (control, bulk, interrupt, isochronous with its packet descriptors); completion → `USBIP_RET_SUBMIT` with actual length, status and (for IN) data. Fully asynchronous, per-endpoint ordered, many URBs in flight. An URB is isochronous when its `number_of_packets` is 1 or more (Linux's `vhci` sends 0 for other URBs, the protocol document `0xffffffff`); the device decides each transfer's outcome, including a stall for an endpoint absent from the current configuration or alternate setting. A NAK keeps the URB waiting (retried on the device's wakeup and every millisecond). `USBIP_CMD_UNLINK` → `cancel_packet`; reply `-ECONNRESET` if the URB was withdrawn (and no `RET_SUBMIT` follows), status 0 if it had already completed.
- **Connection lifecycle**: one client at a time. Import = the port's device is "connected to a host" (for the gadget: session valid → VBUS/connect interrupts in the guest). TCP close or client detach = unplug (guest sees session end/disconnect); a new import re-enumerates.
- **Errors**: malformed messages close that client connection only; the emulated device and guest are unaffected. Status codes follow Linux URB conventions (`-EPIPE` stall, `-EOVERFLOW`, `-ECONNRESET`, `-ESHUTDOWN` on disconnect).

### 4.4 Command-line summary

```
qemu-rpi-system-aarch64 -M raspi0 ... \
  -chardev socket,id=usbipchr,host=127.0.0.1,port=3240,server=on,wait=off \
  -device usbip-server,id=usbip0,chardev=usbipchr \
  -device dwc2-gadget,bus=usbip0.0
# on a Linux host:  usbip list -r 127.0.0.1 ; usbip attach -r 127.0.0.1 -b <busid>
```

Guest side is stock: `dtoverlay=dwc2` (with `dr_mode=peripheral` or the default `otg`) plus the gadget modules/configfs setup the image already uses.

## 5. Behaviour by #22 scenario

| Scenario | Emulation |
|---|---|
| No host | `usbip-server` without a client: session invalid, pull-up invisible; the guest's gadget driver waits exactly as on a board with nothing plugged in. |
| Host at boot | Client imports before/while the guest loads its gadget: session valid; when the guest connects the pull-up the server enumerates and the client sees the device. |
| Host after boot | Client imports later: `SESSREQINT`/VBUS rise in the guest, then reset/enumeration; the device appears on the client. Detach → session end/disconnect in the guest; the gadget returns to waiting. |

## 6. Error handling

- Guest programming errors behave as the core does (e.g. arming an endpoint with an invalid DMA address yields the core's AHB error handling in `DxEPINTn`), not QEMU aborts.
- Host protocol errors are confined to the USB/IP connection.
- Everything is event-driven in QEMU's main loop; no blocking waits (a Renode-style blocking read would deadlock CDC-ACM, whose host keeps an IN URB pending indefinitely).

## 7. Migration/compat

- `vmstate` for new device-mode state (versioned), and for `dwc2-gadget`/`usbip-server` connection-independent state; a live connection is not migrated (the client reconnects).
- Identity/HWCFG changes (§3) apply to every raspi machine; host-mode regression coverage in §8.

## 8. Testing

1. **Protocol harness in CI** — a pure-Python USB/IP client (the protocol `vhci` speaks), `ci/usbip_client.py`: device list, import, control/bulk/interrupt/isochronous URBs, unlink, disconnect. Unit-tested against the server with an existing QEMU device (e.g. `usb-storage`) first, which isolates the server from the gadget.
2. **raspi0 gadget boot tests in CI** (stock Zero W `kernel.img`, `bcm2708-rpi-zero-w.dtb` with the `dwc2` overlay applied via `fdtoverlay`, Alpine armhf initramfs with the gadget modules from the pinned firmware commit):
   - ACM: login prompt / command round trip on `ttyGS0` through the client;
   - ECM and NCM: DHCP/ping between client-side stack (userspace in the harness) and guest `usb0`;
   - mass storage: client reads a known pattern from the exported image and writes back;
   - composite configfs gadget with all of the above;
   - scenarios: no host, host at boot, host after boot, detach/reattach.
   Each with a negative control (the test fails on the pre-change QEMU).
3. **Interop with the kernel's vhci-hcd**: `run-usbip-vhci-test.py` attaches the exported device via vhci's sysfs interface (what `usbip attach` does after its import request), or with the real `usbip` tool, and checks the kernel's own drivers (`usb-storage`; with the gadget also `cdc_acm`, `cdc_ether`/`cdc_ncm`) bind and work; run locally for every PR and in CI where the runner can load `vhci-hcd`.
4. **Host-mode regressions**: all existing boot tests (raspi4b boot/socket/PXE; raspi0 `usb-net`, SysRq, identity) must stay green with the new identity/HWCFG values.

## 9. Delivery (PR series, each reviewed and CI-green before the next)

1. `dwc2`: silicon identity/HWCFG values (+ host-mode regression evidence).
2. `usbip-server` + Python client harness, exporting existing QEMU devices (independently useful; CI on `usb-storage`).
3. `dwc2` peripheral mode + `dwc2-gadget`: EP0 control and bulk/interrupt; CI with ACM, ECM/NCM, mass storage, composite; #22 scenarios.
4. Isochronous endpoints (+ a UAC/UVC-class gadget test).
5. Docs (README raspi0 gadget section) and closing #22.

## 10. Open items

- Reset-value sample from a bare `dtoverlay=dwc2` Zero (`rpiz-new-f2db2f`) to confirm device-mode reset defaults.
- Host-role USB/IP client (Renode/`usbipd` interop in the other direction) — separate spec after this one.
