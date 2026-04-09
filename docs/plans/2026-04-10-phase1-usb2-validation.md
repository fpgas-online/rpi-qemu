# Phase 1: USB2 (DWC2) Validation on raspi4b

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove the DWC2 USB2 host controller works on QEMU `raspi4b`, add USB device tests, and correct the README's "No USB" claim.

**Architecture:** DWC2 is already initialized and IRQ-wired in QEMU's base RPi peripheral code (shared by all raspi machines). We add USB devices to the QEMU command line, add detection checkpoints to the init script, and verify USB enumeration works with the stock RPi kernel's mainline `dwc2` driver. No QEMU patches are needed -- this is pure test infrastructure.

**Tech Stack:** Python (test scripts), shell (initramfs init script), QEMU CLI flags, Alpine Linux aarch64 initramfs

**Spec:** `docs/specs/2026-04-09-rpi-emulation-roadmap.md` (Phase 1, lines 27-68)

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `build-initramfs.py` | Add USB detection commands to init script |
| Modify | `run-rpi-boot-test.py` | Add USB device to QEMU, add USB checkpoints |
| Modify | `README.md` | Correct "No USB" to document USB2 support |

No new files are created. All changes are to existing test infrastructure and documentation.

---

### Task 1: Add USB Detection to Initramfs Init Script

**Files:**
- Modify: `build-initramfs.py:14-85` (the `INIT_SCRIPT` string)

The init script currently only tests network. We add USB device listing after the filesystem mounts but before the network tests. This gives us dmesg-based USB detection that the boot test can match against.

- [ ] **Step 1: Add USB detection block to INIT_SCRIPT**

In `build-initramfs.py`, add a USB detection section after the `sleep 2` at **file line 25** (between `sleep 2` and the `# Bring up eth0` comment at file line 28). Insert this block in the `INIT_SCRIPT` string:

```sh
echo "=== USB Devices ==="
# List USB devices via sysfs (works without usbutils, keeps initramfs small)
usb_found=0
for dev in /sys/bus/usb/devices/[0-9]*; do
    [ -f "$dev/idVendor" ] || continue
    vendor=$(cat "$dev/idVendor")
    product=$(cat "$dev/idProduct")
    manufacturer=""
    product_name=""
    [ -f "$dev/manufacturer" ] && manufacturer=$(cat "$dev/manufacturer")
    [ -f "$dev/product" ] && product_name=$(cat "$dev/product")
    echo "  USB: ${vendor}:${product} ${manufacturer} ${product_name}"
    usb_found=1
done
if [ "$usb_found" = "0" ]; then
    echo "  No USB devices found"
fi
# List USB serial devices
echo "=== USB Serial Devices ==="
ls -la /dev/ttyUSB* 2>/dev/null || echo "  No /dev/ttyUSB* devices"
```

This uses sysfs directly (no `usbutils` package needed, keeping the initramfs small). The `usb_found` flag avoids a misleading message when the USB bus exists but no devices have enumerated yet.

- [ ] **Step 2: Also add a dmesg USB summary at the end of the init script**

In `build-initramfs.py`, in the `INIT_SCRIPT`, add after the existing `dmesg | grep -i -e genet -e "Link is"` line at **file line 79**:

```python
# After the existing dmesg genet grep, add:
echo "=== dmesg usb ==="
dmesg 2>&1 | grep -i -e "dwc2" -e "usb 1-" -e "ttyUSB" | tail -10
```

- [ ] **Step 3: Run build-initramfs.py to verify it still builds**

Run: `uv run build-initramfs.py`

Expected: Builds successfully, outputs `test-images/test-initramfs.cpio.gz` with the updated init script.

Note: This requires `test-images/alpine-minirootfs.tar.gz` to exist. If it doesn't, download it first:
```bash
mkdir -p test-images
wget -q -O test-images/alpine-minirootfs.tar.gz \
  "https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/aarch64/alpine-minirootfs-3.21.3-aarch64.tar.gz"
```

- [ ] **Step 4: Commit**

```bash
git add build-initramfs.py
git commit -m "Initramfs: add USB device detection to init script

List USB devices via sysfs and dmesg after boot. This gives the
boot test checkpoints something to match against when USB devices
are attached to QEMU."
```

---

### Task 2: Add USB Keyboard Device to Boot Test QEMU Command

**Files:**
- Modify: `run-rpi-boot-test.py:101-108` (QEMU Popen args)

We add `-device usb-kbd` to the QEMU command line. This is the simplest USB device to test -- it's a standard USB HID keyboard that the kernel auto-detects. If DWC2 USB works at all, this device will enumerate.

- [ ] **Step 1: Add `-device usb-kbd` to QEMU command**

