# rpi-qemu -- Raspberry Pi Emulation with Network Support

Test Raspberry Pi software without real hardware. This project provides QEMU builds with working Ethernet emulation for the Raspberry Pi 4B, enabling PXE network boot, TFTP/NFS workflows, and CI integration -- the same setups used by [fpgas.online](https://fpgas.online) infrastructure for automated board testing.

## Why?

Upstream QEMU's `raspi4b` machine has no network support. You can boot a kernel, but you can't DHCP, TFTP, or reach the internet. This makes it impossible to test:

- PXE-booted NFS root systems (the standard fpgas.online deployment model)
- Network provisioning and configuration management
- CI pipelines that validate RPi images before deploying to real boards
- Any workflow that depends on the Pi having a working network connection

This project adds the missing BCM2838 GENET Ethernet controller to QEMU's raspi4b emulation, making all of the above work.

## Quick Start

### Install from APT (Debian trixie / amd64)

```bash
# Add the repository
echo "deb [trusted=yes] https://fpgas-online.github.io/rpi-qemu trixie main" | \
  sudo tee /etc/apt/sources.list.d/qemu-rpi.list
sudo apt-get update

# Install QEMU with RPi Ethernet support
sudo apt-get install qemu-rpi-system-arm

# Install PXE boot firmware (optional -- emulates VideoCore bootloader)
sudo apt-get install qemu-rpi-pxeboot
```

### Manual Boot (direct kernel loading)

Load a kernel and initramfs directly -- useful for quick testing:

```bash
qemu-rpi-system-aarch64 -M raspi4b \
  -kernel Image -dtb bcm2711-rpi-4-b.dtb -initrd initrd.gz \
  -append "earlycon=pl011,mmio32,0xfe201000 console=ttyAMA1 rdinit=/init" \
  -nic user \
  -serial stdio -display none
```

The emulated Pi gets a DHCP address via QEMU's SLIRP networking and can reach the internet.

### PXE Boot (VideoCore emulation)

Boot from a TFTP server layout exactly like real Pi hardware:

```bash
# Set up a TFTP root with the standard RPi layout
mkdir -p /srv/tftpboot/deadbeef
cp kernel8.img bcm2711-rpi-4-b.dtb config.txt cmdline.txt /srv/tftpboot/deadbeef/

# Boot -- the firmware handles DHCP, TFTP probing, and kernel loading automatically
qemu-rpi-system-aarch64 -M raspi4b \
  -kernel /usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.bin \
  -dtb /usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.dtb \
  -nic user,tftp=/srv/tftpboot \
  -serial stdio -display none
```

The PXE boot firmware emulates the VideoCore GPU bootloader's TFTP sequence: serial-prefixed file probes, `config.txt` parsing (`kernel=`/`device_tree=` overrides), and kernel loading. Users see VideoCore-style UART progress, not U-Boot.

### Static Binary (no installation needed)

Download a self-contained static binary from the [Releases page](https://github.com/fpgas-online/rpi-qemu/releases):

```bash
tar xzf qemu-rpi-static-linux-amd64.tar.gz
./qemu-rpi-system-aarch64-static -M raspi4b -kernel Image ...
```

### Using in CI (GitHub Actions example)

```yaml
- name: Install QEMU RPi
  run: |
    echo "deb [trusted=yes] https://fpgas-online.github.io/rpi-qemu trixie main" | \
      sudo tee /etc/apt/sources.list.d/qemu-rpi.list
    sudo apt-get update
    sudo apt-get install -y qemu-rpi-system-arm qemu-rpi-pxeboot

- name: Test RPi PXE boot
  run: |
    qemu-rpi-system-aarch64 -M raspi4b \
      -kernel /usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.bin \
      -dtb /usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.dtb \
      -nic user,tftp=test-tftproot \
      -serial stdio -display none
```

Note: the APT packages are built on Debian trixie. For Ubuntu runners, use the `debian:trixie` container.

## Packages

| Package | Description |
|---------|-------------|
| `qemu-rpi-system-arm` | QEMU `qemu-rpi-system-aarch64` binary with GENET Ethernet. Installs alongside standard Debian QEMU. |
| `qemu-rpi-system-data` | Firmware and data files for qemu-rpi. |
| `qemu-rpi-pxeboot` | PXE boot firmware (`rpi4b-pxeboot.bin` + `.dtb`). Emulates VideoCore bootloader TFTP sequence. |

All packages use `qemu-rpi-*` naming to coexist with standard Debian `qemu-system-arm`.

## What Works

- GENET Ethernet at 1 Gbps (full duplex, DHCP, TCP, UDP, ICMP)
- U-Boot DHCP + TFTP (50+ MiB/s via SLIRP)
- Linux `bcmgenet` driver (stock RPi kernel, no modifications)
- PXE network boot with VideoCore-compatible TFTP sequence
- Internet access (ping, HTTPS) via QEMU SLIRP user networking
- `config.txt` parsing (`kernel=`, `device_tree=` overrides)

## Known Limitations

- **Pi 4B only.** Pi 3B/3B+ lack Ethernet emulation in QEMU (they use USB-attached Ethernet which QEMU doesn't implement).
- **No VideoCore GPU.** `start4.elf` is fetched but not executed. No HDMI, no hardware video decode.
- **No USB.** QEMU's raspi4b doesn't emulate the USB controller.
- **SLIRP networking only.** Uses QEMU's user-mode networking (NAT). No bridged/tap networking tested.
- **Console is `ttyAMA1`.** The PL011 UART registers as `ttyAMA1` in the stock RPi kernel (not `ttyAMA0`).

---

## For Developers

### Repository Structure

```
ci/
  qemu-patches/          16 patches adding GENET to QEMU v11.0.0-rc2
  debian/                Debian packaging for qemu-rpi-* packages
  vc-boot-pi4b.env       VideoCore boot script (U-Boot environment)
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
