# rpi-qemu -- Raspberry Pi Emulation with Network Support

Test Raspberry Pi software without real hardware. This is a patched QEMU that adds working Ethernet to the `raspi4b` machine, so you can PXE boot, TFTP, DHCP, and reach the internet -- just like on a real Pi.

## Why?

Upstream QEMU's `raspi4b` machine has no network support. You can boot a kernel, but you can't DHCP, TFTP, or reach the internet. This makes it impossible to test:

- PXE-booted NFS root systems
- Network provisioning and configuration management
- CI pipelines that validate RPi images before deploying to real boards
- Any workflow that depends on the Pi having a working network connection

This project patches QEMU to add Ethernet support to the `raspi4b` machine, making all of the above work.

## What Works

- Gigabit Ethernet (full duplex, DHCP, TCP, UDP, ICMP)
- DHCP + TFTP at 50+ MiB/s
- Stock Raspberry Pi kernels work without modification
- PXE network boot from a standard TFTP server layout
- Internet access (ping, HTTPS) via QEMU user-mode networking
- `config.txt` parsing (`kernel=`, `device_tree=` overrides)

## Requirements

- **Host:** x86_64 Linux (Debian trixie or compatible)
- **Pi model:** Raspberry Pi 4B only (`raspi4b` QEMU machine)
- **Kernel/DTB/initrd:** from [raspberrypi/firmware](https://github.com/raspberrypi/firmware/tree/master/boot) or your own build

## Install

### APT (Debian trixie / amd64)

```bash
# Add the repository
echo "deb [trusted=yes] https://fpgas-online.github.io/rpi-qemu trixie main" | \
  sudo tee /etc/apt/sources.list.d/qemu-rpi.list
sudo apt-get update

# Install QEMU with RPi Ethernet support
sudo apt-get install qemu-rpi-system-arm

# Optional: PXE boot firmware (enables network boot from a TFTP server)
sudo apt-get install qemu-rpi-pxeboot
```

### Static Binary (no installation needed)

Download a self-contained binary from the [Releases page](https://github.com/fpgas-online/rpi-qemu/releases):

```bash
tar xzf qemu-rpi-static-linux-amd64.tar.gz
./qemu-rpi-system-aarch64-static -M raspi4b -kernel Image ...
```

## Usage

### Boot a Kernel Directly

The simplest way to test -- load a kernel and initramfs directly:

```bash
qemu-rpi-system-aarch64 -M raspi4b \
  -kernel Image -dtb bcm2711-rpi-4-b.dtb -initrd initrd.gz \
  -append "earlycon=pl011,mmio32,0xfe201000 console=ttyAMA1 rdinit=/init" \
  -nic user \
  -serial stdio -display none
```

The emulated Pi gets a DHCP address via QEMU user-mode networking and can reach the internet. Get `Image` and `bcm2711-rpi-4-b.dtb` from the [raspberrypi/firmware](https://github.com/raspberrypi/firmware/tree/master/boot) repo.

> **Note:** The stock RPi kernel uses `console=ttyAMA1` (not `ttyAMA0`). Use this in your kernel command line or you won't see any output.

### PXE Network Boot

Boot from a TFTP server layout, the same way a real Pi does:

```bash
# Set up a TFTP root with the standard RPi file layout
mkdir -p /srv/tftpboot/deadbeef
cp kernel8.img bcm2711-rpi-4-b.dtb config.txt cmdline.txt /srv/tftpboot/deadbeef/

# Boot -- the firmware handles DHCP, TFTP, and kernel loading automatically
qemu-rpi-system-aarch64 -M raspi4b \
  -kernel /usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.bin \
  -dtb /usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.dtb \
  -nic user,tftp=/srv/tftpboot \
  -serial stdio -display none
```

The firmware handles DHCP and TFTP automatically, loads your kernel from the TFTP server, and supports `config.txt` overrides (`kernel=`, `device_tree=`). The boot sequence and serial output match what you would see on real Pi hardware.

### Using in CI (GitHub Actions)

> **Important:** The APT packages are built on Debian trixie. On Ubuntu runners, use a `debian:trixie` container.

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    container: debian:trixie
    steps:
      - name: Install QEMU RPi
        run: |
          apt-get update
          apt-get install -y ca-certificates
          echo "deb [trusted=yes] https://fpgas-online.github.io/rpi-qemu trixie main" \
            > /etc/apt/sources.list.d/qemu-rpi.list
          apt-get update
          apt-get install -y qemu-rpi-system-arm qemu-rpi-pxeboot

      - name: Test RPi PXE boot
        run: |
          qemu-rpi-system-aarch64 -M raspi4b \
            -kernel /usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.bin \
            -dtb /usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.dtb \
            -nic user,tftp=test-tftproot \
            -serial stdio -display none
```

## Packages

| Package | Description |
|---------|-------------|
| `qemu-rpi-system-arm` | QEMU `raspi4b` with Ethernet support. Installs alongside standard Debian QEMU. |
| `qemu-rpi-system-data` | Data files (auto-installed as dependency of `qemu-rpi-system-arm`). |
| `qemu-rpi-pxeboot` | PXE boot firmware. Enables network boot from a TFTP server. |

All packages use `qemu-rpi-*` naming to coexist with standard Debian `qemu-system-arm`.

## Known Limitations

- **Pi 4B only.** Pi 3B/3B+ use USB-attached Ethernet which QEMU doesn't emulate.
- **No GPU.** `start4.elf` is fetched but not executed. No HDMI, no hardware video decode.
- **No USB.** QEMU's raspi4b doesn't emulate the USB controller.
- **User-mode networking only.** Uses QEMU's built-in NAT. No bridged/tap networking tested.
- **Console is `ttyAMA1`.** Use `console=ttyAMA1` in your kernel command line (not `ttyAMA0`).

---

## For Developers

### Repository Structure

```
ci/
  qemu-patches/          16 patches adding GENET Ethernet to QEMU v11.0.0-rc2
  debian/                Debian packaging for qemu-rpi-* packages
  vc-boot-pi4b.env       VideoCore boot emulation script (U-Boot environment)
  rpi_4_qemu_defconfig   U-Boot config for interactive testing
  rpi_4_qemu_pxeboot_defconfig  U-Boot config for PXE boot firmware
  build-debs.py          Local .deb build script
.github/workflows/
  build-qemu-packages.yml   Build debs + pxeboot firmware, publish APT repo
  rpi-boot-test.yml          End-to-end boot test
run-rpi-boot-test.py     Interactive boot test (U-Boot commands via serial)
run-rpi-pxeboot-test.py  Autonomous PXE boot test
```

### QEMU Patches

16 patches on top of QEMU v11.0.0-rc2 (from Debian experimental), ported from Sergey Kambalin's Kambalin v6 series:

- **BCM2838 GENET Ethernet** -- Full DMA-based GbE MAC with MDIO/PHY, TX/RX descriptor rings
- **BCM2838 PCIe Root Complex** -- Basic PCIe host bridge
- **BCM2838 RNG200** -- Hardware random number generator
- **BCM2838 Thermal Sensor** -- Temperature monitoring
- **PL011 UART fix** -- Re-enable UART after U-Boot handoff

### U-Boot Configuration

Two defconfigs, both disabling PCI/EFI/USB (which cause multi-second timeouts in QEMU TCG mode):

- `rpi_4_qemu_defconfig` -- Interactive mode (2s boot delay, `bootflow scan`)
- `rpi_4_qemu_pxeboot_defconfig` -- PXE mode (instant boot, embedded VideoCore script)

### Building Locally

```bash
# Build everything (QEMU + U-Boot + initramfs)
python3 ci/build-all.py

# Run the interactive boot test
uv run run-rpi-boot-test.py

# Run the PXE boot test
uv run run-rpi-pxeboot-test.py
```

### CI Architecture

```
Push to main
  │
  ├─ build-debs ─────── QEMU .deb packages (debian:trixie container)
  ├─ build-static ───── Static QEMU binary (no dependencies)
  ├─ build-pxeboot ──── PXE boot firmware (U-Boot cross-compile)
  │
  ├─ publish-apt-repo ─ Deploy to GitHub Pages APT repo
  ├─ create-release ─── GitHub Release with all artifacts
  │
  └─ rpi-boot-test ──── Install from APT, boot QEMU, verify networking
```

## License

QEMU is GPL-2.0+. GENET patches by Sergey Kambalin (GPL-2.0+), ported to QEMU v11 by this project.
