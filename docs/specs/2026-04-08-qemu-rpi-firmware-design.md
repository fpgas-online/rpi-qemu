# Design: `qemu-rpi-pxeboot` Package

**Date:** 2026-04-08
**Status:** Draft (v2 -- post review)

## Problem

QEMU's raspi4b machine emulates ARM hardware but not the VideoCore GPU bootloader. On a real Raspberry Pi 4B, the SPI EEPROM bootloader performs a specific sequence of TFTP requests (DHCP, config.txt, kernel, DTB, etc.) during PXE network boot. QEMU has no equivalent -- users must manually orchestrate the boot process.

This gap prevents realistic testing of PXE/TFTP server configurations designed for real Raspberry Pi hardware.

## Goal

A Debian package (`qemu-rpi-pxeboot`) that, when loaded into QEMU raspi4b, transparently emulates the VideoCore bootloader's PXE behaviour. Users point QEMU at a TFTP root laid out for real Pi hardware and the emulator just boots -- same DHCP options, same TFTP probe sequence, same `config.txt` parsing.

## Scope

**Pi 4B only.** Pi 3B/3B+ variants are deferred because QEMU's `raspi3b` machine has no Ethernet emulation (real Pi 3 uses USB-attached LAN7515/SMSC95xx which QEMU does not implement). The GENET patches only apply to the BCM2838 (Pi 4B).

## Non-goals

- Emulating the VideoCore GPU itself (start4.elf execution, HDMI init, memory split)
- Sub-second timing fidelity of TFTP request spacing
- OTP-derived serial numbers (uses a configurable placeholder)
- Pi 3B/3B+ support (no QEMU Ethernet for those boards)

## Usage

```bash
qemu-rpi-system-aarch64 -M raspi4b \
  -kernel /usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.bin \
  -dtb /usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.dtb \
  -nic user,tftp=/srv/tftpboot \
  -serial stdio -display none
```

With optional serial number override:

```bash
qemu-rpi-system-aarch64 -M raspi4b \
  -kernel /usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.bin \
  -dtb /usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.dtb \
  -nic user,tftp=/srv/tftpboot \
  -fw_cfg name=opt/pi_serial,string=f393a191 \
  -serial stdio -display none
```

The TFTP root (`/srv/tftpboot`) is laid out the way it would be for a real Pi 4B:

```
/srv/tftpboot/
  <serial>/
    config.txt
    cmdline.txt
    kernel8.img
    bcm2711-rpi-4-b.dtb
    start4.elf          (fetched but not executed)
    fixup4.dat          (fetched but not executed)
```

## Design

### Architecture

```
QEMU raspi4b
  │
  ├── -kernel rpi4b-pxeboot.bin       ← from qemu-rpi-pxeboot package
  ├── -dtb rpi4b-pxeboot.dtb          ← from qemu-rpi-pxeboot package
  └── -nic user,tftp=/srv/tftpboot
        │
        └── Firmware boots, emulates VideoCore:
              1. Print VC-style boot progress on UART
              2. DHCP with PXEClient vendor class
              3. Probe start4.elf, config.txt, fixup4.dat, ...
              4. Parse config.txt for kernel= / device_tree=
              5. Load cmdline.txt → set bootargs
              6. Load kernel + DTB + optional initrd
              7. booti
```

### Implementation: U-Boot with embedded environment

The firmware is a U-Boot binary built with a custom defconfig. The VideoCore emulation script is embedded using U-Boot's `.env` file mechanism (`board/raspberrypi/rpi/rpi.env` override or `CONFIG_DEFAULT_ENV_TEXT_FILE`), which is the correct approach for U-Boot 2026.04-rc5.

**Not** `CONFIG_EXTRA_ENV_SETTINGS` (which is a legacy C preprocessor mechanism no longer used by the RPi board code).

Defconfig: `rpi_4_qemu_pxeboot_defconfig`

| Setting | Value | Why |
|---------|-------|-----|
| `CONFIG_BOOTDELAY` | `-2` | Boot immediately, no abort check |
| `CONFIG_BOOTCOMMAND` | `"run vc_boot"` | Run the VideoCore emulation script |
| `CONFIG_DEFAULT_ENV_TEXT_FILE` | `"ci/vc-boot-env.txt"` | External file with full VC script |
| `CONFIG_PCI` | `n` | Avoid PCIe timeout delays |
| `CONFIG_EFI_LOADER` | `n` | Avoid EFI overhead |
| `CONFIG_USB` | `n` | No USB in QEMU raspi4b |
| `CONFIG_VIDEO` | `n` | Serial-only output |
| `CONFIG_SYS_DEVICE_NULLDEV` | `y` | Required for silent console |

