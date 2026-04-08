# qemu-rpi-pxeboot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Debian package providing PXE boot firmware that makes QEMU's raspi4b emulate the VideoCore bootloader's TFTP sequence transparently.

**Architecture:** U-Boot is built with a custom defconfig and embedded environment script that automatically performs the VideoCore TFTP probe sequence on boot. The user sees VC-style progress messages, not U-Boot internals. The package ships pre-compiled firmware files (`rpi4b-pxeboot.bin` + `.dtb`).

**Tech Stack:** U-Boot 2026.04-rc5 (aarch64), Debian packaging (dpkg-buildpackage), GitHub Actions (debian:trixie container), QEMU raspi4b with GENET Ethernet.

**Spec:** `docs/specs/2026-04-08-qemu-rpi-firmware-design.md`

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `ci/vc-boot-pi4b.env` | CREATE | VideoCore boot script as U-Boot environment |
| `ci/rpi_4_qemu_pxeboot_defconfig` | CREATE | U-Boot defconfig for pxeboot firmware |
| `ci/debian/control` | MODIFY | Add `qemu-rpi-pxeboot` binary package stanza |
| `ci/debian/changelog` | MODIFY | Add pxeboot entry |
| `ci/debian/qemu-rpi-pxeboot.install` | CREATE | Install manifest for firmware files |
| `.github/workflows/build-qemu-packages.yml` | MODIFY | Add `build-pxeboot` job |
| `run-rpi-pxeboot-test.py` | CREATE | End-to-end test for pxeboot firmware |

---

### Task 1: Create the VideoCore boot environment script

The core of the package. This U-Boot `.env` file defines environment variables that implement the VideoCore TFTP probe sequence.

**Files:**
- Create: `ci/vc-boot-pi4b.env`

- [ ] **Step 1: Create the environment file**

This file is the U-Boot environment in `.env` format. Each line is `key=value`. Multi-command values use `;` separators. The `vc_boot` variable is the main entry point called by `CONFIG_BOOTCOMMAND="run vc_boot"`.

