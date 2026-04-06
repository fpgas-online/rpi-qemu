# Downloaded Resources Index

All resources relevant to improving Raspberry Pi hardware emulation in QEMU,
particularly the GENET Ethernet controller for the RPi 4B.

**Downloaded**: 2026-04-06
**QEMU Version**: v11.0.0-rc2
**Total**: 52 files, ~4.8 MB

---

## Datasheets (`datasheets/`)

| File | Description | Source |
|---|---|---|
| `bcm2711-peripherals.pdf` | BCM2711 ARM Peripherals reference (1.3 MB) | datasheets.raspberrypi.com |
| `bcm2835-peripherals.pdf` | BCM2835 ARM Peripherals reference (1.5 MB) | datasheets.raspberrypi.com |
| `rpi4-product-brief.pdf` | Raspberry Pi 4 Model B product brief | datasheets.raspberrypi.com |
| `rpi4-reduced-schematics.pdf` | RPi 4B reduced schematics (shows GENET/PHY wiring) | datasheets.raspberrypi.com |
| `rpi4-mechanical-drawing.pdf` | RPi 4B mechanical drawing | datasheets.raspberrypi.com |

**Note**: No public Broadcom datasheet exists for the GENET controller itself. The register interface must be reverse-engineered from driver source code.

---

## Linux Kernel GENET Driver (`linux-genet-driver/`)

Source: `github.com/torvalds/linux` @ `master`, `drivers/net/ethernet/broadcom/genet/`

| File | Description | Lines |
|---|---|---|
| `bcmgenet.h` | Main header -- register definitions, struct layouts, constants | ~650 |
| `bcmgenet.c` | Main driver -- init, DMA, TX/RX, interrupt handling | ~4000 |
| `bcmmii.c` | MII/MDIO/PHY management -- link setup, speed negotiation | ~500 |
| `bcmgenet_wol.c` | Wake-on-LAN support | ~200 |
| `Makefile` | Build rules | 4 |

This is the **authoritative reference** for register layouts, DMA descriptor formats, and hardware behavior. The header file `bcmgenet.h` contains all register offset definitions.

---

## U-Boot GENET Driver (`uboot-genet-driver/`)

Source: `github.com/u-boot/u-boot` @ `master`

| File | Description |
|---|---|
| `bcmgenet.c` | Single-file GENET driver (~600 lines). Much simpler than Linux -- single TX+RX ring, no multi-queue. |
| `bcm2711-uboot.dtsi` | U-Boot's copy of the BCM2711 device tree include |

The U-Boot driver is the **recommended starting point** for understanding minimal GENET operation. It shows the essential register sequence for link-up, TX, and RX without the complexity of the full Linux driver.

---

## Device Tree Sources (`device-tree/`)

Source: `github.com/raspberrypi/linux` @ `rpi-6.6.y`, `arch/arm/boot/dts/broadcom/`

| File | Description |
|---|---|
| `bcm2711.dtsi` | BCM2711 SoC -- defines GENET node, interrupt mapping, all peripherals |
| `bcm2711-rpi-4-b.dts` | RPi 4B board -- references bcm2711.dtsi, adds board-specific config |
| `bcm2711-rpi-cm4.dts` | Compute Module 4 board definition |
| `bcm2711-rpi.dtsi` | Shared RPi-specific BCM2711 includes |
| `bcm283x.dtsi` | Base BCM283x SoC shared definitions |
| `bcm2835-common.dtsi` | BCM2835 common peripheral definitions |
| `bcm270x.dtsi` | RPi Foundation overlay base |
| `bcm283x-rpi-led-deprecated.dtsi` | LED binding (minor) |
| `bcm283x-rpi-wifi-bt.dtsi` | WiFi/BT binding (minor) |

The key file is `bcm2711.dtsi` which defines the GENET node at address `0x7d580000` with interrupts GIC SPI 157 and 158.

---

## Kambalin v6 GENET Patches (`patches/kambalin-v6-genet/`)

Source: `patchwork.kernel.org`, series by Sergey Kambalin (Auriga), 2024-02-26

These are the **unmerged GENET patches** from the v6 RPi 4B series. Together they form a complete GENET implementation (~1088 lines of `hw/net/bcm2838_genet.c` + ~426 lines of header).