In `run-rpi-boot-test.py`, modify the `subprocess.Popen` call at line 101. Add the USB device argument after the `-nic` line:

```python
proc = subprocess.Popen(
    [str(QEMU), "-M", "raspi4b",
     "-kernel", str(UBOOT), "-dtb", str(DTB),
     "-nic", f"user,tftp={TFTPBOOT}",
     "-device", "usb-kbd",
     "-serial", "stdio",
     "-display", "none", "-monitor", "none"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    stderr=subprocess.PIPE, text=True)
```

QEMU will auto-select the DWC2's USB bus (`usb-bus.0`) since it's the only USB bus on `raspi4b`. No explicit `bus=` parameter needed.

- [ ] **Step 2: Run the boot test to see what happens**

Run: `uv run run-rpi-boot-test.py`

Observe the output. We're looking for:
- **Success**: QEMU starts without error, dmesg shows `dwc2` and/or `usb 1-1` enumeration messages
- **Failure mode A**: QEMU refuses to start (error about USB bus or device) -- means DWC2 bus is not exposed as expected
- **Failure mode B**: QEMU starts but kernel doesn't detect USB -- means DWC2 driver doesn't load or USB device isn't visible

**Do NOT add checkpoints yet** -- first see what output the kernel actually produces so we know what patterns to match.

- [ ] **Step 3: Commit (regardless of test outcome)**

```bash
git add run-rpi-boot-test.py
git commit -m "Test: add USB keyboard device to raspi4b boot test

Add -device usb-kbd to QEMU command line to test DWC2 USB host
controller enumeration. The DWC2 is already initialized and IRQ-wired
in bcm2835_peripherals.c (shared by all raspi machines) with GIC
SPI 73 on raspi4b (bcm2838.c:205-206)."
```

---

### Task 3: Add USB Detection Checkpoints to Boot Test

**Files:**
- Modify: `run-rpi-boot-test.py:200-214` (checks and optional_checks lists)
- Modify: `run-rpi-boot-test.py:236-243` (key output keyword list)

This task depends on the output observed in Task 2. The checkpoint patterns below are the expected case; adjust based on actual kernel output.

- [ ] **Step 1: Add USB checkpoints to the checks list**

In `run-rpi-boot-test.py`, add USB detection checks after the existing `("GENET driver", "bcmgenet")` line. The exact patterns depend on what the kernel outputs, but the expected patterns are:

```python
checks = [
    ("U-Boot DHCP",         "DHCP client bound"),
    ("TFTP transfers",      "Bytes transferred"),
    ("booti starts kernel", "Starting kernel"),
    ("Kernel boots",        "Booting Linux on physical CPU"),
    ("GENET driver",        "bcmgenet"),
    ("DWC2 USB",            "dwc2"),
    ("USB device",          "USB:"),
    ("Link up",             "Link is Up"),
    ("DHCP lease",          "lease of"),
    ("HTTPS fetch",         "HTTPS fetch: SUCCESS"),
]
```

The `"dwc2"` pattern matches the kernel's DWC2 driver loading message (e.g., `"dwc2 fe980000.usb: DWC OTG Controller"`). The `"USB:"` pattern matches our init script's sysfs-based USB device listing (e.g., `"USB: 0627:0001 QEMU QEMU USB Keyboard"`).

**Important**: Adjust these patterns based on actual kernel output observed in Task 2 Step 2. If DWC2 doesn't produce `"dwc2"` in dmesg, use whatever the actual pattern is. If USB devices list differently, match that.

- [ ] **Step 2: Add USB keywords to the output summary**

In `run-rpi-boot-test.py`, add USB-related keywords to the output summary loop at file line 236. Add `"dwc2"` and `"USB:"` to the existing keyword list (do NOT replace the whole list -- just insert the new keywords):

```python
# Add these keywords to the existing list (between "bcmgenet" and "Link is Up"):
"dwc2", "USB:",
```

**Note**: Leave the `optional_checks` list (lines 210-214) unchanged -- it stays as-is.

- [ ] **Step 3: Run the boot test to verify checkpoints pass**

Run: `uv run run-rpi-boot-test.py`

Expected: All checks pass, including the new USB checks. The output should show `[PASS] DWC2 USB` and `[PASS] USB device`.

If the USB checks fail, debug:
1. Check QEMU stderr for USB-related errors
2. Check dmesg output for DWC2 driver messages
3. Adjust checkpoint patterns to match actual output

- [ ] **Step 4: Commit**

```bash
git add run-rpi-boot-test.py
git commit -m "Test: add USB detection checkpoints to boot test

Verify DWC2 USB controller loads and attached USB device is
detected during boot. Patterns match kernel dmesg output and
the init script's sysfs-based USB device listing."
```

---

### Task 4: Add USB Serial Device Test