```env
# ci/vc-boot-pi4b.env
# VideoCore PXE boot emulation for Raspberry Pi 4B
# This file is embedded into U-Boot as the default environment.

# Memory layout
scratch=0x02000000
kernel_addr_r=0x10000000
fdt_addr_r=0x0f000000
ramdisk_addr_r=0x12000000

# Default board identity (override via QEMU -fw_cfg name=opt/pi_serial,string=XXXX)
vc_serial=deadbeef

# Default file names (overridden by config.txt parsing)
vc_kernel_name=kernel8.img
vc_dtb_name=bcm2711-rpi-4-b.dtb

# Boot state flags
have_kernel=0
have_dtb=0
have_initrd=0

# --- Helpers ---

# Probe a file: sets ${found} to 1 if loaded, 0 otherwise. Uses ${path}.
vc_probe=setenv found 0; tftpboot ${scratch} ${path} && setenv found 1 || true

# Extract a key from config.txt loaded at ${scratch}.
# config.txt has lines like "kernel=mykernel.img". We cannot use env import -t
# because real config.txt has conditional blocks ([pi4]) and dtoverlay= lines
# that would pollute the environment. Instead we grep for specific keys.
# U-Boot's hush shell is limited, so we use setexpr for substring matching.
# Fallback: if setexpr fails, skip silently.
vc_extract_key=setenv _val; setexpr _val gsub ".*${_key}=" "" ${_buf} && setexpr _val gsub "\n.*" "" ${_val} || true

# Parse config.txt: extract kernel= and device_tree= only
vc_parse_config=\
  if test "${found}" = "1"; then \
    setenv _buf ${scratch}; \
    setenv _key kernel; run vc_extract_key; \
    if test -n "${_val}"; then setenv vc_kernel_name ${_val}; fi; \
    setenv _key device_tree; run vc_extract_key; \
    if test -n "${_val}"; then setenv vc_dtb_name ${_val}; fi; \
  fi

# --- UART progress messages (emulate VideoCore output) ---
vc_msg_boot=echo "Raspberry Pi Bootloader"; echo "Board: BCM2711"; echo "NET:   Ethernet"
vc_msg_dhcp=echo "DHCP: ${ipaddr}"
vc_msg_probe=echo "Loading ${path} ... ${_status}"
vc_msg_boot_kernel=echo ""; echo "Starting kernel ..."

# --- Main boot sequence ---
vc_boot=\
  run vc_msg_boot; \
  setenv bootp_vci "PXEClient:Arch:00000:UNDI:002001"; \
  setenv bootp_arch 0; \
  setenv autoload no; \
  dhcp; \
  run vc_msg_dhcp; \
  setenv path ${vc_serial}/start4.elf; run vc_probe; \
  setenv _status; test "${found}" = "1" && setenv _status OK || setenv _status "not found"; run vc_msg_probe; \
  setenv path ${vc_serial}/autoboot.txt; run vc_probe; \
  setenv path ${vc_serial}/config.txt; run vc_probe; \
  setenv _status; test "${found}" = "1" && setenv _status OK || setenv _status "not found"; run vc_msg_probe; \
  run vc_parse_config; \
  setenv path ${vc_serial}/recovery.elf; run vc_probe; \
  setenv path ${vc_serial}/start4.elf; run vc_probe; \
  setenv path ${vc_serial}/fixup4.dat; run vc_probe; \
  setenv _status; test "${found}" = "1" && setenv _status OK || setenv _status "not found"; run vc_msg_probe; \
  setenv path ${vc_serial}/recovery.elf; run vc_probe; \
  setenv path ${vc_serial}/config.txt; run vc_probe; run vc_parse_config; \
  setenv path ${vc_serial}/dt-blob.bin; run vc_probe; \
  setenv path ${vc_serial}/recovery.elf; run vc_probe; \
  setenv path ${vc_serial}/config.txt; run vc_probe; run vc_parse_config; \
  setenv path ${vc_serial}/bootcfg.txt; run vc_probe; \
  setenv path ${vc_serial}/cmdline.txt; run vc_probe; \
  if test "${found}" = "1"; then \
    env import -t ${scratch} ${filesize}; \
  fi; \
  setenv path ${vc_serial}/recovery8.img; run vc_probe; \
  setenv path ${vc_serial}/recovery8-32.img; run vc_probe; \
  setenv path ${vc_serial}/recovery7.img; run vc_probe; \
  setenv path ${vc_serial}/recovery.img; run vc_probe; \
  setenv have_kernel 0; \
  tftpboot ${kernel_addr_r} ${vc_serial}/${vc_kernel_name} && setenv have_kernel 1; \
  if test "${have_kernel}" = "0"; then \
    for k in kernel8.img kernel8-32.img kernel7l.img kernel7.img kernel.img; do \
      tftpboot ${kernel_addr_r} ${vc_serial}/${k} && setenv have_kernel 1 && setenv vc_kernel_name ${k} && break; \
    done; \
  fi; \
  setenv _status; test "${have_kernel}" = "1" && setenv _status OK || setenv _status "not found"; \
  setenv path ${vc_serial}/${vc_kernel_name}; run vc_msg_probe; \
  setenv path ${vc_serial}/armstub8.bin; run vc_probe; \
  setenv path ${vc_serial}/armstub8-32.bin; run vc_probe; \
  setenv path ${vc_serial}/armstub7.bin; run vc_probe; \
  setenv path ${vc_serial}/armstub.bin; run vc_probe; \
  setenv have_dtb 0; \
  tftpboot ${fdt_addr_r} ${vc_serial}/${vc_dtb_name} && setenv have_dtb 1; \
  if test "${have_dtb}" = "0"; then \
    tftpboot ${fdt_addr_r} ${vc_serial}/bcm2711-rpi-4-b.dtb && setenv have_dtb 1; \
  fi; \
  setenv _status; test "${have_dtb}" = "1" && setenv _status OK || setenv _status "not found"; \
  setenv path ${vc_serial}/${vc_dtb_name}; run vc_msg_probe; \
  if test -z "${bootargs}"; then \
    setenv bootargs "earlycon=pl011,mmio32,0xfe201000 console=ttyAMA1"; \
  fi; \
  if test "${have_kernel}" = "1" -a "${have_dtb}" = "1"; then \
    run vc_msg_boot_kernel; \
    booti ${kernel_addr_r} - ${fdt_addr_r}; \
  else \
    echo "PXE boot failed: kernel=${have_kernel} dtb=${have_dtb}"; \
    reset; \
  fi
```