Cross-compiler: `aarch64-linux-gnu-` (Pi 4B is AArch64).

### Memory layout

U-Boot's default `CFG_SYS_SDRAM_SIZE` for RPi is 128 MB (conservative for real hardware with VideoCore memory split). For QEMU where there is no VideoCore, the defconfig overrides this to use the full available RAM (960 MB for raspi4b default).

| Address | Use |
|---------|-----|
| `0x02000000` | Scratch buffer for speculative TFTP probes |
| `0x10000000` | Kernel load address |
| `0x0f000000` | DTB load address |
| `0x12000000` | Initrd load address |

### Serial number configuration

Default: `deadbeef`. Override via QEMU's `-fw_cfg` mechanism:

```
-fw_cfg name=opt/pi_serial,string=f393a191
```

The boot script reads this via U-Boot's `qfw` command: `qfw load opt/pi_serial`. This is more reliable than parsing `-append` (which sets DTB `/chosen/bootargs` and doesn't auto-parse into individual variables).

If `-fw_cfg` is not provided, the default serial from the environment is used.

### UART boot progress messages

Before the console is silenced for Linux, the firmware prints VideoCore-style progress text using explicit `echo` commands:

```
Raspberry Pi Bootloader
Board: BCM2711
NET:   Ethernet

BOOTP broadcast 1
DHCP client bound to 10.0.2.15

Loading start4.elf ... not found
Loading config.txt ... OK
Loading kernel8.img ... OK
Loading bcm2711-rpi-4-b.dtb ... OK

Starting kernel...
```

The console is silenced after the "Starting kernel..." message by setting the `silent` environment variable before `booti`. This way users see VC-like progress but no U-Boot internals.

### Pi 4B TFTP probe sequence

Adapted from the Pi 3B+ reference. Pi 4B has no `bootcode.bin` (SPI EEPROM), uses `start4.elf`/`fixup4.dat`, and `bcm2711-*.dtb`:

1. `<serial>/start4.elf`
2. `<serial>/autoboot.txt`
3. `<serial>/config.txt` (parse: extract `kernel=`, `device_tree=`)
4. `<serial>/recovery.elf`
5. `<serial>/start4.elf` (re-read)
6. `<serial>/fixup4.dat`
7. `<serial>/recovery.elf`
8. `<serial>/config.txt` (re-read, re-parse)
9. `<serial>/dt-blob.bin`
10. `<serial>/recovery.elf`
11. `<serial>/config.txt` (re-read, re-parse)
12. `<serial>/bootcfg.txt`
13. `<serial>/cmdline.txt` → set `bootargs`
14. `<serial>/recovery8.img` → `recovery8-32.img` → `recovery7.img` → `recovery.img` (probe only)
15. `<serial>/kernel8.img` → `kernel8-32.img` → `kernel7l.img` → `kernel7.img` → `kernel.img` (first hit wins, load to kernel address)
16. `<serial>/armstub8.bin` → `armstub8-32.bin` → `armstub7.bin` → `armstub.bin` (probe only)
17. `<serial>/bcm2711-rpi-4-b.dtb` → fallback DTBs (first hit wins, load to DTB address)

### config.txt parsing

**Limited key extraction, not wholesale import.** Real `config.txt` files contain conditional sections (`[pi4]`, `[all]`), `dtoverlay=` directives, and other syntax incompatible with U-Boot's `env import -t`. The firmware extracts only these keys using line-by-line grep:

- `kernel=<name>` → overrides which kernel file to load
- `device_tree=<name>` → overrides which DTB to load
- `initramfs <name> followkernel` → load initrd after kernel (basic support, fixed addresses only)

Implementation: after loading `config.txt` to scratch, use `setexpr` or shell string operations to extract specific values rather than blindly importing the entire file.

### cmdline.txt handling

After loading `cmdline.txt` from TFTP, the firmware sets `bootargs` from its content. The firmware appends `earlycon=pl011,mmio32,0xfe201000 console=ttyAMA1` if not already present, ensuring serial console works in QEMU.

### DHCP options

The firmware sets these DHCP options to match the VideoCore:

| Option | Value | Notes |
|--------|-------|-------|
| 60 (vendor-class-identifier) | `PXEClient:Arch:00000:UNDI:002001` | Makes dnsmasq tag-match work |
| 93 (client architecture) | `0` (x86PC) | What VC actually sends |

Option 94 (NDI) is **not** emulated because U-Boot has no mechanism to set it. Option 43 gate is **omitted** because QEMU's SLIRP backend does not send option 43 replies, and this is the primary use case.

### Package definition

