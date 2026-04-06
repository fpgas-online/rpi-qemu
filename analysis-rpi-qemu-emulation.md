# Raspberry Pi Device Emulation in QEMU: Deep Analysis

**Date**: 2026-04-06
**QEMU Version Analysed**: v11.0.0-rc2 (upstream `gitlab.com/qemu-project/qemu`)
**Goal**: Assess current fidelity of RPi hardware emulation, identify gaps (especially networking), and catalogue pending work that could improve emulation quality.

---

## 1. Supported Board Models

QEMU emulates 6 Raspberry Pi boards across 4 SoC generations:

| Machine   | SoC      | CPU              | Cores | RAM        | Arch    |
|-----------|----------|------------------|-------|------------|---------|
| `raspi0`  | BCM2835  | ARM1176JZF-S     | 1     | 512 MiB    | ARM32   |
| `raspi1ap`| BCM2835  | ARM1176JZF-S     | 1     | 512 MiB    | ARM32   |
| `raspi2b` | BCM2836  | Cortex-A7        | 4     | 1 GiB      | ARM32   |
| `raspi3ap`| BCM2837  | Cortex-A53       | 4     | 512 MiB    | AArch64 |
| `raspi3b` | BCM2837  | Cortex-A53       | 4     | 1 GiB      | AArch64 |
| `raspi4b` | BCM2838  | Cortex-A72       | 4     | 1-2 GiB    | AArch64 |

**Not emulated**: Raspberry Pi 5 (BCM2712), Raspberry Pi Zero 2 W, Compute Modules.

### Key Source Files

- `hw/arm/raspi.c` -- Machine definitions for Pi 0/1/2/3
- `hw/arm/raspi4b.c` -- Pi 4B machine definition
- `hw/arm/bcm2836.c` -- SoC model for BCM2835/2836/2837
- `hw/arm/bcm2838.c` -- SoC model for BCM2838
- `hw/arm/bcm2835_peripherals.c` -- Base peripheral wiring (shared)
- `hw/arm/bcm2838_peripherals.c` -- BCM2838-specific peripheral wiring

---

## 2. Peripheral Device Completeness

### 2.1 Fully Functional Peripherals

| Peripheral | Files | Notes |
|---|---|---|
| **Interrupt Controller** (BCM2835 IC) | `hw/intc/bcm2835_ic.c` | 64 GPU + 8 ARM IRQs |
| **BCM2836 Control** (per-core IRQ routing) | `hw/intc/bcm2836_control.c` | Mailboxes, timer routing |
| **GICv2** (BCM2838 only) | ARM GIC via `hw/arm/bcm2838.c` | 192 SPIs, virt extensions |
| **DMA Controller** | `hw/dma/bcm2835_dma.c` | 16 channels, 2D transfers |
| **System Timer** | `hw/timer/bcm2835_systmr.c` | 64-bit free-running + 4 compares |
| **GPIO** | `hw/gpio/bcm2835_gpio.c`, `bcm2838_gpio.c` | 54 pins, SD bus switching |
| **UART0 (PL011)** | PL011 standard | Full UART |
| **Mailbox** | `hw/misc/bcm2835_mbox.c` | ARM-to-VC, 8 channels |
| **Property Channel** | `hw/misc/bcm2835_property.c` | Firmware property interface |
| **Frame Buffer** | `hw/display/bcm2835_fb.c` | Virtual/physical resolution |
| **SD Host** | `hw/sd/bcm2835_sdhost.c` | FIFO-based, no DMA |
| **SDHCI (EMMC1/EMMC2)** | Generic SDHCI | SD spec v2/v3 |
| **DWC2 USB** | `hw/usb/hcd-dwc2.c` | Host-mode only (see 2.2) |
| **I2C** (3 controllers) | `hw/i2c/bcm2835_i2c.c` | BSC0/BSC1/BSC2 |
| **SPI** | `hw/ssi/bcm2835_spi.c` | SPI0 master, no DMA/LoSSI |
| **Clock Manager (CPRMAN)** | `hw/misc/bcm2835_cprman.c` | Full clock tree |
| **RNG** | `hw/misc/bcm2835_rng.c` | Uses QEMU guest entropy |
| **Power Management** | `hw/misc/bcm2835_powermgt.c` | Reset/shutdown/watchdog |

### 2.2 Partially Implemented Peripherals