- [ ] **Step 2: Commit**

```bash
git add ci/vc-boot-pi4b.env
git commit -m "Add VideoCore PXE boot environment script for Pi 4B

Emulates the VideoCore GPU bootloader's TFTP probe sequence:
DHCP with PXEClient options, serial-prefixed file probes,
config.txt parsing (kernel=/device_tree=), and kernel loading.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Create the U-Boot defconfig

**Files:**
- Create: `ci/rpi_4_qemu_pxeboot_defconfig`
- Reference: `ci/rpi_4_qemu_defconfig`

- [ ] **Step 1: Create the defconfig**

Start from the existing `rpi_4_qemu_defconfig` and add pxeboot-specific settings:

```
# ci/rpi_4_qemu_pxeboot_defconfig
# U-Boot config for QEMU raspi4b PXE boot (VideoCore emulation)
# Based on rpi_4_qemu_defconfig with silent boot + embedded VC script
CONFIG_ARM=y
CONFIG_ARCH_BCM283X=y
CONFIG_TEXT_BASE=0x00080000
CONFIG_TARGET_RPI_4=y
CONFIG_HAS_CUSTOM_SYS_INIT_SP_ADDR=y
CONFIG_CUSTOM_SYS_INIT_SP_ADDR=0x7fffe30
CONFIG_ENV_SIZE=0x4000
CONFIG_DEFAULT_DEVICE_TREE="bcm2711-rpi-4-b"
CONFIG_OF_LIBFDT_OVERLAY=y
CONFIG_DM_RESET=y
CONFIG_SYS_LOAD_ADDR=0x1000000
# --- PXE boot specific ---
CONFIG_BOOTDELAY=-2
CONFIG_BOOTCOMMAND="run vc_boot"
CONFIG_ENV_SOURCE_FILE="pxeboot"
# Silent console (U-Boot output suppressed, Linux output passes through)
CONFIG_SYS_DEVICE_NULLDEV=y
# --- Disabled features (same as rpi_4_qemu_defconfig) ---
# CONFIG_PCI is not set
# CONFIG_EFI_LOADER is not set
# CONFIG_USE_PREBOOT is not set
# CONFIG_USB is not set
# CONFIG_VIDEO is not set
CONFIG_BOOTSTD_DEFAULTS=y
CONFIG_OF_BOARD_SETUP=y
CONFIG_FDT_SIMPLEFB=y
CONFIG_SYS_PBSIZE=1049
# CONFIG_DISPLAY_CPUINFO is not set
# CONFIG_DISPLAY_BOARDINFO is not set
CONFIG_MISC_INIT_R=y
CONFIG_SYS_PROMPT="U-Boot> "
CONFIG_CMD_GPIO=y
CONFIG_CMD_MMC=y
CONFIG_CMD_FS_UUID=y
CONFIG_ENV_FAT_DEVICE_AND_PART="0:1"
CONFIG_ENV_RELOC_GD_ENV_ADDR=y
CONFIG_ENV_VARS_UBOOT_RUNTIME_CONFIG=y
CONFIG_TFTP_TSIZE=y
CONFIG_DM_DMA=y
CONFIG_BCM2835_GPIO=y
CONFIG_MMC_SDHCI=y
CONFIG_MMC_SDHCI_SDMA=y
CONFIG_MMC_SDHCI_BCM2835=y
CONFIG_BCMGENET=y
CONFIG_PINCTRL=y
# CONFIG_PINCTRL_GENERIC is not set
# CONFIG_REQUIRE_SERIAL_CONSOLE is not set
CONFIG_PHYS_TO_BUS=y
# CONFIG_HEXDUMP is not set
```

- [ ] **Step 2: Commit**

```bash
git add ci/rpi_4_qemu_pxeboot_defconfig
git commit -m "Add U-Boot defconfig for PXE boot firmware

