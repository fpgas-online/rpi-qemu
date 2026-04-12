# RPi OS Boot to Login Prompt -- Design Spec

## Problem

QEMU raspi4b with GENET patches can PXE boot an initramfs and run network tests, but cannot boot a real Raspberry Pi OS Lite SD card image to a login prompt. The kernel hangs at "Waiting for root device /dev/mmcblk0p2..." because the SD card appears as `mmcblk1` instead of `mmcblk0`.

## Evidence

Boot test with real RPi OS Lite image (2026-04-12) showed:
- Kernel boots all 4 CPUs, initializes GENET, DWC2, framebuffer, RNG, watchdog
- SD card detected: `mmcblk1: mmc1:3b9b QEMU! 2.57 GiB` with partitions `p1 p2`
- Hangs: `Waiting for root device /dev/mmcblk0p2...` (wrong device name)
- PCIe fails gracefully: `PCIe RC controller misconfigured as Endpoint` (error -22)
- GPIO firmware errors: `Failed to get GPIO 0 config (0 80)` (repeated for GPIOs 0-7)

Three issues identified, all fixable in the QEMU source.

## Fix 1: SD Card Routing to EMMC2

### Background

On real RPi 4B hardware:
- EMMC2 (offset 0x340000) is the SD card controller -> mmcblk0
- Arasan SDHCI (offset 0x300000) is for WiFi -> mmcblk1
- SDHOST (offset 0x202000) is the legacy controller (unused by default)

The DTB has aliases `mmc0 = &emmc2` and `mmc1 = &mmcnr` which enforce this numbering.

### Root Cause

In `bcm2838_peripherals.c`, the GPIO SD bus multiplexer links:
```c
object_property_add_const_link(OBJECT(&s->gpio), "sdbus-sdhci",
                               OBJECT(&s_base->sdhci.sdbus));  // Points to EMMC1!
```

This points to the base SoC's SDHCI (EMMC1 at 0x300000), not EMMC2 (at 0x340000). The GPIO mux defaults to routing the SD card to "SDHCI" on reset, so the card ends up on EMMC1 -> mmc1 -> mmcblk1.

### Fix

Change the "sdbus-sdhci" link to point to EMMC2's SD bus:
```c
object_property_add_const_link(OBJECT(&s->gpio), "sdbus-sdhci",
                               OBJECT(&s->emmc2.sdbus));  // Points to EMMC2
```

Result: GPIO mux routes SD card to EMMC2 -> mmc0 -> mmcblk0 -> RPi OS finds root.

### Files

- `upstream-qemu/hw/arm/bcm2838_peripherals.c` (1 line change)

## Fix 2: PCIe Port Type Status Bit

### Background

The Linux kernel's `brcm-pcie` driver reads `PCIE_MISC_PCIE_STATUS` at offset 0x4068. Bit 7 (`PCIE_PORT`) indicates whether the controller is in Root Port mode (1) or Endpoint mode (0). When the bit is 0, the driver returns EINVAL with "PCIe RC controller misconfigured as Endpoint".

### Root Cause

All vendor registers are zero-initialized (fixed in patch 0019). This includes the status register at offset 0x4068, where bit 7 should be 1 to indicate Root Port mode.

### Fix

In `bcm2838_pcie_root_port_reset_hold()`, after zeroing the register array, set the PCIE_PORT bit:

```c
#define BCM2838_PCIE_MISC_PCIE_STATUS   0x4068
#define BCM2838_PCIE_PCIE_PORT_MASK     0x80    /* Bit 7: 1=RC, 0=EP */

memset(s->regs, 0x00, sizeof(s->regs));

/* Indicate Root Port mode (not Endpoint) in PCIE_MISC_PCIE_STATUS */
uint32_t *pcie_status = (uint32_t *)(s->regs
    + BCM2838_PCIE_MISC_PCIE_STATUS - PCIE_CONFIG_SPACE_SIZE);
*pcie_status = BCM2838_PCIE_PCIE_PORT_MASK;
```

Note: plain assignment, no `cpu_to_le32()`. The `regs[]` array stores host-native values because the MMIO ops use `DEVICE_NATIVE_ENDIAN` and the read handler uses raw `memcpy`. Byte-swapping happens at the QEMU memory region boundary, not in the array.