| File | Patch | Description |
|---|---|---|
| `00-cover-letter.mbox` | v6 0/41 | Series overview -- 41 patches, lists all components |
| `19-genet-stub.mbox` | v6 19/41 | Initial GENET device stub -- SysBus device, memory region, basic r/w |
| `20-genet-regs-part1.mbox` | v6 20/41 | SYS, GR_BRIDGE, EXT, INTRL, RBUF, TBUF register structs |
| `21-genet-regs-part2.mbox` | v6 21/41 | UMAC register struct (MAC core) |
| `22-genet-regs-part3.mbox` | v6 22/41 | RDMA/TDMA descriptor and ring register structs |
| `23-genet-regs-part4.mbox` | v6 23/41 | PHY register structs (MDIO, shadow regs) |
| `24-genet-register-macros.mbox` | v6 24/41 | REG32/FIELD register access macros |
| `25-genet-register-ops.mbox` | v6 25/41 | Read/write handlers, interrupt logic, MAC address handling, reset |
| `26-genet-mdio.mbox` | v6 26/41 | MDIO/PHY management -- BCM54213PE emulation |
| `27-genet-tx-path.mbox` | v6 27/41 | TX DMA -- descriptor ring processing, packet transmission |
| `28-genet-rx-path.mbox` | v6 28/41 | RX DMA -- packet reception, descriptor filling, interrupts |
| `29-enable-genet.mbox` | v6 29/41 | Integration -- wire into BCM2838 peripherals, remove DTB disabling |

**Status**: NOT merged into QEMU mainline. Review feedback required:
1. Endianness: `memcpy()` in read/write needs `ldn_he_p()`/`stn_he_p()`
2. Some register macro style issues
3. qtest portability (big-endian hosts, macOS)

---

## Kambalin v6 Other Unmerged Patches (`patches/kambalin-v6-other/`)

| File | Patch | Description |
|---|---|---|
| `13-pcie-root-complex.mbox` | v6 13/41 | BCM2838 PCIe Root Complex device |
| `14-pcie-host.mbox` | v6 14/41 | BCM2838 PCIe Host Bridge |
| `15-pcie-host-pci.mbox` | v6 15/41 | Enable BCM2838 PCIe |
| `16-rng200.mbox` | v6 16/41 | BCM2838 RNG200 hardware RNG |
| `17-thermal-sensor.mbox` | v6 17/41 | BCM2838 Thermal Sensor |
| `18-clock-stub.mbox` | v6 18/41 | Clock ISP stub |

---

## Reference Material (`reference/`)

### External Documentation

| File | Description | Source |
|---|---|---|
| `brcm-bcmgenet-binding.yaml` | Official Linux DT binding for GENET | kernel.org |
| `ultibo-genet-unit.md` | Ultibo Pascal GENET register definitions | ultibo.org |
| `freebsd-genet-manpage.md` | FreeBSD GENET driver man page | freebsd.org |
| `rpi-forum-genet-pitfalls.md` | RPi Forum: GENET hardware pitfalls | forums.raspberrypi.com |
| `qemu-issue-2547-rpi4-ethernet.md` | QEMU GitLab issue #2547 (RPi4 Ethernet) | gitlab.com/qemu-project |

### QEMU Source Snapshots (v11.0.0-rc2)

These are copies of key QEMU source files for offline reference:

| File | Original Path | Purpose |
|---|---|---|
| `qemu-raspi4b.c` | `hw/arm/raspi4b.c` | RPi 4B machine -- DTB disabling code |
| `qemu-bcm2838-peripherals.c` | `hw/arm/bcm2838_peripherals.c` | Where GENET would be wired |
| `qemu-bcm2838-peripherals.h` | `include/hw/arm/bcm2838_peripherals.h` | BCM2838 state struct, GIC SPI numbers |
| `qemu-raspi-platform.h` | `include/hw/arm/raspi_platform.h` | Peripheral offsets, IRQ numbers |
| `qemu-bcm2835-property.c` | `hw/misc/bcm2835_property.c` | Property channel (has MAC TODO) |
| `qemu-cadence-gem.c` | `hw/net/cadence_gem.c` | Reference: closest NIC implementation to GENET |
| `qemu-cadence-gem.h` | `include/hw/net/cadence_gem.h` | Reference: Cadence GEM header |
| `qemu-raspi-docs.rst` | `docs/system/arm/raspi.rst` | Official QEMU RPi documentation |