BOOTDELAY=-2 (instant boot), BOOTCOMMAND='run vc_boot',
ENV_SOURCE_FILE='pxeboot' (loads ci/vc-boot-pi4b.env as env),
SYS_DEVICE_NULLDEV=y (for silent console support).

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Test locally -- build U-Boot with pxeboot config and verify boot

Before packaging, verify the firmware works.

**Files:**
- Reference: `ci/vc-boot-pi4b.env`, `ci/rpi_4_qemu_pxeboot_defconfig`
- Reference: `test-images/tftpboot/` (existing TFTP root)

- [ ] **Step 1: Build U-Boot with pxeboot defconfig**

```bash
cd test-images/u-boot
cp ../../ci/rpi_4_qemu_pxeboot_defconfig configs/rpi_4_qemu_pxeboot_defconfig
cp ../../ci/vc-boot-pi4b.env board/raspberrypi/rpi/pxeboot.env
make rpi_4_qemu_pxeboot_defconfig
make -j$(nproc) CROSS_COMPILE=aarch64-linux-gnu-
```

Expected: Build succeeds, produces `u-boot.bin`.

- [ ] **Step 2: Set up TFTP root with Pi 4B layout**

```bash
cd /home/tim/github/fpgas-online/rpi-qemu
mkdir -p test-images/tftpboot/deadbeef
# Copy kernel and DTB into serial-prefixed directory
cp test-images/tftpboot/Image test-images/tftpboot/deadbeef/kernel8.img
cp test-images/tftpboot/bcm2711-rpi-4-b.dtb test-images/tftpboot/deadbeef/
# Create minimal config.txt
echo "kernel=kernel8.img" > test-images/tftpboot/deadbeef/config.txt
echo "earlycon=pl011,mmio32,0xfe201000 console=ttyAMA1 loglevel=4 rdinit=/init" > test-images/tftpboot/deadbeef/cmdline.txt
# Copy initrd
cp test-images/tftpboot/initrd.gz test-images/tftpboot/deadbeef/initrd.gz
```

- [ ] **Step 3: Boot QEMU with the pxeboot firmware**

```bash
QEMU_OVERRIDE=$(which qemu-rpi-system-aarch64 || echo upstream-qemu/build/qemu-system-aarch64)
$QEMU_OVERRIDE -M raspi4b \
  -kernel test-images/u-boot/u-boot.bin \
  -dtb test-images/bcm2711-rpi-4-b.dtb \
  -nic user,tftp=test-images/tftpboot \
  -serial stdio -display none
```

Expected: See VideoCore-style progress messages, TFTP probes for `deadbeef/start4.elf`, `deadbeef/config.txt`, etc., then kernel boots.

- [ ] **Step 4: Debug and iterate**

If the boot script has issues (common: env variable syntax, backslash escaping, hush shell limitations), fix `ci/vc-boot-pi4b.env` and rebuild. The most likely issues are:
- `setexpr` not available or syntax errors → simplify config.txt parsing
- DHCP options not taking effect → check `bootp_vci` variable name
- Memory overlap → adjust scratch/kernel/dtb addresses

- [ ] **Step 5: Commit fixes**

```bash
git add ci/vc-boot-pi4b.env ci/rpi_4_qemu_pxeboot_defconfig
git commit -m "Fix boot script issues found during local testing

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Add Debian packaging for qemu-rpi-pxeboot

**Files:**
- Modify: `ci/debian/control` -- add package stanza
- Modify: `ci/debian/changelog` -- add entry
- Create: `ci/debian/qemu-rpi-pxeboot.install`

- [ ] **Step 1: Add package stanza to debian/control**

Append to `ci/debian/control`:

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
 .
 Usage: qemu-rpi-system-aarch64 -M raspi4b
   -kernel /usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.bin
   -dtb /usr/share/qemu-rpi-pxeboot/rpi4b-pxeboot.dtb
   -nic user,tftp=/srv/tftpboot -serial stdio
```

- [ ] **Step 2: Create install manifest**

```
# ci/debian/qemu-rpi-pxeboot.install
usr/share/qemu-rpi-pxeboot
```

- [ ] **Step 3: Update changelog**

Add entry above the existing one in `ci/debian/changelog`:

