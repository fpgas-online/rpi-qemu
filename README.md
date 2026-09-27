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
- **Raspberry Pi Zero / Zero W (`raspi0`):** stock Raspberry Pi OS boots to a serial login, wired networking through a USB Ethernet adapter (`usb-net`), and a settable board serial -- see [below](#raspberry-pi-zero--zero-w-raspi0)

## Requirements

- **Host:** x86_64 Linux (Debian trixie or compatible)
- **Pi model:** Raspberry Pi 4B (`raspi4b`) and Raspberry Pi Zero / Zero W (`raspi0`)
- **Kernel/DTB/initrd:** from [raspberrypi/firmware](https://github.com/raspberrypi/firmware/tree/master/boot) or your own build

## Install

### APT (Debian trixie / amd64)

```bash
# Add the repository (signed; setup also on https://fpgas.online/rpi-qemu/)
sudo install -d -m0755 /etc/apt/keyrings
curl -fsSL https://fpgas.online/rpi-qemu/rpi-qemu.gpg | sudo tee /etc/apt/keyrings/rpi-qemu.gpg > /dev/null
echo "deb [signed-by=/etc/apt/keyrings/rpi-qemu.gpg] https://fpgas.online/rpi-qemu/trixie/ ./" \
  | sudo tee /etc/apt/sources.list.d/rpi-qemu.list
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
  -append "earlycon=pl011,mmio32,0xfe201000 console=ttyAMA0 rdinit=/init" \
  -nic user \
  -serial stdio -display none
```

The emulated Pi gets a DHCP address via QEMU user-mode networking and can reach the internet. Get `Image` and `bcm2711-rpi-4-b.dtb` from the [raspberrypi/firmware](https://github.com/raspberrypi/firmware/tree/master/boot) repo.

To attach USB devices (keyboard, serial, etc.) to the emulated Pi:

```bash
qemu-rpi-system-aarch64 -M raspi4b \
  -kernel Image -dtb bcm2711-rpi-4-b.dtb -initrd initrd.gz \
  -append "earlycon=pl011,mmio32,0xfe201000 console=ttyAMA0 rdinit=/init" \
  -nic user \
  -device usb-kbd \
  -chardev null,id=ser0 -device usb-serial,chardev=ser0 \
  -serial stdio -display none
```

USB 2.0 devices attach to the DWC2 controller. The guest sees standard `/dev/ttyUSB*` serial ports and `/dev/input/*` HID devices.

### Raspberry Pi Zero / Zero W (`raspi0`)

Boot stock Raspberry Pi OS (armhf) on the `raspi0` machine, with the kernel and device tree from the image's own boot partition:

```bash
# An overlay keeps the downloaded image pristine and leaves room for
# Raspberry Pi OS to grow its root filesystem on first boot.
qemu-img create -f qcow2 -b 2026-09-15-raspios-trixie-armhf-lite.img -F raw zero.qcow2 4G

qemu-rpi-system-aarch64 -M raspi0 \
  -kernel kernel.img -dtb bcm2708-rpi-zero-w.dtb \
  -drive file=zero.qcow2,format=qcow2,if=sd \
  -append "console=serial0,115200 root=/dev/mmcblk0p2 rootfstype=ext4 rootwait" \
  -serial null -serial stdio -display none
```

- **Serial ports.** The first `-serial` is the PL011, which a Zero W gives to Bluetooth; the second is the mini UART, which is `serial0` and so the console (`ttyS0`). QEMU behaves as if `enable_uart=1`: on a real Zero W the stock `config.txt` leaves it at 0 (the default when the mini UART is the primary UART), so the board needs `enable_uart=1` for this console.
- **Firmware behaviour.** With `-kernel` there is no VideoCore firmware, so QEMU does what it would to the DTB and command line: routes GPIO 14/15 to `serial0` (`enable_uart=1`), passes the DTB's own `bootargs` (e.g. `8250.nr_uarts=1`) ahead of `-append`, resolves `console=serial0` to the real tty, and publishes the board revision and serial. The boot reaches `raspberrypi login:` on `ttyS0`.
- **Wired networking.** A Zero has no on-board Ethernet; like the real board, give it a USB Ethernet adapter on the OTG port: `-netdev user,id=n0 -device usb-net,netdev=n0`. It appears as a `cdc_ether` interface (`usb0`) and gets a DHCP lease from QEMU (`10.0.2.15`, gateway `10.0.2.2`). Leave it off to test a Zero with no network -- also a configuration it boots in.
- **Board serial.** `-M raspi0,board-serial=0x00000000c0ffee01` sets the serial the guest sees in `/proc/cpuinfo`, `/proc/device-tree/serial-number` and the firmware's `GET_BOARD_SERIAL` (default `0x0000000012345678`).
- **Magic SysRq over serial.** The mini UART has no break detection (BCM2835 ARM Peripherals, 2.2), so a BREAK never reaches SysRq on `ttyS0` -- on hardware or here. Use the PL011 as the console, as `dtoverlay=disable-bt` does on a real Zero W: apply the overlay to the DTB (`fdtoverlay -i bcm2708-rpi-zero-w.dtb -o zero-w-disable-bt.dtb overlays/disable-bt.dtbo`), boot with that DTB and swap the ports (`-serial stdio -serial null`); `console=serial0` then lands on `ttyAMA0`, and with `-serial mon:stdio`, Ctrl-A b sends a BREAK.
- **USB gadget mode.** With `dtoverlay=dwc2` (apply `overlays/dwc2.dtbo` with `fdtoverlay`) the stock kernel's `dwc2` driver runs the OTG port as a peripheral, and the gadget it runs (configfs, or a legacy `g_*` module) appears on a USB host as a real device, over USB/IP: add `-device dwc2-gadget,bus=usbip0.0` to a `usbip-server` (see [USB/IP export](#usbip-export)) and run `usbip attach -r 127.0.0.1 -b 1-1` on a Linux host. The Zero sees the host come and go as a real cable does (the USB/IP connection is the host's VBUS); with no client it boots with the gadget unattached. `-global dwc2-usb.otg-cable=host|device` overrides the OTG plug (default: device when a `dwc2-gadget` exists). Tested with configfs CDC-ACM, CDC-ECM, CDC-NCM, mass storage and a composite of them (`run-rpi0-gadget-test.py`).
- **Not emulated:** the BCM43438 Wi-Fi/Bluetooth and the VideoCore (camera, codecs, `vchiq`).

### USB/IP export

`usbip-server` exports the QEMU USB device on its port over
[USB/IP](https://docs.kernel.org/usb/usbip_protocol.html), so another
machine's USB stack uses it as if it were plugged in -- Linux's
`usbip attach` (vhci-hcd), or any USB/IP client:

```bash
qemu-rpi-system-aarch64 -M raspi0 ... \
  -chardev socket,id=usbipchr,host=127.0.0.1,port=3240,server=on,wait=off \
  -device usbip-server,id=usbip0,chardev=usbipchr \
  -drive if=none,id=disk0,format=raw,file=disk.img \
  -device usb-storage,bus=usbip0.0,drive=disk0

# on a Linux host:
usbip list -r 127.0.0.1
sudo usbip attach -r 127.0.0.1 -b 1-1
```

The server enumerates the device itself (vhci-hcd never sends
`SET_ADDRESS`), serves one client at a time, and closes the connection
when the device is unplugged or the machine resets -- how a USB/IP
exporter reports a removed device. It is allowed on the `raspi*` machines
and on `-M none`. Tested by `run-usbip-test.py` (control, bulk,
interrupt, isochronous, unlink, reconnection, with QEMU's usb-storage,
usb-kbd and usb-audio) and `run-usbip-vhci-test.py` (the kernel's
vhci-hcd binding its own usb-storage driver).

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
          apt-get install -y ca-certificates curl
          install -d -m0755 /etc/apt/keyrings
          curl -fsSL https://fpgas.online/rpi-qemu/rpi-qemu.gpg > /etc/apt/keyrings/rpi-qemu.gpg
          echo "deb [signed-by=/etc/apt/keyrings/rpi-qemu.gpg] https://fpgas.online/rpi-qemu/trixie/ ./" \
            > /etc/apt/sources.list.d/rpi-qemu.list
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

- **Pi 4B and Pi Zero only.** Pi 3B/3B+ use USB-attached Ethernet (LAN9514/LAN7515) which QEMU doesn't emulate. The Pi Zero has no Wi-Fi/Bluetooth model and no USB gadget mode (see above).
- **No GPU.** `start4.elf` is fetched but not executed. No HDMI, no hardware video decode.
- **No USB 3.0.** The VL805 xHCI controller (USB 3.0) requires PCIe, which isn't fully emulated. USB 2.0 works via the DWC2 controller.
- **No bridged/tap networking tested.** User-mode and socket networking work; bridged/tap not tested.

---

## For Developers

### Repository Structure

```
ci/
  qemu-patches/          23 patches adding GENET Ethernet to QEMU v11.1.0
  debian/                Debian packaging for qemu-rpi-* packages
  vc-boot-pi4b.env       VideoCore boot emulation script (U-Boot environment)
  rpi_4_qemu_defconfig   U-Boot config for interactive testing
  rpi_4_qemu_pxeboot_defconfig  U-Boot config for PXE boot firmware
  build-debs.py          Local .deb build script
.github/workflows/
  build-qemu-packages.yml   Build debs + pxeboot firmware, publish APT repo
  rpi-boot-test.yml          End-to-end boot test
run-rpi-boot-test.py              Interactive boot test (U-Boot via serial, -nic user)
run-rpi-pxeboot-test.py           Autonomous PXE boot test (-nic user)
run-rpi-socket-boot-test.py       Socket networking boot test (no peer, -nic socket)
run-rpi-socket-network-test.py    Socket networking with DHCP/TFTP peer (-nic socket)
run-usbip-test.py                 USB/IP server test (-M none, QEMU USB devices)
run-usbip-vhci-test.py            USB/IP interop with the kernel's vhci-hcd (root)
run-rpi0-gadget-test.py           raspi0 USB gadget (dwc2 peripheral mode) over USB/IP
```

### QEMU Patches

23 patches on top of QEMU v11.1.0 (from Debian), ported from Sergey Kambalin's Kambalin v6 series:

- **BCM2838 GENET Ethernet** -- Full DMA-based GbE MAC with MDIO/PHY, TX/RX descriptor rings
- **BCM2838 PCIe Root Complex** -- Basic PCIe host bridge
- **BCM2838 RNG200** -- Hardware random number generator
- **BCM2838 Thermal Sensor** -- Temperature monitoring
- **PL011 UART fix** -- Re-enable UART after U-Boot handoff
- **Serial alias fix** -- PL011 is `ttyAMA0`, matching expected behavior

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

# Run socket networking tests (proves -nic socket works with GENET)
uv run run-rpi-socket-boot-test.py       # boot with socket, no peer
uv run run-rpi-socket-network-test.py    # full DHCP/TFTP over socket
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