```
Package: qemu-rpi-pxeboot
Architecture: all
Depends: ${misc:Depends}
Recommends: qemu-rpi-system-arm
Description: PXE network boot emulation for QEMU Raspberry Pi 4B
 Enables QEMU's raspi4b machine to PXE network boot from a
 standard Raspberry Pi TFTP server layout, reproducing the same
 DHCP options, TFTP probe sequence, config.txt parsing, and
 kernel loading behaviour as real Pi 4B hardware.
 .
 Point QEMU at a TFTP root containing your Pi OS files and it
 boots them the same way a physical Raspberry Pi would.
```

Architecture is `all` because the package ships pre-compiled firmware binaries that run inside QEMU, not natively on the host. The aarch64 U-Boot binary is cross-compiled during the CI build.

### Files installed

| File | Description |
|------|-------------|
| `/usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.bin` | PXE boot firmware for QEMU raspi4b |
| `/usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.dtb` | Device tree for raspi4b |
| `/usr/share/doc/qemu-rpi-pxeboot/README` | Usage instructions |

### Relationship to existing packages

- `qemu-rpi-pxeboot` **Recommends** `qemu-rpi-system-arm` (the QEMU binary with GENET patches)
- `qemu-rpi-pxeboot` does **not** replace the existing `rpi_4_qemu_defconfig` / U-Boot build used by `run-rpi-boot-test.py`. That interactive U-Boot build remains for the direct boot test. The pxeboot firmware is a separate, purpose-built binary.

### CI integration

Built and published by the existing `build-qemu-packages.yml` workflow in `fpgas-online/rpi-qemu`. A new `build-pxeboot` job is added alongside the existing `build-debs` and `build-static` jobs:

**Build job (`build-pxeboot`):**
1. Runs in a `debian:trixie` container (same as the QEMU deb build)
2. Installs `gcc-aarch64-linux-gnu` cross-compiler
3. Clones U-Boot at the pinned commit (47e064f1)
4. Copies `ci/rpi_4_qemu_pxeboot_defconfig` and `ci/vc-boot-env.txt` into the U-Boot tree
5. Builds with `make CROSS_COMPILE=aarch64-linux-gnu-`
6. Renames output: `u-boot.bin` → `rpi4b-pxeboot.bin`, DTB → `rpi4b-pxeboot.dtb`
7. Uploads as artifact `qemu-rpi-pxeboot`

**Packaging:** The existing `publish-apt-repo` job is extended to also build `qemu-rpi-pxeboot_*.deb` from the firmware artifacts using the `ci/debian/` packaging (which gains the new binary package stanza). The deb is added to the same APT repo on GitHub Pages at `https://fpgas-online.github.io/rpi-qemu`.

**Release:** The existing `create-release` job is extended to include the pxeboot deb and the raw firmware files (`rpi4b-pxeboot.bin`, `rpi4b-pxeboot.dtb`) in the GitHub Release at `fpgas-online/rpi-qemu`.

**Triggers:** Same as the existing build workflow -- pushes to `main` that change `ci/` files, plus `workflow_dispatch`.

**Caching:** The U-Boot build is cached by the hash of the defconfig + env file. Subsequent runs skip the build if unchanged.

### Testing

A new test script `run-rpi-pxeboot-test.py` that:
1. Sets up a TFTP root with standard RPi layout (config.txt with `kernel=kernel8.img`, kernel, DTB)
2. Boots QEMU with the pxeboot firmware
3. Verifies the VideoCore-style TFTP probe sequence appears in the output
4. Verifies Linux boots and networking works (DHCP + HTTPS fetch)

### Known limitations

- **No start4.elf execution.** Fetched but not executed (no GPU emulation).
- **1-second timing granularity.** U-Boot's `sleep` has 1-second resolution; real VC gaps are sub-second.
- **No DHCP option 43 gate.** Omitted because QEMU SLIRP doesn't send it. With a real DHCP server (tap/bridge networking), this could be re-enabled.
- **No DHCP option 94.** U-Boot has no mechanism to set the NDI option.
- **Limited config.txt.** Only `kernel=`, `device_tree=`, and basic `initramfs` are parsed. Conditional sections (`[pi4]`), `dtoverlay=`, and other directives are ignored.
- **No `followkernel` initramfs.** Only fixed-address initramfs loading. The `followkernel` keyword requires knowing the kernel's loaded size.
- **Pi 3B/3B+ not supported.** QEMU `raspi3b` lacks Ethernet emulation.

## Future work

- Pi 3B/3B+ support when QEMU gains USB Ethernet or alternative NIC emulation
- Richer config.txt parsing (conditional sections, dtparam)
- `followkernel` initramfs support
- Capture a real Pi 4B EEPROM bootloader TFTP trace to verify probe sequence fidelity