| Peripheral | Status | Gap |
|---|---|---|
| **DWC2 USB** | Host-only | No device/OTG mode, no slave mode FIFOs. `TODO` at `hcd-dwc2.c:738` |
| **AUX (Mini UART)** | Core UART only | No line/modem control, baudrate, SPI1/SPI2 |
| **SPI** | Transfer-active only | DMA and LoSSI modes unimplemented |
| **Thermal** | Stub | Hardcoded 25C. `hw/misc/bcm2835_thermal.c` |
| **OTP** | Stub | Only row access; registers undocumented. `hw/nvram/bcm2835_otp.c` |
| **MPHI** | Minimal stub | Just FIQ interrupt for DWC-OTG driver. `hw/misc/bcm2835_mphi.c` |
| **Property Channel** | Most tags | Missing: `GET_BOARD_MODEL`, `GET_BOARD_SERIAL`, clock set/get. `TODO` at line 548: _"connect to MAC address of USB NIC device, once we emulate it"_ |

### 2.3 Unimplemented Devices (Stubs via `UnimplementedDeviceState`)

These are mapped to prevent guest crashes but return zero / log accesses:

| Device | Offset | Size |
|---|---|---|
| Transposer (TXP) | 0x4000 | 0x1000 |
| ARM Timer (SP804) | 0xB400 | 0x40 |
| I2S/PCM | 0x203000 | 0x100 |
| SMI | 0x600000 | 0x100 |
| BSC Slave | 0x214000 | 0x100 |
| DBUS | 0x900000 | 0x8000 |
| AVE0 | 0x910000 | 0x8000 |
| V3D (3D Graphics) | 0xC00000 | 0x1000 |
| SDRAMC | 0xE00000 | 0x100 |
| Clock ISP (BCM2838) | 0xC11000 | 0x100 |
| RPiVid ASB (BCM2838) | 0xA000 | 0x24 |

### 2.4 Completely Missing (BCM2838 / RPi 4B)

These devices are **explicitly disabled in the device tree** at boot (`raspi4b.c:66-71`):

| Device | DT Compatible | GIC SPI IRQ | Status |
|---|---|---|---|
| **GENET Ethernet** | `brcm,bcm2711-genet-v5` | 157, 158 | **No code exists** |
| **PCIe Root Port** | `brcm,bcm2711-pcie` | 143 | **No code exists** |
| **RNG200** | `brcm,bcm2711-rng200` | 125 | **No code exists** |
| **BCM2711 Thermal** | `brcm,bcm2711-thermal` | -- | **No code exists** |
| **PWM** | -- | -- | Missing on all models |
| **XHCI USB** | -- | -- | RPi4 USB3 not available |

---

## 3. Network Emulation: The Critical Gap

### 3.1 Current State: No Network on Any RPi Model

**None of the 6 RPi machine types have network connectivity out of the box.**

| RPi Model | Real Hardware | QEMU Status |
|---|---|---|
| RPi 0/1 | No built-in Ethernet | N/A |
| RPi 2B | No built-in Ethernet (via USB hub) | N/A |
| RPi 3B/3B+ | USB Ethernet (LAN9514/LAN7515) | **Not emulated** |
| RPi 4B | GENET + BCM54213PE GbE | **Disabled in DTB, no code** |

### 3.2 Why RPi 3 Has No Network

The RPi 3B uses a LAN9514 USB Ethernet hub chip (Microchip/SMSC). While QEMU has:
- **DWC2 USB host controller** (functional, host-mode)
- **USB CDC/RNDIS network device** (`hw/usb/dev-network.c`)

...these are **never connected** in the RPi machine definition. The USB network device could theoretically be attached manually via command-line (`-device usb-net,netdev=net0`), but this doesn't match real hardware and has reported reliability issues.

A proper fix would require implementing a LAN9514 USB device model, or at minimum, auto-attaching a USB network device in the machine init code.

### 3.3 Why RPi 4 Has No Network

The RPi 4B uses a **Broadcom GENET v5** (Generic Ethernet) MAC controller connected to a **BCM54213PE** Gigabit Ethernet PHY. This is a dedicated Ethernet controller at a fixed MMIO address -- not USB-based like the RPi 3.

**No GENET emulation code exists in QEMU.** The interrupt numbers are reserved:
```c
// include/hw/arm/bcm2838_peripherals.h
#define GIC_SPI_INTERRUPT_GENET_A      157
#define GIC_SPI_INTERRUPT_GENET_B      158
```