**Files:**
- Modify: `run-rpi-boot-test.py:101-108` (QEMU Popen args)
- Modify: `run-rpi-boot-test.py:200-214` (checks list)

A USB serial device is more relevant to fpgas.online than a keyboard (FTDI FT2232 appears as `/dev/ttyUSB*`). Test that QEMU's `usb-serial` device works on DWC2.

- [ ] **Step 1: Add USB serial device to QEMU command**

In `run-rpi-boot-test.py`, replace `-device usb-kbd` with both a keyboard and a serial device. We use `chardev` type `null` (simplest backend -- no actual I/O needed, just verifying device enumeration):

```python
proc = subprocess.Popen(
    [str(QEMU), "-M", "raspi4b",
     "-kernel", str(UBOOT), "-dtb", str(DTB),
     "-nic", f"user,tftp={TFTPBOOT}",
     "-device", "usb-kbd",
     "-chardev", "null,id=usb-serial0",
     "-device", "usb-serial,chardev=usb-serial0",
     "-serial", "stdio",
     "-display", "none", "-monitor", "none"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    stderr=subprocess.PIPE, text=True)
```

- [ ] **Step 2: Add ttyUSB checkpoint**

Add to the checks list:

```python
    ("USB serial",          "ttyUSB"),
```

The `"ttyUSB"` pattern matches both the kernel's FTDI driver message (e.g., `"usb 1-2: FTDI USB Serial Device converter now attached to ttyUSB0"`) and our init script's `/dev/ttyUSB*` listing.

- [ ] **Step 3: Add ttyUSB to output summary keywords**

Add `"ttyUSB"` to the keyword list in the output summary loop.

- [ ] **Step 4: Run test to verify**

Run: `uv run run-rpi-boot-test.py`

Expected: `[PASS] USB serial` -- the `usb-serial` device enumerates and creates `/dev/ttyUSB0`.

If this fails, the `usb-serial` device (FTDI FT232BM emulation in `hw/usb/dev-serial.c`) may not work with DWC2 bulk transfers. Debug by checking dmesg for USB errors.

- [ ] **Step 5: Commit**

```bash
git add run-rpi-boot-test.py
git commit -m "Test: add USB serial device to boot test

Add usb-serial with null chardev backend to QEMU command.
Verifies ttyUSB0 appears in the guest, matching the FTDI
FT2232 devices used on real fpgas.online RPi hosts."
```

---

### Task 5: Test USB Network Device

**Files:**
- Modify: `run-rpi-boot-test.py:101-108` (QEMU Popen args)

Test that QEMU's `usb-net` (CDC/RNDIS) device works on DWC2. This is the foundation for RPi 3/3+ network support (Phase 2), and validates that USB bulk transfers work reliably with DWC2.

- [ ] **Step 1: Add a second network device via USB**

This is a standalone test -- run it separately from the main boot test to avoid interfering with the GENET-based networking. Create a small test variant or add a flag. The key QEMU args to test:

```bash
# Minimal test: just verify usb-net device enumerates
qemu-rpi-system-aarch64 -M raspi4b \
  -kernel u-boot.bin -dtb bcm2711-rpi-4-b.dtb \
  -nic user,tftp=test-images/tftpboot \
  -device usb-net \
  -serial stdio -display none -monitor none
```

When both `-nic user` (GENET) and `-device usb-net` are present, the guest will see two network interfaces: `eth0` (GENET) and a USB Ethernet device (likely `usb0` or `eth1`).

- [ ] **Step 2: Verify USB network device appears in init script output**

Check dmesg for `cdc_ether` or `rndis_host` driver loading, and a second network interface appearing.

- [ ] **Step 3: Document the result**

If `usb-net` works: note this in the commit message as confirmation that DWC2 bulk transfers support network traffic (prerequisite for Phase 2 RPi 3 network).

If `usb-net` fails: this indicates DWC2 bulk transfer reliability issues that must be fixed before Phase 2. Document the specific failure in the commit message.

- [ ] **Step 4: Commit**

```bash
git add run-rpi-boot-test.py
git commit -m "Test: verify USB network device on DWC2

Test usb-net (CDC/RNDIS) device enumeration on raspi4b DWC2
controller. This validates USB bulk transfers work reliably,
which is the prerequisite for Phase 2 RPi 3/3+ networking."
```

---

### Task 6: Update README to Document USB2 Support

**Files:**
- Modify: `README.md:130-135` (Known Limitations section)

The README currently says "No USB" which is incorrect -- DWC2 USB2 works. Correct this and add USB usage examples.

- [ ] **Step 1: Update Known Limitations**

In `README.md`, replace the current Known Limitations section (lines 130-135):

