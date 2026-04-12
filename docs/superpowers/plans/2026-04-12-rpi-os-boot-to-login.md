# RPi OS Boot to Login Prompt -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable QEMU raspi4b to boot a real Raspberry Pi OS Lite SD card image to a serial login prompt.

**Architecture:** Three independent fixes in the QEMU device emulation layer: (1) redirect the SD card from EMMC1 to EMMC2 so it appears as mmcblk0, (2) set the PCIe status register port-type bit so the kernel driver reports "link down" instead of "misconfigured as Endpoint", (3) add firmware GPIO mailbox stubs so the exp-gpio driver doesn't spam errors and cascade-fail regulators/LEDs.

**Tech Stack:** C (QEMU device model), git format-patch

**Spec:** `docs/superpowers/specs/2026-04-12-rpi-os-boot-to-login-design.md`

---

### Task 1: Fix SD card routing to EMMC2

**Files:**
- Modify: `upstream-qemu/hw/arm/bcm2838_peripherals.c:62-63`

- [ ] **Step 1: Edit the GPIO sdbus-sdhci link**

In `upstream-qemu/hw/arm/bcm2838_peripherals.c`, line 62-63, change the "sdbus-sdhci" link from the base SoC SDHCI to EMMC2:

```c
// Before (line 62-63):
    object_property_add_const_link(OBJECT(&s->gpio), "sdbus-sdhci",
                                   OBJECT(&s_base->sdhci.sdbus));

// After:
    object_property_add_const_link(OBJECT(&s->gpio), "sdbus-sdhci",
                                   OBJECT(&s->emmc2.sdbus));
```

Context: `s->emmc2` is `SDHCIState` (declared at `include/hw/arm/bcm2838_peripherals.h:72`). `SDHCIState.sdbus` is `SDBus` (declared at `include/hw/sd/sdhci.h:42`). The GPIO mux defaults to routing the SD card to "sdhci" on reset (`hw/gpio/bcm2838_gpio.c:307`), so this change makes the card appear on EMMC2 at 0x340000. The DTB alias `mmc0 = &emmc2` ensures it becomes mmcblk0.

- [ ] **Step 2: Incremental build to verify compilation**

Run: `cd /home/tim/github/fpgas-online/rpi-qemu/upstream-qemu/build && make -j$(nproc) qemu-system-aarch64 2>&1 | tail -5`
Expected: Compiles with `bcm2838_peripherals.c` recompiled, links successfully.

- [ ] **Step 3: Generate and save patch**

```bash
cd /home/tim/github/fpgas-online/rpi-qemu/upstream-qemu
git diff hw/arm/bcm2838_peripherals.c
```

Create `ci/qemu-patches/0020-Fix-SD-card-routing-to-EMMC2-for-correct-mmcblk0.patch` using the diff wrapped in the standard git format-patch envelope (match format of existing patches like 0018). Commit message:

```
Fix SD card routing: use EMMC2 instead of EMMC1 for mmcblk0

The GPIO SD bus multiplexer linked "sdbus-sdhci" to the base SoC's
SDHCI (EMMC1 at 0x300000), causing the SD card to appear as mmcblk1.
On real RPi 4B, the SD card uses EMMC2 (at 0x340000), which the DTB
aliases as mmc0 -> mmcblk0.  Redirect the link to EMMC2 so RPi OS
can find root=/dev/mmcblk0p2.
```

- [ ] **Step 4: Commit the patch file**

```bash
git add ci/qemu-patches/0020-Fix-SD-card-routing-to-EMMC2-for-correct-mmcblk0.patch
git commit -m "Fix: SD card routing to EMMC2 for correct mmcblk0 device name"
```

---

### Task 2: Fix PCIe port-type bit in status register

**Files:**
- Modify: `upstream-qemu/hw/arm/bcm2838_pcie.c:253`
- Modify: `upstream-qemu/include/hw/arm/bcm2838_pcie.h`

- [ ] **Step 1: Add register defines to the header**

In `upstream-qemu/include/hw/arm/bcm2838_pcie.h`, after the existing `#define BCM2838_PCIE_EXT_CFG_INDEX  0x9000` (line 37), add:

```c
#define BCM2838_PCIE_MISC_PCIE_STATUS   0x4068
#define BCM2838_PCIE_PCIE_PORT_MASK     0x80    /* Bit 7: 1=RC, 0=EP */
```

- [ ] **Step 2: Set the port-type bit in the reset handler**

In `upstream-qemu/hw/arm/bcm2838_pcie.c`, in `bcm2838_pcie_root_port_reset_hold()`, after the `memset` at line 253, add:

```c
    memset(s->regs, 0x00, sizeof(s->regs));

    /*
     * Set PCIE_PORT bit in PCIE_MISC_PCIE_STATUS to indicate Root Complex
     * mode. Without this, the Linux brcm-pcie driver reports "PCIe RC
     * controller misconfigured as Endpoint" and returns -EINVAL.
     */
    *(uint32_t *)(s->regs + BCM2838_PCIE_MISC_PCIE_STATUS
                  - PCIE_CONFIG_SPACE_SIZE) = BCM2838_PCIE_PCIE_PORT_MASK;
```

Note: plain assignment, no `cpu_to_le32()`. The `regs[]` array stores host-native values (MMIO ops use `DEVICE_NATIVE_ENDIAN`, read handler uses raw `memcpy`).

- [ ] **Step 3: Incremental build**

Run: `cd /home/tim/github/fpgas-online/rpi-qemu/upstream-qemu/build && make -j$(nproc) qemu-system-aarch64 2>&1 | tail -5`
Expected: Both `bcm2838_pcie.c` and possibly header-dependent files recompile successfully.

- [ ] **Step 4: Generate and save patch**

Create `ci/qemu-patches/0021-Fix-PCIe-port-type-bit-in-status-register.patch` from `git diff` of both files. Commit message:

```
Fix PCIe: set PCIE_PORT bit in PCIE_MISC_PCIE_STATUS on reset

The zero-initialized vendor registers left PCIE_MISC_PCIE_STATUS
bit 7 (PCIE_PORT) clear, indicating Endpoint mode.  The Linux
brcm-pcie driver rejected this with "PCIe RC controller
misconfigured as Endpoint" (EINVAL).  Set the bit to indicate Root
Complex mode so the driver reaches the link-check path and reports
"link down" (ENODEV) gracefully.
```

- [ ] **Step 5: Commit the patch file**

```bash
git add ci/qemu-patches/0021-Fix-PCIe-port-type-bit-in-status-register.patch
git commit -m "Fix: PCIe port-type bit in PCIE_MISC_PCIE_STATUS register"
```

---

### Task 3: Add firmware GPIO config mailbox stubs

**Files:**
- Modify: `upstream-qemu/hw/misc/bcm2835_property.c:417` (before the `default:` case)

- [ ] **Step 1: Add GPIO tag handlers**

In `upstream-qemu/hw/misc/bcm2835_property.c`, insert the following cases before the `default:` case at line 418. Follow the existing pattern of using `ldl_le_phys`/`stl_le_phys` for DMA memory access:

```c
        case RPI_FWREQ_GET_GPIO_STATE:
            /* gpio=0 (success), state=0 (low) */
            stl_le_phys(&s->dma_as, value + 12, 0);
            stl_le_phys(&s->dma_as, value + 16, 0);
            resplen = 8;
            break;
        case RPI_FWREQ_SET_GPIO_STATE:
            /* gpio=0 (success); accept and ignore the state write */
            stl_le_phys(&s->dma_as, value + 12, 0);
            resplen = 8;
            break;
        case RPI_FWREQ_GET_GPIO_CONFIG:
            /* gpio=0 (success), direction=1 (input), polarity=0, term_en=0,
             * term_pull_up=0 */
            stl_le_phys(&s->dma_as, value + 12, 0);
            stl_le_phys(&s->dma_as, value + 16, 1);
            stl_le_phys(&s->dma_as, value + 20, 0);
            stl_le_phys(&s->dma_as, value + 24, 0);
            stl_le_phys(&s->dma_as, value + 28, 0);
            resplen = 20;
            break;
        case RPI_FWREQ_SET_GPIO_CONFIG:
            /* gpio=0 (success); accept and ignore the config write */
            stl_le_phys(&s->dma_as, value + 12, 0);
            resplen = 24;
            break;
```