The device tree nodes are actively disabled at boot:
```c
// hw/arm/raspi4b.c:66-71
const char *nodes_to_remove[] = {
    "brcm,bcm2711-pcie",
    "brcm,bcm2711-rng200",
    "brcm,bcm2711-thermal",
    "brcm,bcm2711-genet-v5",
};
```

### 3.4 GENET Technical Specifications

**No public Broadcom datasheet exists.** All specifications below are derived from the Linux kernel driver (`drivers/net/ethernet/broadcom/genet/`), U-Boot driver (`drivers/net/bcmgenet.c`), the RPi device tree (`bcm2711.dtsi`), the unmerged QEMU patch series, and the [Ultibo GENET Unit](https://ultibo.org/wiki/Unit_GENET) documentation.

#### Address Mapping

| Property | Value |
|---|---|
| DTS bus address | `0x7d580000` |
| CPU-mapped address | `0xfd580000` (via SCB: `0x7c000000` -> `0xfc000000`) |
| QEMU peripheral offset | `0x1580000` (from peri_low base `0xfc000000`) |
| Region size | `0x10000` (64 KiB) |
| DT compatible | `brcm,bcm2711-genet-v5` |
| IRQs | GIC SPI 157 (default), SPI 158 (priority) |
| PHY | BCM54213PE via RGMII, MDIO at UMAC+0x614 |

#### Register Block Layout (offsets from base)

| Block | Offset | Size | Purpose |
|---|---|---|---|
| SYS | `0x0000` | `0x40` | System control, revision ID |
| GR_BRIDGE | `0x0040` | `0x40` | Bridge control |
| EXT | `0x0080` | `0x80` | External interface config |
| INTRL2_0 | `0x0200` | `0x40` | Level 2 IRQ controller (default) |
| INTRL2_1 | `0x0240` | `0x40` | Level 2 IRQ controller (priority) |
| RBUF | `0x0300` | `0x200` | Receive buffer control |
| TBUF | `0x0600` | `0x200` | Transmit buffer control |
| UMAC | `0x0800` | `0x800` | UniMAC core (MAC engine) |
| RDMA | `0x2000` | `0x2000` | RX DMA descriptors + ring regs + ctrl |
| TDMA | `0x4000` | `0x2000` | TX DMA descriptors + ring regs + ctrl |
| HFB data | `0x8000` | `0x7C00` | Hardware Filter Block data |
| HFB regs | `0xFC00` | `0x400` | Hardware Filter Block registers |

#### DMA Descriptor Format (12 bytes each)

| Field | Offset | Description |
|---|---|---|
| LENGTH_STATUS | `+0x00` | bits[31:16]=length, bits[15:0]=status flags |
| ADDRESS_LO | `+0x04` | Lower 32-bit DMA address |
| ADDRESS_HI | `+0x08` | Upper 32-bit DMA address (GENET v4+) |

Status flags: `DMA_OWN=0x8000`, `DMA_EOP=0x4000`, `DMA_SOP=0x2000`, `DMA_WRAP=0x1000`

#### DMA Ring Architecture

- **Total descriptors**: 256 per direction (shared across all rings)
- **Total rings**: 17 (indices 0-16; ring 16 = default queue)
- **Priority queues**: 4 TX, up to 16 RX (configurable)
- **Ring register block**: `0x40` per ring
- **Descriptor area**: 256 x 12 = 3072 bytes (`0xC00`)
- **RDMA ring regs**: base `0x2C00` (= `0x2000 + 0xC00`)
- **TDMA ring regs**: base `0x4C00` (= `0x4000 + 0xC00`)
- **RDMA ctrl regs**: `0x2C00 + 17*0x40`
- **TDMA ctrl regs**: `0x4C00 + 17*0x40`
- **SCB burst size**: `0x08`

#### Key Registers for Minimal Emulation

| Register | Offset | Purpose |
|---|---|---|
| `SYS_REV_CTRL` | `0x00` | Version ID; bits[27:24] must = 6 (GENET_V5) |
| `SYS_PORT_CTRL` | `0x04` | Port mode (`EXT_GPHY=3`) |
| `UMAC_CMD` | `0x808` | TX/RX enable, speed, reset |
| `UMAC_MAC0` | `0x80C` | MAC address bytes 0-3 |
| `UMAC_MAC1` | `0x810` | MAC address bytes 4-5 |
| `UMAC_MDIO_CMD` | `0xE14` | MDIO read/write to PHY |
| `DMA_CTRL` | ring ctrl + `0x04` | `DMA_EN` bit 0 |
| `INTRL2_0_*` | `0x200-0x20C` | IRQ0 status/set/clear/mask |
| `INTRL2_1_*` | `0x240-0x24C` | IRQ1 per-queue TX/RX interrupts |

#### Reference Source Code

- Linux: [`drivers/net/ethernet/broadcom/genet/bcmgenet.h`](https://github.com/torvalds/linux/blob/master/drivers/net/ethernet/broadcom/genet/bcmgenet.h)
- Linux: [`drivers/net/ethernet/broadcom/genet/bcmgenet.c`](https://github.com/torvalds/linux/blob/master/drivers/net/ethernet/broadcom/genet/bcmgenet.c)
- U-Boot (simpler, single-queue): [`drivers/net/bcmgenet.c`](https://github.com/u-boot/u-boot/blob/master/drivers/net/bcmgenet.c)
- Device tree: [`arch/arm/boot/dts/broadcom/bcm2711.dtsi`](https://github.com/raspberrypi/linux/blob/rpi-6.6.y/arch/arm/boot/dts/broadcom/bcm2711.dtsi)
- BCM2711 ARM Peripherals PDF: https://datasheets.raspberrypi.com/bcm2711/bcm2711-peripherals.pdf
- Ultibo wiki: https://ultibo.org/wiki/Unit_GENET

### 3.5 Existing QEMU Network Device Models (Reference)

These existing implementations demonstrate the patterns needed for a GENET model:

| Device | Lines | Architecture | Relevance |
|---|---|---|---|
| `cadence_gem.c` | 1860 | DMA descriptor rings, MDIO PHY | **Most similar** to GENET |
| `lan9118.c` | 1350 | FIFO-based, memory-mapped | Simpler but relevant pattern |
| `ftgmac100.c` | 1452 | DMA descriptor rings, GbE | Good reference |
| `imx_fec.c` | 1278 | DMA-based embedded MAC | Similar complexity |

A GENET implementation would likely be **1500-2500 lines** based on comparable devices.

---

## 4. Unmerged Patch Series: Kambalin RPi4B Peripherals

### 4.1 Patch Series History

Sergey Kambalin (Auriga) submitted a comprehensive RPi 4B patch series through multiple revisions:

| Version | Date | Patches | Status |
|---|---|---|---|
| v1 | 2023-07 | 44 | RFC |
| v2 | 2023-12-03 | 45 | Review |
| v3 | 2023-12-04 | 45 | Review |
| v4 | 2023-12-08 | 45 | Review |
| v5 | 2024-02 | 41 | Review |
| **v6** | **2024-02-26** | **41** | **Partial merge** |

### 4.2 What Was Merged (in QEMU ~9.0)

The base RPi4B machine support was merged by Peter Maydell into `target-arm.next`:
- BCM2838 SoC definition
- BCM2838 GPIO controller
- GICv2 integration
- EMMC2 (second SD controller)
- BCM2838 peripheral wiring
- raspi4b machine type
- Documentation (listing GENET/PCIe as "missing")

### 4.3 What Was NOT Merged

The following patches exist in the v6 series but were **not accepted into mainline**:

| Component | Patches | Reason for Non-Merge |
|---|---|---|
| **BCM2838 PCIe Root Complex** | 3 patches | Review feedback, complexity |
| **RNG200** | 1 patch | Review feedback |
| **BCM2838 Thermal Sensor** | 1 patch | Review feedback |
| **GENET Stub** | 1 patch | Part of peripheral block |
| **GENET Register Definitions** | 4 patches | Large, needed review |
| **GENET Register Operations** | 1 patch | Depended on above |
| **GENET MDIO Interface** | 1 patch | Depended on above |
| **GENET TX Path** | 1 patch | Depended on above |
| **GENET RX Path** | 1 patch | Depended on above |
| **GENET Enable/Integration** | 1 patch | Depended on above |
| **Test Suite** | ~12 patches | Portability issues (big-endian, macOS) |

**Key reviewer feedback**:
- Register macros needed to use QEMU's `REG32`/`FIELD` infrastructure
- Big-endian handling needed improvement
- qtest patches had portability issues (macOS, big-endian hosts)
- Some code style issues

### 4.4 No v7+ Resubmission Found

As of 2026-04-06, **no v7 or later resubmission** has been found on the QEMU mailing list. The GENET patch series appears to have stalled after v6 in February 2024.

### 4.5 Key Patch URLs

- Cover letter (v6): https://patchew.org/QEMU/20240226000259.2752893-1-sergey.kambalin@auriga.com/
- GENET enable patch (v4): https://patchwork.kernel.org/project/qemu-devel/patch/20231203212905.1364036-33-sergey.kambalin@auriga.com/
- GENET stub (v5): https://www.mail-archive.com/qemu-devel@nongnu.org/msg1023621.html
- GENET RX path (v1): https://lore.kernel.org/qemu-devel/20230726132512.149618-32-sergey.kambalin@auriga.com/

---

## 5. Other Community Efforts

### 5.1 Philippe Mathieu-Daude's `raspi4_wip` Branch

Philippe Mathieu-Daude (QEMU ARM maintainer) had a `raspi4_wip` branch at `gitlab.com/philmd/qemu` which is now 404/deleted. This was likely the staging area for reviewing the Kambalin series.

### 5.2 Alex Bennee's Early RPi4 WIP (2021)

An earlier 7-patch WIP series by Alex Bennee (Linaro) was posted in October 2021 but was superseded by Kambalin's more comprehensive work.

### 5.3 Workarounds Currently Used

Users working around the lack of network use:
1. **Manual USB-net attachment**: `-device usb-net,netdev=net0` (unreliable, doesn't match real hardware)
2. **`virt` machine type instead of `raspi4b`**: Sacrifices hardware fidelity for virtio-net support
3. **Host-forwarded ports via user-mode networking**: Limited, no incoming connections

---

## 6. Architecture: How to Add GENET to QEMU

Based on the existing code patterns and the unmerged patch series, adding GENET would involve:

### 6.1 New Files Required

```
hw/net/bcm2838_genet.c          -- Device implementation (~1500-2500 lines)
include/hw/net/bcm2838_genet.h  -- Device state and register definitions
```

### 6.2 Modified Files

```
hw/arm/bcm2838_peripherals.c           -- Add GENET init/realize/wiring
include/hw/arm/bcm2838_peripherals.h   -- Add BCM2838GenetState field
hw/arm/raspi4b.c                       -- Remove "brcm,bcm2711-genet-v5" from disabled list
hw/misc/bcm2835_property.c             -- Connect MAC address (line 548 TODO)
hw/net/meson.build                     -- Add bcm2838_genet.c
docs/system/arm/raspi.rst              -- Move GENET from missing to implemented
```

### 6.3 Implementation Blocks

1. **Register I/O** -- Memory-mapped register read/write handlers for SYS, EXT, UMAC, INTRL2, RBUF, TBUF, HFB blocks
2. **MDIO/PHY** -- Management interface for BCM54213PE PHY (link status, speed negotiation)
3. **TX DMA** -- Descriptor ring processing, packet transmission via QEMU `NetClientState`
4. **RX DMA** -- Packet reception, descriptor ring filling, interrupt generation
5. **Interrupt Controller** -- INTRL2 with mask/status/clear for both default and priority IRQ lines
6. **Integration** -- Wire into BCM2838 peripheral bus, connect IRQs to GIC SPI 157/158

### 6.4 Reference Implementation

The Cadence GEM (`hw/net/cadence_gem.c`) is the closest architectural match:
- DMA descriptor ring-based TX/RX
- MDIO PHY management
- Memory-mapped MAC registers
- Interrupt generation
- Similar line count

---

## 7. Summary and Recommendations

### Current State

| Aspect | RPi 0/1 | RPi 2/3 | RPi 4 |
|---|---|---|---|
| CPU | Good | Good | Good |
| Boot | Works | Works | Works |
| Serial | Works | Works | Works |
| SD Card | Works | Works | Works |
| USB | Host-only | Host-only | **Missing (no XHCI/PCIe)** |
| **Network** | **N/A** | **Missing** | **Missing (no GENET)** |
| GPIO/I2C/SPI | Works | Works | Works |
| Interrupts | Works | Works | Works (GICv2) |
| Display | Works | Works | Works |

### Critical Gaps (Priority Order)

1. **GENET Ethernet Controller** -- RPi4 has no network. Complete implementation exists in unmerged v6 patch series but needs rework per reviewer feedback.
2. **PCIe Root Port** -- Blocks XHCI USB3 and potentially other PCIe devices on RPi4. Also in unmerged v6 series.
3. **USB Network for RPi3** -- Could be a simpler win by auto-attaching a USB-net device in machine init.
4. **RNG200** -- Modern RNG for RPi4 (the legacy BCM2835 RNG still works).
5. **DWC2 Device/OTG Mode** -- Only host mode works; gadget mode would be useful.

### Recommended Approach

1. **Revive the Kambalin GENET patches** -- The v6 series contains a complete implementation. It needs:
   - Reworking register definitions to use QEMU's `REG32`/`FIELD` macros
   - Fixing big-endian handling
   - Addressing reviewer style feedback
   - Rebasing onto current QEMU master (v11.x)

2. **Quick win for RPi3 networking** -- Auto-attach a USB-net device to the DWC2 controller in `raspi.c` machine init, matching how `vexpress.c` attaches LAN9118.

3. **Consider alternative for RPi4** -- If GENET is too complex, the PCIe root port + a standard virtio-net-pci device could provide network access without full GENET fidelity.

---

## Appendix A: File Index

### Board/SoC Definitions
| File | Purpose |
|---|---|
| `hw/arm/raspi.c` | RPi 0/1/2/3 machine definitions |
| `hw/arm/raspi4b.c` | RPi 4B machine definition |
| `hw/arm/bcm2836.c` | BCM2835/2836/2837 SoC |
| `hw/arm/bcm2838.c` | BCM2838 SoC |
| `hw/arm/bcm2835_peripherals.c` | Shared peripheral wiring |
| `hw/arm/bcm2838_peripherals.c` | BCM2838-specific peripherals |
| `include/hw/arm/raspi_platform.h` | Platform offsets, IRQ numbers, clock rates |
| `include/hw/arm/bcm2835_peripherals.h` | Base peripheral state |
| `include/hw/arm/bcm2838_peripherals.h` | BCM2838 peripheral state, GIC SPI numbers |
| `docs/system/arm/raspi.rst` | Official documentation |

### Peripheral Device Implementations
| File | Device |
|---|---|
| `hw/intc/bcm2835_ic.c` | GPU interrupt controller |
| `hw/intc/bcm2836_control.c` | Per-core IRQ/mailbox routing |
| `hw/dma/bcm2835_dma.c` | DMA controller (16 channels) |
| `hw/timer/bcm2835_systmr.c` | System timer |
| `hw/gpio/bcm2835_gpio.c` | GPIO controller |
| `hw/gpio/bcm2838_gpio.c` | BCM2838 GPIO (enhanced) |
| `hw/char/bcm2835_aux.c` | Mini UART / AUX |
| `hw/misc/bcm2835_mbox.c` | Mailbox controller |
| `hw/misc/bcm2835_property.c` | Firmware property channel |
| `hw/misc/bcm2835_thermal.c` | Thermal sensor (stub) |
| `hw/misc/bcm2835_powermgt.c` | Power management / watchdog |
| `hw/misc/bcm2835_cprman.c` | Clock manager |
| `hw/misc/bcm2835_rng.c` | Random number generator |
| `hw/misc/bcm2835_mphi.c` | MPHI (USB FIQ support) |
| `hw/nvram/bcm2835_otp.c` | OTP memory (stub) |
| `hw/display/bcm2835_fb.c` | Framebuffer |
| `hw/sd/bcm2835_sdhost.c` | SD host controller |
| `hw/ssi/bcm2835_spi.c` | SPI master |
| `hw/i2c/bcm2835_i2c.c` | I2C controllers |
| `hw/usb/hcd-dwc2.c` | DWC2 USB host controller |

### Relevant Network Device References
| File | Device | Lines | Relevance |
|---|---|---|---|
| `hw/net/cadence_gem.c` | Cadence GEM | 1860 | Closest architectural match to GENET |
| `hw/net/lan9118.c` | SMSC LAN9118 | 1350 | Memory-mapped MAC pattern |
| `hw/net/ftgmac100.c` | Faraday FTGMAC100 | 1452 | DMA ring GbE reference |
| `hw/usb/dev-network.c` | USB CDC/RNDIS | -- | Potential RPi3 quick-win |
