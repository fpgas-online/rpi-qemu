# RPi Emulation Improvement Roadmap for fpgas.online

**Date**: 2026-04-09
**Status**: Design
**Approach**: Bottom-Up Hardware Enablement (validate low-level hardware first, build upward)

## Context

The rpi-qemu project currently emulates RPi 4B with GENET Ethernet (18 QEMU patches). fpgas.online deploys Raspberry Pis to manage FPGA boards at two sites:

- **PS1 (Chicago)**: 8 Arty A7 boards on RPi 3B/3B+/4B, 2 LiteFury on CM4/CM5 Compute Blades
- **Welland (Australia)**: 5 Arty A7 on RPi 4/3B+, 5 NeTV2 on RPi 3B+, 3 Acorn CLE-215+ on RPi 5, 2 Fomu EVT on RPi 3B+, 4 TT FPGA on RPi 4

Key hardware interactions to emulate:
- **USB serial/JTAG**: FTDI FT2232 (`0403:6010`) -- JTAG + UART for Arty A7 boards
- **USB Ethernet**: ASIX AX88179 (`0b95:1790`) -- separate adapter for FPGA board Ethernet
- **Camera**: CSI camera module via `libcamera-vid` streaming to TCP
- **Network**: PXE boot, NFS root, SSH, web services, HLS video streams
- **PCIe**: Acorn CLE-215+ on RPi 5 (via mPCIe HAT)

RPi model breakdown across both sites:
- RPi 3B/3B+: **majority** (all PS1 Arty hosts except one, all Welland NeTV2/Fomu hosts)
- RPi 4B: 3 hosts at Welland, 1 at PS1
- RPi 5: 3 at Welland (Acorn), 1 at PS1 (dev)
- CM4/CM5: 4 at PS1 (Compute Blades)

## Phase 1: USB2 Validation on raspi4b

**Goal**: Prove DWC2 USB host controller works on `raspi4b` with no workarounds.

**Duration**: Days (mostly testing)

### Background

DWC2 is already initialized and wired in QEMU:
- `hw/arm/bcm2835_peripherals.c:154` -- DWC2 initialized as part of base SoC state (shared by ALL raspi machines)
- `hw/arm/bcm2838.c:205-206` -- DWC2 IRQ wired to GIC SPI 73 on raspi4b
- `hw/usb/hcd-dwc2.c` -- complete USB 2.0 host controller (8 channels, DMA transfers, low/full/high speed)

The README currently states "No USB" which appears to refer to the missing xHCI USB3 controller (real RPi 4B routes USB through VL805 over PCIe), not the DWC2 USB2 controller which IS present.

Known DWC2 limitations (`hcd-dwc2.c:738-767`): TX/RX FIFO flush, host/core soft reset are `LOG_UNIMP` stubs. These are non-critical for normal USB host operation.

The stock RPi 4B kernel uses the mainline `dwc2` driver (not the out-of-tree `dwc_otg`), so the `dwc_otg.fiq_fsm_enable=0` workaround mentioned in the DWC2 source header should not be needed.

### Implementation