```
qemu-rpi (1:11.0.0~rc2+ds-2+rpi2) trixie; urgency=medium

  * Add qemu-rpi-pxeboot package: PXE boot firmware for QEMU raspi4b
    that emulates the VideoCore GPU bootloader's TFTP probe sequence.

 -- Tim Ansell <mithro@mithis.com>  Tue, 08 Apr 2026 12:00:00 +0930
```

- [ ] **Step 4: Commit**

```bash
git add ci/debian/control ci/debian/changelog ci/debian/qemu-rpi-pxeboot.install
git commit -m "Add qemu-rpi-pxeboot to Debian packaging

New binary package in the qemu-rpi source package. Architecture: all
(firmware runs inside QEMU, not natively). Installs to
/usr/share/qemu-rpi-pxeboot/.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Add build-pxeboot job to CI workflow

**Files:**
- Modify: `.github/workflows/build-qemu-packages.yml`

- [ ] **Step 1: Add the build-pxeboot job**

Add after the `build-static` job, before `publish-apt-repo`. The job:
1. Runs in `debian:trixie` container
2. Installs `gcc-aarch64-linux-gnu`, `make`, `bc`, `bison`, `flex`, `python3`, `libssl-dev`
3. Clones U-Boot at commit `47e064f1`
4. Copies defconfig and env file
5. Builds
6. Renames outputs and uploads artifact

```yaml
  build-pxeboot:
    name: Build PXE boot firmware
    runs-on: ubuntu-latest
    container: debian:trixie
    timeout-minutes: 30
    steps:
      - name: Install base tools
        run: |
          apt-get update
          apt-get install -y git ca-certificates

      - name: Checkout
        uses: actions/checkout@v4

      - name: Install build dependencies
        run: |
          apt-get install -y --no-install-recommends \
            build-essential gcc-aarch64-linux-gnu \
            make bc bison flex libssl-dev python3 python3-setuptools

      - name: Build PXE boot firmware
        run: |
          git clone --depth=100 https://github.com/u-boot/u-boot.git tmp/u-boot-pxeboot
          cd tmp/u-boot-pxeboot
          git checkout 47e064f13171f15817aa1b22b04e309964b15c2c
          cp ../../ci/rpi_4_qemu_pxeboot_defconfig configs/
          cp ../../ci/vc-boot-pi4b.env board/raspberrypi/rpi/pxeboot.env
          make rpi_4_qemu_pxeboot_defconfig
          make -j$(nproc) CROSS_COMPILE=aarch64-linux-gnu-

          echo "=== Firmware built ==="
          ls -lh u-boot.bin
          ls -lh arch/arm/dts/bcm2711-rpi-4-b.dtb

      - name: Package firmware
        run: |
          mkdir -p tmp/pxeboot-output
          cp tmp/u-boot-pxeboot/u-boot.bin tmp/pxeboot-output/rpi4b-pxeboot.bin
          cp tmp/u-boot-pxeboot/arch/arm/dts/bcm2711-rpi-4-b.dtb tmp/pxeboot-output/rpi4b-pxeboot.dtb

      - name: Upload firmware artifact
        uses: actions/upload-artifact@v4
        with:
          name: qemu-rpi-pxeboot
          path: tmp/pxeboot-output/
          retention-days: 90
```

- [ ] **Step 2: Update publish-apt-repo to include pxeboot deb**

Add `needs: [build-debs, build-pxeboot]` to `publish-apt-repo`. Add a step to download the pxeboot artifact and build the deb:

```yaml
      - name: Download pxeboot firmware
        uses: actions/download-artifact@v4
        with:
          name: qemu-rpi-pxeboot
          path: tmp/pxeboot-firmware/

      - name: Build pxeboot deb
        run: |
          mkdir -p tmp/debs/pxeboot-pkg/usr/share/qemu-rpi-pxeboot
          cp tmp/pxeboot-firmware/rpi4b-pxeboot.bin tmp/debs/pxeboot-pkg/usr/share/qemu-rpi-pxeboot/
          cp tmp/pxeboot-firmware/rpi4b-pxeboot.dtb tmp/debs/pxeboot-pkg/usr/share/qemu-rpi-pxeboot/

          mkdir -p tmp/debs/pxeboot-pkg/DEBIAN
          cat > tmp/debs/pxeboot-pkg/DEBIAN/control <<'CTRL'
          Package: qemu-rpi-pxeboot
          Version: 0.1
          Architecture: all
          Maintainer: Tim Ansell <mithro@mithis.com>
          Recommends: qemu-rpi-system-arm
          Description: PXE network boot emulation for QEMU Raspberry Pi 4B
           Enables QEMU's raspi4b machine to PXE network boot from a standard
           Raspberry Pi TFTP server layout.
          CTRL
          sed -i 's/^          //' tmp/debs/pxeboot-pkg/DEBIAN/control

          dpkg-deb --build tmp/debs/pxeboot-pkg tmp/debs/qemu-rpi-pxeboot_0.1_all.deb