```markdown
## Known Limitations

- **Pi 4B only.** Pi 3B/3B+ use USB-attached Ethernet which QEMU doesn't emulate.
- **No GPU.** `start4.elf` is fetched but not executed. No HDMI, no hardware video decode.
- **No USB 3.0.** The VL805 xHCI controller (USB 3.0) requires PCIe, which isn't fully emulated. USB 2.0 works via the DWC2 controller.
- **User-mode networking only.** Uses QEMU's built-in NAT. No bridged/tap networking tested.
```

Note the change: "No USB" becomes "No USB 3.0" with an explanation that USB 2.0 does work.

- [ ] **Step 2: Add USB device examples to the "Boot a Kernel Directly" section**

In `README.md`, after the existing QEMU command example (around line 64-69), add a note about USB devices:

```markdown
To attach USB devices (keyboard, serial, etc.) to the emulated Pi:

```bash
qemu-rpi-system-aarch64 -M raspi4b \
  -kernel Image -dtb bcm2711-rpi-4-b.dtb -initrd initrd.gz \
  -append "earlycon=pl011,mmio32,0xfe201000 console=ttyAMA0 rdinit=/init" \
  -nic user \
  -device usb-kbd \
  -chardev null,id=ser0 -device usb-serial,chardev=ser0 \
  -serial stdio -display none
```

USB 2.0 devices attach to the DWC2 controller. The guest sees standard `/dev/ttyUSB*` serial ports and `/dev/input/*` HID devices.
```

- [ ] **Step 3: Verify README renders correctly**

Review the markdown manually to ensure the code blocks and formatting are correct.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "Docs: correct USB support status in README

DWC2 USB 2.0 works on raspi4b -- keyboard and serial devices
enumerate correctly. Update Known Limitations to say 'No USB 3.0'
instead of 'No USB', and add USB device examples to Usage."
```

---

### Task 7: Update PXE Boot Test with USB (Optional)

**Files:**
- Modify: `run-rpi-pxeboot-test.py:119-125` (QEMU Popen args)
- Modify: `run-rpi-pxeboot-test.py:164-174` (checks list)

If Tasks 2-4 succeed, extend the PXE boot test to also verify USB. This is optional since PXE boot doesn't interact with USB, but confirms USB works in the autonomous boot path too.

- [ ] **Step 1: Add USB devices to PXE test QEMU command**

In `run-rpi-pxeboot-test.py`, add the same USB devices as the interactive test:

```python
proc = subprocess.Popen(
    [str(QEMU), "-M", "raspi4b",
     "-kernel", str(FIRMWARE), "-dtb", str(DTB),
     "-nic", f"user,tftp={TFTPBOOT}",
     "-device", "usb-kbd",
     "-chardev", "null,id=usb-serial0",
     "-device", "usb-serial,chardev=usb-serial0",
     "-serial", "stdio", "-display", "none", "-monitor", "none"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    stderr=subprocess.PIPE, text=True)
```

- [ ] **Step 2: Add USB checkpoints to PXE test**

Add to the checks list (same patterns as interactive test):

```python
    ("DWC2 USB",            "dwc2"),
    ("USB device",          "USB:"),
    ("USB serial",          "ttyUSB"),
```

- [ ] **Step 3: Run PXE test to verify**

Run: `uv run run-rpi-pxeboot-test.py`

Expected: All checks pass including USB.

- [ ] **Step 4: Commit**

```bash
git add run-rpi-pxeboot-test.py
git commit -m "Test: add USB device verification to PXE boot test

Same USB devices and checkpoints as the interactive boot test.
Confirms USB works in the autonomous PXE boot path."
```

---

## Debugging Guide

If USB doesn't work at all (QEMU starts but no USB in dmesg):

1. **Check QEMU stderr**: QEMU may print warnings about the USB bus or device attachment. The test script currently suppresses stderr (`read_stderr` discards it). Temporarily capture stderr for debugging.

2. **Check kernel config**: The stock RPi kernel (`kernel8.img` from `raspberrypi/firmware`) may not have `CONFIG_USB_DWC2=y`. Verify with: `zcat kernel8.img | strings | grep -i dwc2` or check the kernel's `/proc/config.gz` if available.

3. **Check device tree**: The DWC2 node in the BCM2711 DTB may be disabled or have wrong compatible strings. Inspect with: `dtc -I dtb -O dts bcm2711-rpi-4-b.dtb | grep -A5 "usb@"`.

4. **Try a different kernel**: Use a mainline kernel instead of the RPi kernel. The mainline kernel's `dwc2` driver is the reference implementation for QEMU's DWC2 emulation.

5. **Check QEMU DWC2 with raspi3b instead**: If raspi4b has issues, try `-M raspi3b` which has been tested more extensively with DWC2 in the QEMU test suite (`tests/functional/aarch64/test_raspi3.py`).