Critical: the first word at `value + 12` is the `gpio` field which the kernel driver checks as `if (ret || get.gpio != 0)` -- it is a firmware error code, NOT the GPIO number. Must be 0 for success.

- [ ] **Step 2: Incremental build**

Run: `cd /home/tim/github/fpgas-online/rpi-qemu/upstream-qemu/build && make -j$(nproc) qemu-system-aarch64 2>&1 | tail -5`
Expected: `bcm2835_property.c` recompiles successfully.

- [ ] **Step 3: Generate and save patch**

Create `ci/qemu-patches/0022-Add-firmware-GPIO-config-mailbox-stubs.patch` from `git diff`. Commit message:

```
Add firmware GPIO config/state mailbox stubs

The raspberrypi-exp-gpio kernel driver queries the VideoCore
firmware via mailbox tags GET/SET_GPIO_CONFIG and GET/SET_GPIO_STATE.
These tags were unhandled, causing "Failed to get GPIO config"
errors and cascading regulator/LED probe failures.

Add stub handlers that return success (gpio=0) with sensible
defaults (direction=input, polarity=active-high, state=low).
```

- [ ] **Step 4: Commit the patch file**

```bash
git add ci/qemu-patches/0022-Add-firmware-GPIO-config-mailbox-stubs.patch
git commit -m "Add: firmware GPIO config/state mailbox stubs"
```

---

### Task 4: Integration test -- boot RPi OS to login prompt

**Files:** None modified (verification only)

- [ ] **Step 1: Boot RPi OS image and verify login prompt**

Create `tmp/test-rpi-os-login.py` (modelled on `run-rpi-pxeboot-test.py`):

```python
#!/usr/bin/env python3
"""Boot real RPi OS Lite image and check for login prompt."""
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent.resolve()
QEMU = BASE / "upstream-qemu" / "build" / "qemu-system-aarch64"
KERNEL = BASE / "test-images" / "Image"
DTB = BASE / "test-images" / "bcm2711-rpi-4-b.dtb"
SD_IMG = BASE / "test-images" / "rpi-os-lite.img"

for f in [QEMU, KERNEL, DTB, SD_IMG]:
    if not f.exists():
        print(f"MISSING: {f}")
        sys.exit(1)

cmd = [
    str(QEMU), "-M", "raspi4b",
    "-kernel", str(KERNEL),
    "-dtb", str(DTB),
    "-sd", str(SD_IMG),
    "-append",
    "earlycon=pl011,mmio32,0xfe201000 console=ttyAMA0 loglevel=7 "
    "root=/dev/mmcblk0p2 rootfstype=ext4 rootwait fsck.repair=no",
    "-serial", "stdio", "-display", "none", "-nic", "user",
]

try:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    output = proc.stdout
except subprocess.TimeoutExpired as e:
    output = e.stdout.decode() if e.stdout else ""

checks = {
    "SD card as mmcblk0": "mmcblk0:" in output,
    "PCIe link down (not Endpoint)": "link down" in output
        and "misconfigured as Endpoint" not in output,
    "No GPIO config errors": "Failed to get GPIO" not in output,
    "Login prompt": "login:" in output,
}

print("=" * 60)
for name, passed in checks.items():
    status = "PASS" if passed else "FAIL"
    print(f"  [{status}] {name}")
print("=" * 60)

if not all(checks.values()):
    # Dump last 80 lines for debugging
    lines = output.splitlines()
    print("\nLast 80 lines of serial output:")
    for line in lines[-80:]:
        print(f"  > {line}")
    sys.exit(1)

print("\nALL CHECKS PASSED")
```

Run: `uv run tmp/test-rpi-os-login.py`
Expected: All 4 checks pass (SD card on mmcblk0, PCIe link down, no GPIO errors, login prompt).

- [ ] **Step 2: Verify PXE boot regression test still passes**

Run: `uv run run-rpi-pxeboot-test.py`
Expected: `ALL TESTS PASSED` (15/15 checks)

- [ ] **Step 3: Clean up temporary test files**

Remove `tmp/test-rpi-os-login.py`.

- [ ] **Step 4: Push all commits**

```bash
git push origin main
```
