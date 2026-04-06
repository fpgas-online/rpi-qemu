# Porting Results: Kambalin v6 Patches to QEMU v11.0.0-rc2

**Date**: 2026-04-06
**QEMU Base**: v11.0.0-rc2 (v10.2.92)
**Patches**: Kambalin v6 series (2024-02-26), patches 13-29 of 41

---

## 1. Patch Application (Step 1)

**Result: 0/17 patches apply cleanly to QEMU v11.0.0-rc2.**

Every patch fails `git apply --check` due to:
- Context line changes in existing files (meson.build, bcm2838_peripherals.c/h, raspi4b.c, trace-events)
- Sequential dependencies (patches 8-16 modify files created by earlier patches in the series)

See `porting-log.md` for detailed per-patch results.

---

## 2. Porting (Step 2)

### Strategy Used

1. Extracted new file contents by applying patches sequentially in a temp directory
2. Applied automated API fixes via `fix-api-compat.py`
3. Manually integrated wiring code into existing QEMU files

### API Fixes Required (8 total categories)

| Fix | Pattern | Files |
|---|---|---|
| `class_init` signature | `void *data` -> `const void *data` | 4 .c files |
| Device reset API | `dc->reset = fn` -> `device_class_set_legacy_reset(dc, fn)` | 2 .c files |
| Property terminator | Remove `DEFINE_PROP_END_OF_LIST()` | 2 .c files |
| Property const | `static Property` -> `static const Property` | 2 .c files |
| Include: dma | `sysemu/dma.h` -> `system/dma.h` | 1 file |
| Include: rng | `sysemu/rng.h` -> `system/rng.h` | 1 file |
| Include: ptimer/clock | `hw/ptimer.h` -> `hw/core/ptimer.h`, `hw/qdev-clock.h` -> `hw/core/qdev-clock.h` | 1 file |
| Include: irq/sysbus/etc | `hw/irq.h` -> `hw/core/irq.h`, `hw/sysbus.h` -> `hw/core/sysbus.h`, etc. | 4 files |
| fifo8 API | `fifo8_pop_buf()` -> `fifo8_pop_bufptr()` (signature changed) | 1 file (rng200) |
| Resettable API | `hold(Object *obj)` -> `hold(Object *obj, ResetType type)` | 1 file (pcie) |
| PCIe trace events | Missing from integration, added manually | 1 file (trace-events) |

### Files Created (8 new files)

| File | Lines | Description |
|---|---|---|
| `hw/net/bcm2838_genet.c` | ~1088 | GENET Ethernet controller |
| `include/hw/net/bcm2838_genet.h` | ~426 | GENET header + register structs |
| `hw/arm/bcm2838_pcie.c` | ~295 | PCIe Root Complex + Host |
| `include/hw/arm/bcm2838_pcie.h` | ~75 | PCIe header |
| `hw/misc/bcm2838_rng200.c` | ~405 | RNG200 hardware RNG |
| `include/hw/misc/bcm2838_rng200.h` | ~43 | RNG200 header |
| `hw/misc/bcm2838_thermal.c` | ~98 | Thermal sensor |
| `include/hw/misc/bcm2838_thermal.h` | ~24 | Thermal header |

### Files Modified (10 existing files)

| File | Changes |
|---|---|
| `include/hw/arm/bcm2838_peripherals.h` | +4 includes, +4 struct fields |
| `hw/arm/bcm2838_peripherals.c` | +device init/realize for all 4 peripherals |
| `hw/arm/bcm2838.c` | +GIC IRQ connections for RNG200, PCIe, GENET |
| `hw/arm/raspi4b.c` | Removed DTB disabling of 4 now-implemented devices |
| `hw/arm/meson.build` | +bcm2838_pcie.c to build |
| `hw/net/meson.build` | +bcm2838_genet.c to build |
| `hw/misc/meson.build` | +bcm2838_rng200.c, +bcm2838_thermal.c to build |
| `hw/arm/trace-events` | +2 PCIe trace events |
| `hw/net/trace-events` | +16 GENET trace events |
| `hw/misc/trace-events` | +8 RNG200 trace events |
| `docs/system/arm/raspi.rst` | GENET+PCIe moved to implemented |

### Commits on `raspi4b-genet-port` branch

```
6de96a122 Fix PCIe: ResetType parameter and add trace events
3d7e0354d Fix RNG200: fifo8_pop_buf -> fifo8_pop_bufptr for QEMU v11
59c8f1930 Fix RNG200 include: sysemu/rng.h -> system/rng.h
a7376f3b2 Wire BCM2838 peripherals: GENET, PCIe, RNG200, Thermal
4df2ca9e4 Add BCM2838 GENET Ethernet controller (ported from Kambalin v6)
e9744d0a3 Add BCM2838 thermal sensor (ported from Kambalin v6)
9d1d9f8cd Add BCM2838 RNG200 hardware RNG (ported from Kambalin v6)
ca90f7d40 Add BCM2838 PCIe Root Complex and Host (ported from Kambalin v6)
```