Result: Driver passes the port-type check, reaches the link check, sees link-down, prints "link down" and returns ENODEV gracefully.

### Files

- `upstream-qemu/hw/arm/bcm2838_pcie.c` (add ~5 lines in reset handler)
- `upstream-qemu/include/hw/arm/bcm2838_pcie.h` (add 2 defines)

## Fix 3: Firmware GPIO Config Mailbox Stubs

### Background

The `raspberrypi-exp-gpio` kernel driver queries the VideoCore firmware via mailbox property tags to get/set GPIO configuration. Four tags are involved:

| Tag | Value | Direction |
|-----|-------|-----------|
| `RPI_FWREQ_GET_GPIO_STATE` | 0x00030041 | Read GPIO pin state |
| `RPI_FWREQ_SET_GPIO_STATE` | 0x00038041 | Write GPIO pin state |
| `RPI_FWREQ_GET_GPIO_CONFIG` | 0x00030043 | Read GPIO config (direction, polarity, pull) |
| `RPI_FWREQ_SET_GPIO_CONFIG` | 0x00038043 | Write GPIO config |

### Root Cause

None of these tags are handled in `bcm2835_property.c`. They fall through to the default case which returns a zero-length response. The kernel interprets this as an error, causing "Failed to get GPIO config" messages and cascading failures in LED and voltage regulator probes.

### Fix

Add case handlers in the `bcm2835_property_mbox_push()` switch statement.

**Critical protocol detail**: The kernel driver (`gpio-raspberrypi-exp.c`) checks success with `if (ret || get.gpio != 0)`. The `gpio` field (first word of the response payload at `value+12`) is a **firmware error code**, not the GPIO number. It must be written as **0** to indicate success. Any non-zero value causes the driver to report failure.

Response buffer layouts and `resplen` values:

| Tag | resplen | Response fields (at value+12) |
|-----|---------|-------------------------------|
| GET_GPIO_STATE | 8 | gpio=0 (success), state=0 (low) |
| SET_GPIO_STATE | 8 | gpio=0 (success), state=(echoed) |
| GET_GPIO_CONFIG | 20 | gpio=0, direction=1 (input), polarity=0 (active-high), term_en=0, term_pull_up=0 |
| SET_GPIO_CONFIG | 24 | gpio=0, direction, polarity, term_en, term_pull_up, state (all accepted, ignored) |

Handlers:
- **GET_GPIO_STATE**: Write gpio=0, state=0. resplen=8.
- **SET_GPIO_STATE**: Write gpio=0. resplen=8. No-op.
- **GET_GPIO_CONFIG**: Write gpio=0, direction=1, polarity=0, term_en=0, term_pull_up=0. resplen=20.
- **SET_GPIO_CONFIG**: Write gpio=0. resplen=24. No-op (accept and ignore config).

### Files

- `upstream-qemu/hw/misc/bcm2835_property.c` (add ~40 lines in switch statement)

## Patch Organization

Three patches in `ci/qemu-patches/`:
1. `0020-Fix-SD-card-routing-to-EMMC2-for-correct-mmcblk0.patch`
2. `0021-Fix-PCIe-port-type-bit-in-status-register.patch`
3. `0022-Add-firmware-GPIO-config-mailbox-stubs.patch`

## Verification

1. Build: `cd upstream-qemu/build && make -j$(nproc) qemu-system-aarch64`
2. Boot RPi OS image:
   ```
   qemu-system-aarch64 -M raspi4b \
     -kernel test-images/Image -dtb test-images/bcm2711-rpi-4-b.dtb \
     -sd test-images/rpi-os-lite.img \
     -append "earlycon=pl011,mmio32,0xfe201000 console=ttyAMA0 root=/dev/mmcblk0p2 rootfstype=ext4 rootwait" \
     -serial stdio -display none -nic user
   ```
3. Expected: kernel mounts mmcblk0p2, systemd starts, login prompt appears on serial
4. Also verify existing PXE test still passes: `uv run run-rpi-pxeboot-test.py`
