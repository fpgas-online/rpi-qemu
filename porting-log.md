# Patch Application Log

**QEMU Version**: v11.0.0-rc2
**Date**: 2026-04-06
**Branch**: raspi4b-genet-port

## Results

| # | Patch | Status | Details |
|---|---|---|---|
| 1 | BCM2838 PCIe Root Complex | CONFLICT | error: patch failed: hw/arm/meson.build:30 error: hw/arm/meson.build: patch does not apply |
| 2 | BCM2838 PCIe Host | CONFLICT | error: hw/arm/bcm2838_pcie.c: No such file or directory error: include/hw/arm/bcm2838_pcie.h: No such file or directory |
| 3 | Enable BCM2838 PCIe | CONFLICT | error: patch failed: hw/arm/bcm2838_peripherals.c:12 error: hw/arm/bcm2838_peripherals.c: patch does not apply error: patch failed: hw/arm/meson.build:32 error: hw/arm/meson.build: patch does not apply |
| 4 | RPi4 RNG200 | CONFLICT | error: patch failed: hw/arm/bcm2838_peripherals.c:34 error: hw/arm/bcm2838_peripherals.c: patch does not apply error: patch failed: hw/arm/raspi4b.c:67 error: hw/arm/raspi4b.c: patch does not apply error: patch failed: hw/misc/trace-events:341 |
| 5 | BCM2838 Thermal Sensor | CONFLICT | error: patch failed: hw/arm/bcm2838_peripherals.c:37 error: hw/arm/bcm2838_peripherals.c: patch does not apply error: patch failed: hw/arm/raspi4b.c:67 error: hw/arm/raspi4b.c: patch does not apply error: patch failed: hw/misc/meson.build:93 |
| 6 | Clock ISP Stub | CONFLICT | error: patch failed: hw/arm/bcm2838_peripherals.c:17 error: hw/arm/bcm2838_peripherals.c: patch does not apply error: patch failed: include/hw/arm/bcm2838_peripherals.h:77 error: include/hw/arm/bcm2838_peripherals.h: patch does not apply |
| 7 | GENET Stub | CONFLICT | error: hw/misc/bcm2838_thermal.c: No such file or directory error: patch failed: hw/net/meson.build:70 error: hw/net/meson.build: patch does not apply error: patch failed: hw/net/trace-events:513 error: hw/net/trace-events: patch does not apply |
| 8 | GENET Register Structs Part 1 | CONFLICT | error: hw/net/bcm2838_genet.c: No such file or directory error: include/hw/net/bcm2838_genet.h: No such file or directory |
| 9 | GENET Register Structs Part 2 | CONFLICT | error: hw/net/bcm2838_genet.c: No such file or directory error: include/hw/net/bcm2838_genet.h: No such file or directory |
| 10 | GENET Register Structs Part 3 | CONFLICT | error: hw/net/bcm2838_genet.c: No such file or directory error: include/hw/net/bcm2838_genet.h: No such file or directory |
| 11 | GENET Register Structs Part 4 | CONFLICT | error: include/hw/net/bcm2838_genet.h: No such file or directory |
| 12 | GENET Register Access Macros | CONFLICT | error: include/hw/net/bcm2838_genet.h: No such file or directory |
| 13 | GENET Register Ops | CONFLICT | error: hw/net/bcm2838_genet.c: No such file or directory error: include/hw/net/bcm2838_genet.h: No such file or directory |
| 14 | GENET MDIO | CONFLICT | error: hw/net/bcm2838_genet.c: No such file or directory error: include/hw/net/bcm2838_genet.h: No such file or directory |
| 15 | GENET TX Path | CONFLICT | error: hw/net/bcm2838_genet.c: No such file or directory error: include/hw/net/bcm2838_genet.h: No such file or directory |
| 16 | GENET RX Path | CONFLICT | error: hw/net/bcm2838_genet.c: No such file or directory error: include/hw/net/bcm2838_genet.h: No such file or directory |
| 17 | Enable BCM2838 GENET | CONFLICT | error: patch failed: hw/arm/bcm2838.c:239 error: hw/arm/bcm2838.c: patch does not apply error: patch failed: hw/arm/bcm2838_peripherals.c:47 error: hw/arm/bcm2838_peripherals.c: patch does not apply error: patch failed: hw/arm/raspi4b.c:63 |

## Summary

- **Applied cleanly**: 0/17
- **Failed**: 17/17
- **Skipped** (due to prior failure): 0/17

Each patch was tested independently (dry-run) against the unmodified QEMU v11 tree.

## Conflict Details

The patches were written against QEMU ~v9.0 (Feb 2024). QEMU v11.0.0-rc2 has significant API changes including:

1. `class_init()` signature changed from `void *data` to `const void *data`
2. `dc->reset` replaced by `device_class_set_legacy_reset()`
3. `DEFINE_PROP_END_OF_LIST()` removed
4. `Property` arrays now `const`
5. Include paths moved: `sysemu/dma.h` -> `system/dma.h`, `hw/sysbus.h` -> `hw/core/sysbus.h`

Additionally, existing files modified by the patches (bcm2838_peripherals.c/h, meson.build, etc.) have diverged.