1. Add `-device usb-kbd,bus=dwc2-usb.0` to existing boot test QEMU command
2. Verify USB device detection in dmesg (mainline `dwc2` driver)
3. If USB enumeration fails, debug the actual DWC2 emulation failure (do not add kernel parameter workarounds)
4. Test `usb-serial` device: `-device usb-serial,chardev=X`
5. Test `usb-net` device: `-device usb-net,netdev=X`
6. Add USB detection checkpoints to test suite
7. Update README to document USB2 support (USB3/xHCI via PCIe is what's missing)

### Verification

- `dmesg | grep -i usb` shows DWC2 controller and attached device enumeration
- `ls /dev/ttyUSB*` shows serial device (for usb-serial test)
- USB-net device gets DHCP address (for usb-net test)

### Key files

- `upstream-qemu/hw/usb/hcd-dwc2.c` -- DWC2 host controller implementation
- `upstream-qemu/hw/arm/bcm2835_peripherals.c:154` -- DWC2 initialization
- `upstream-qemu/hw/arm/bcm2838.c:205-206` -- GIC interrupt wiring
- `run-rpi-boot-test.py` -- test script to modify
- `README.md` -- documentation to update

---

## Phase 2: RPi 3/3+ Network via USB Ethernet

**Goal**: Auto-attach a USB network device to `raspi3b` so it gets networking out of the box.

**Duration**: 1-2 weeks

### Background

RPi 3B/3B+ real hardware uses LAN9514/LAN7515 USB Ethernet hub (Microchip/SMSC). QEMU doesn't emulate this specific chip, but has a generic `usb-net` (CDC/RNDIS) device that Linux can use. This is the same approach other QEMU machines use (e.g., `vmapple.c` auto-attaches USB devices in machine init).

This is the highest-impact improvement: 7/8 PS1 Arty hosts and 5/5 Welland NeTV2 hosts are RPi 3B/3B+.

### Implementation

**QEMU patches** (new patches added to `ci/qemu-patches/`):

1. Auto-attach `usb-net` to DWC2 bus in `hw/arm/raspi.c` machine init for raspi3b/raspi3ap
   - Follow `vmapple.c` pattern: resolve USB bus, create usb-net device, realize
   - Wire to QEMU's NIC backend so `-nic user` works
2. Add `select USB_NETWORK` to RASPI Kconfig (`hw/arm/Kconfig`)
3. Connect MAC address in `hw/misc/bcm2835_property.c` (existing TODO at line 548)

**If DWC2 + `dwc_otg` FIQ FSM causes issues on RPi 3**: Fix the DWC2 FIQ emulation in `hcd-dwc2.c` rather than requiring kernel parameters. The `dwc_otg` driver's FIQ FSM optimization uses direct register access patterns that the emulation may not handle -- investigate and fix the specific failure.

**U-Boot configuration**:

4. Create `ci/rpi_3_qemu_defconfig` -- based on `rpi_3_defconfig`, adding `CONFIG_USB_ETHER_RNDIS=y`, disabling `CONFIG_VIDEO`, `CONFIG_PCI`, `CONFIG_EFI_LOADER`
5. Add U-Boot build step for raspi3b to CI workflow

**Test infrastructure**:

6. Parameterize `run-rpi-boot-test.py` for machine type (raspi3b vs raspi4b)
   - Different QEMU machine (`-M raspi3b` vs `-M raspi4b`)
   - Different DTB (`bcm2837-rpi-3-b.dtb` vs `bcm2711-rpi-4-b.dtb`)
   - Same kernel `Image` (both AArch64)
   - Same network test checkpoints (DHCP, ping, HTTPS)
7. Add CI job for raspi3b boot + network test
8. Create `run-rpi3-boot-test.py` or extend existing test with `--machine` flag

### Verification

Same checkpoints as raspi4b:
- U-Boot DHCP, TFTP transfers, booti starts kernel
- Linux boots, USB Ethernet driver loads (`cdc_ether` or `rndis_host`)
- DHCP lease acquired, ping 8.8.8.8, HTTPS fetch

### Key files

- `upstream-qemu/hw/arm/raspi.c` -- machine init (add USB-net auto-attach)
- `upstream-qemu/hw/arm/Kconfig` -- RASPI config (add USB_NETWORK)
- `upstream-qemu/hw/misc/bcm2835_property.c:548` -- MAC address TODO
- `upstream-qemu/hw/usb/dev-network.c` -- USB CDC/RNDIS device
- `ci/rpi_3_qemu_defconfig` -- new U-Boot config

---

## Phase 3: USB Device VID/PID Matching

**Goal**: QEMU USB devices present correct vendor/product IDs so udev rules and device detection work.

**Duration**: 1-2 weeks

### Background

fpgas.online uses udev rules and device detection based on USB VID/PID. Key devices:

| Device | Real VID:PID | Purpose | QEMU device |
|--------|-------------|---------|-------------|
| FTDI FT2232 | `0403:6010` | JTAG + UART for Arty A7 | `usb-serial` (defaults to `0403:6001` FT232BM) |
| ASIX AX88179 | `0b95:1790` | USB GbE for FPGA board Ethernet | `usb-net` (defaults to QEMU vendor ID) |

### Implementation

**QEMU patches**:

1. Check if `usb-serial` (`hw/usb/dev-serial.c`) supports `vendorid`/`productid` property overrides
   - If yes: document how to use them
   - If no: add QOM properties to override USB descriptor VID/PID (small patch, ~20 lines)
2. Same for `usb-net` (`hw/usb/dev-network.c`)
3. For FTDI FT2232 two-interface fidelity: attach two `usb-serial` instances behind a `usb-hub`
   - Channel A (if00): JTAG serial -- `/dev/ttyUSB0`
   - Channel B (if01): UART serial -- `/dev/ttyUSB1`
   - This matches the real device topology visible in `lsusb` output from PS1/Welland hosts

**Test infrastructure**:

4. Add USB device detection to boot test (check `lsusb` output for expected VID:PID)
5. Verify udev rules create expected device paths (`/dev/serial/by-id/...`)

### Verification

- `lsusb` shows `0403:6010` (FTDI FT2232)
- `ls /dev/ttyUSB0 /dev/ttyUSB1` shows two serial ports
- `lsusb` shows `0b95:1790` (ASIX AX88179) if USB Ethernet adapter is attached

### Key files

- `upstream-qemu/hw/usb/dev-serial.c` -- FTDI USB serial device
- `upstream-qemu/hw/usb/dev-network.c` -- USB CDC/RNDIS network device
- `upstream-qemu/hw/usb/dev-hub.c` -- USB hub device

---

## Phase 4: Camera Mock/Test Infrastructure

**Goal**: Enable testing of camera-dependent deployment without real camera hardware.

**Duration**: 1-2 weeks

### Background

fpgas.online uses CSI camera modules with `libcamera-vid` to stream live video:
```bash
libcamera-vid -t 0 --inline --listen -o tcp://${ip}:4444
```

The CSI camera requires VideoCore GPU firmware (not emulated, impractical to emulate). However, the downstream consumer (nginx HLS transcoding on the gateway server) only sees a TCP H.264 stream.

`libcamera-vid` cannot use v4l2loopback or vivid devices -- it has its own hardware enumeration that talks to the CSI/ISP pipeline specifically.

### Implementation

**Test camera service** (produces identical TCP output from synthetic source):

1. Create `cam-test.sh` -- uses `ffmpeg` or `gst-launch-1.0` with `videotestsrc` to produce H.264 stream to `tcp://IP:4444`
   ```bash
   gst-launch-1.0 videotestsrc pattern=smpte \
     ! video/x-raw,width=640,height=480,framerate=15/1 \
     ! x264enc tune=zerolatency ! h264parse \
     ! tcpserversink host=0.0.0.0 port=4444
   ```
2. Create `cam-test.service` systemd unit as drop-in replacement for `cam.service`
3. Include gstreamer/ffmpeg in test initramfs (`build-initramfs.py`)
4. Add camera stream test to CI -- verify TCP connection on port 4444 produces valid H.264

**For deployment testing**: The test service can be configured via an environment variable or config file to switch between real camera (`libcamera-vid`) and test mode (`gst-launch`). This way the same service unit works in both QEMU and real hardware.

### Verification

- `cam-test.service` starts successfully in QEMU
- TCP connection to port 4444 receives H.264 data
- If gateway HLS transcoding is running, `/live/piN.m3u8` produces valid HLS playlist

### Key files

- New: `cam-test.sh`, `cam-test.service`
- `build-initramfs.py` -- add gstreamer packages
- Reference: `fpgas.online-cam` repo (`cam.sh`, `cam.service`)

---

## Phase 5: RPi 5 Minimum Viable Emulation

**Goal**: Boot RPi 5 with network and serial -- enough to test PXE-booted deployments.

**Duration**: 2-4 months

### Background

RPi 5 uses BCM2712 SoC with RP1 southbridge (connected via PCIe). Key facts:
- CPU: Cortex-A76 x4 (QEMU has this model)
- Interrupt controller: GIC-400/GICv2 (same as RPi 4B, NOT GICv3)
- RP1 Ethernet: **Cadence GEM/MACB** (`hw/net/cadence_gem.c`, 1860 lines, mature QEMU model)
- RP1 UART: **PL011** (`hw/char/pl011.c`, mature)
- RP1 Peripherals Datasheet: https://datasheets.raspberrypi.com/rp1/rp1-peripherals.pdf

This is a significant advantage over RPi 4B's GENET (which had zero QEMU support and no public documentation). The two critical peripherals for boot+network already exist in QEMU.

### Implementation

**BCM2712 SoC skeleton** (~1500 lines new code):

1. `hw/arm/bcm2712.c` -- SoC model following `bcm2838.c` pattern
   - Cortex-A76 x4 (existing CPU model)
   - GIC-400/GICv2 (existing, same as RPi 4B)
   - On-SoC PL011 UART for early console
   - Basic memory map from BCM2712 device tree
2. `include/hw/arm/bcm2712.h` -- SoC state definition
3. Kconfig and meson.build entries

**BCM2712 PCIe root complex** (~500 lines):

4. `hw/misc/bcm2712_pcie.c` -- adapt from existing BCM2838 PCIe patches
   - BCM2712 has 3 PCIe controllers (vs 1 on BCM2711)
   - RP1 connects to one of them
5. Wire PCIe IRQs to GIC

**RP1 as PCIe device** (~1000 lines):

6. `hw/misc/rp1.c` -- PCIe endpoint that maps internal register space
   - Instantiate Cadence GEM for Ethernet (reuse existing `cadence_gem.c`)
   - Instantiate PL011 for UART (reuse existing)
   - Stub all other RP1 peripherals as unimplemented (GPIO, I2C, SPI, DMA)
7. `include/hw/misc/rp1.h`

**Machine definition** (~300 lines):

8. `hw/arm/raspi5b.c` -- following `raspi4b.c` pattern
   - Device tree loading/modification
   - Memory layout
   - Boot configuration
9. `docs/system/arm/raspi.rst` -- update documentation

**Build and test infrastructure**:

10. U-Boot defconfig for RPi 5 QEMU
11. PXE boot test for raspi5b
12. CI job for raspi5b

### What can be reused from RPi 4B

- GIC setup code (identical GICv2)
- PL011 UART (same IP block)
- SoC scaffolding pattern from `bcm2838.c`
- Machine definition pattern from `raspi4b.c`
- Build infrastructure (meson.build, Kconfig patterns)
- Test harness structure

### What is NOT in scope for minimum viable

- I2C, SPI, GPIO (RP1-specific controllers, not needed for boot+network)
- DMA controller (`bcm2712-dma`, different from `bcm2835-dma`)
- SD/MMC (`bcm2712-sdhci`, different variant)
- HDMI, V3D GPU
- PCIe device emulation for Acorn FPGA boards

### Verification

- raspi5b boots to U-Boot prompt
- U-Boot DHCP via Cadence GEM Ethernet
- TFTP transfers, kernel boot
- Linux gets DHCP address via Cadence GEM driver (`macb`)
- Same network tests as raspi4b (ping, HTTPS)

### Key files

- New: `hw/arm/bcm2712.c`, `hw/arm/raspi5b.c`, `hw/misc/rp1.c`, `hw/misc/bcm2712_pcie.c`
- Reuse: `hw/net/cadence_gem.c`, `hw/char/pl011.c`
- Reference: `hw/arm/bcm2838.c`, `hw/arm/raspi4b.c`
- Reference: BCM2712 device tree (`bcm2712.dtsi`), RP1 device tree (`rp1.dtsi`)
- Reference: RP1 Peripherals Datasheet

---

## Phase 6: FTDI FT2232 MPSSE Emulation (Future)

**Goal**: Full FTDI FT2232H emulation with MPSSE so OpenOCD/openFPGALoader work.

**Duration**: 2-4 weeks

### Background

openFPGALoader talks to FT2232H via MPSSE (Multi-Protocol Synchronous Serial Engine) for JTAG bitstream programming. QEMU's `usb-serial` only emulates basic UART mode (FT232BM profile).

### Implementation

New QEMU USB device model:

1. `hw/usb/dev-ft2232h.c` (~1500-2000 lines)
   - USB composite device with two interfaces
   - Channel A: MPSSE mode (bit-bang, byte transfers, clock config, JTAG state machine)
   - Channel B: UART mode (standard serial)
   - MPSSE command interpreter based on FTDI AN_108 application note
   - Backend: chardev socket or custom JTAG proxy protocol
2. VID:PID `0403:6010`, product string "Digilent USB Device"
3. Integration tests with openFPGALoader (verify JTAG scan chain detection)

### Alternative

USB passthrough to real FT2232H: `-device usb-host,vendorid=0x0403,productid=0x6010`. Works today if QEMU is built with libusb. Requires physical hardware on the host.

### Key files

- New: `hw/usb/dev-ft2232h.c`
- Reference: FTDI AN_108 (MPSSE command reference)
- Reference: Linux `ftdi_sio.c` driver
- Reference: openFPGALoader FTDI backend source

---

## Phase Dependencies

```
Phase 1 (USB2 Validation)
    │
    ├──► Phase 2 (RPi 3 Network) ──► Phase 3 (VID/PID Matching)
    │                                       │
    │                                       └──► Phase 6 (FTDI MPSSE)
    │
    └──► Phase 4 (Camera Mock) [independent of Phase 2]

Phase 5 (RPi 5) [independent, can start after Phase 1 validates USB approach]
```

## Commit Strategy

Each phase produces many small, discrete commits. Examples:

- "Test: add USB keyboard device to raspi4b boot test"
- "Test: add USB device detection checkpoint"
- "Docs: update README to document USB2 support"
- "QEMU patch: auto-attach usb-net to raspi3b DWC2 bus"
- "QEMU patch: add USB_NETWORK to RASPI Kconfig"
- "CI: add rpi_3_qemu_defconfig for U-Boot"
- "Test: parameterize boot test for raspi3b machine type"
- "CI: add raspi3b boot test job"

No commit should change more than one logical thing. Build infrastructure, QEMU patches, test changes, and documentation updates are separate commits even within the same phase.