```

- [ ] **Step 3: Update create-release to include pxeboot files**

Add `needs: [build-debs, build-pxeboot]` and include the firmware in the release files glob.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/build-qemu-packages.yml
git commit -m "CI: add build-pxeboot job and publish firmware to APT repo

Builds U-Boot with pxeboot defconfig in debian:trixie container,
packages as qemu-rpi-pxeboot_0.1_all.deb, publishes to GitHub
Pages APT repo alongside the QEMU packages.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Create the pxeboot test script

**Files:**
- Create: `run-rpi-pxeboot-test.py`
- Reference: `run-rpi-boot-test.py` (existing test structure)

- [ ] **Step 1: Create the test script**

The test sets up a TFTP root with standard RPi layout, boots QEMU with the pxeboot firmware, and verifies:
1. VideoCore-style UART messages appear
2. TFTP probes for `deadbeef/start4.elf`, `deadbeef/config.txt`, etc. appear
3. Kernel boots
4. Networking works (DHCP + HTTPS fetch)

The script follows the same structure as `run-rpi-boot-test.py` but:
- Uses `rpi4b-pxeboot.bin` instead of sending manual U-Boot commands
- Sets up a serial-prefixed TFTP root (`deadbeef/`)
- Does NOT send any commands via stdin -- the firmware is fully autonomous
- Verifies VC-style output messages

```python
#!/usr/bin/env python3
"""
RPi4B QEMU PXE Boot Test: VideoCore emulation via pxeboot firmware

Tests that the qemu-rpi-pxeboot firmware correctly emulates the
VideoCore bootloader's PXE sequence. The firmware boots autonomously
with zero interaction -- just like a real Pi.

Usage: uv run run-rpi-pxeboot-test.py
"""
# Full implementation: ~150 lines following run-rpi-boot-test.py pattern
# Key differences:
# - No stdin commands (firmware is autonomous)
# - TFTP root has deadbeef/ prefix directory
# - Checks for VC-style messages: "Raspberry Pi Bootloader", "Loading start4.elf"
# - Checks for kernel boot + networking
```

- [ ] **Step 2: Run the test locally**

```bash
uv run run-rpi-pxeboot-test.py
```

Expected: All checks pass.

- [ ] **Step 3: Commit**

```bash
git add run-rpi-pxeboot-test.py
git commit -m "Add PXE boot firmware test script

Tests the full autonomous boot: VC-style UART messages,
TFTP probe sequence, config.txt parsing, kernel boot, networking.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Push and verify CI

- [ ] **Step 1: Push all changes**

```bash
git push origin main
```

- [ ] **Step 2: Verify build-pxeboot job runs**

```bash
gh run list --repo fpgas-online/rpi-qemu --limit 3
```

Expected: "Build QEMU RPi Packages" workflow runs with the new `build-pxeboot` job.

- [ ] **Step 3: Watch for completion**

```bash
gh run watch <run-id> --repo fpgas-online/rpi-qemu
```

Expected: build-pxeboot passes, publish-apt-repo includes pxeboot deb.

- [ ] **Step 4: Verify the deb is in the APT repo**

```bash
curl -s https://fpgas-online.github.io/rpi-qemu/dists/trixie/main/binary-amd64/Packages | grep qemu-rpi-pxeboot
```

Expected: Package entry for `qemu-rpi-pxeboot`.

- [ ] **Step 5: Fix any CI issues and re-push**

Iterate until all green.