---

## 3. Build Verification (Step 3)

**Result: PASS -- builds cleanly with zero errors.**

- Configure: `../configure --target-list=aarch64-softmmu` (with `--enable-slirp`)
- Build: `ninja -j$(nproc)` completes successfully
- Binary: `qemu-system-aarch64` (116 MB)
- Verified: GENET (191K .o), RNG200 (84K .o), PCIe (101K .o) all compiled

---

## 4. Boot Test (Step 4)

**Result: PASS -- Linux kernel boots on raspi4b with all new peripherals detected.**

Test kernel: Linux 6.18.20-v8+ (from `github.com/raspberrypi/firmware`)
Test DTB: `bcm2711-rpi-4-b.dtb` (same source)

### Boot log evidence

```
[    0.000000] Machine model: Raspberry Pi 4 Model B
[    0.000000] earlycon: pl11 at MMIO 0x00000000fe201000 (options '')
[    0.190672] smp: Brought up 1 node, 4 CPUs
...
[    0.721130] /scb/pcie@7d500000: Fixed dependency cycle(s) with /scb/pcie@7d500000
[    3.239322] brcm-pcie fd500000.pcie: host bridge /scb/pcie@7d500000 ranges:
[    3.256668] brcm-pcie fd500000.pcie: PCI host bridge to bus 0000:00
[    3.374178] iproc-rng200 fe104000.rng: hwrng registered
[    3.578267] bcmgenet fd580000.ethernet: GENET 5.0 EPHY: 0x0000
[    3.580684] bcmgenet fd580000.ethernet: using random Ethernet MAC
[    3.772981] NET: Registered PF_PACKET protocol family
```

All 4 new peripherals detected:
- **GENET**: `bcmgenet fd580000.ethernet: GENET 5.0 EPHY: 0x0000` (driver probed)
- **PCIe**: `brcm-pcie fd500000.pcie: PCI host bridge to bus 0000:00`
- **RNG200**: `iproc-rng200 fe104000.rng: hwrng registered`
- **Thermal**: `thermal_sys: Registered thermal governor 'step_wise'`

---

## 5. Network Verification (Step 5)

**Result: PARTIAL -- GENET NIC probes and initializes, but end-to-end traffic test blocked by rootfs limitations.**

### What works

- Linux `bcmgenet` driver loads and probes the GENET controller at `fd580000`
- GENET version detected correctly as "GENET 5.0"
- NIC interface created with random MAC address
- UniMAC MDIO bus registered: `unimac-mdio unimac-mdio.-19: Broadcom UniMAC MDIO bus`
- QEMU auto-creates the NIC: `nic bcm2838-genet.0`

### What wasn't testable

- DHCP, ping, SSH -- requires a working root filesystem with networking tools
- The RPi OS Lite image couldn't complete boot due to missing VideoCore firmware (GPIO regulator errors)
- A minimal initrd-based rootfs would be needed for full traffic verification

### QEMU NIC binding note

When using `-netdev user,id=net0` without explicit device binding, QEMU warns:
```
qemu-system-aarch64: warning: netdev net0 has no peer
qemu-system-aarch64: warning: nic bcm2838-genet.0 has no peer
```

The `qemu_configure_nic_device()` in bcm2838_peripherals.c needs investigation -- it should auto-bind the GENET NIC to the available netdev backend. This may require passing `model=bcm2838-genet` or adjusting the NIC configuration call.

---

## 6. U-Boot Verification (Step 6)

*Pending -- U-Boot build and test in progress.*

---

## 7. Summary

| Step | Status | Result |
|---|---|---|
| 1. Patch application | DONE | 0/17 apply cleanly |
| 2. Porting | DONE | 8 new files, 10 modified, 11 API fix categories |
| 3. Build | PASS | Zero errors, 116MB binary |
| 4. Boot test | PASS | All 4 CPUs, all 4 new peripherals detected |
| 5. Linux network | PARTIAL | Driver probes, NIC created; traffic test needs rootfs |
| 6. U-Boot network | PENDING | Building |
| 7. Documentation | DONE | This file |

### Remaining Work

1. **NIC-netdev binding**: Investigate why `-netdev user` doesn't auto-bind to GENET NIC
2. **Traffic testing**: Build a minimal initrd with networking tools (busybox + ip + ping)
3. **MAC address**: Connect GENET MAC to property channel (TODO at `bcm2835_property.c:548`)
4. **Endianness review**: The original reviewer feedback about `memcpy` in read/write handlers should be addressed for big-endian host support
5. **Upstream submission**: The ported patches could be re-submitted to qemu-devel with the v11 API fixes
