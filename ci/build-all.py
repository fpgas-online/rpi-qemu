#!/usr/bin/env python3
"""
Build all dependencies for the RPi QEMU boot test.

Downloads and builds QEMU (with GENET patches), U-Boot, RPi kernel/DTB,
Alpine initramfs - everything needed to run run-rpi-boot-test.py.

Usage: uv run ci/build-all.py [--skip-qemu] [--skip-uboot]
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent.resolve()
NPROC = os.cpu_count() or 4

# Versions - pinned for reproducibility
QEMU_VERSION = "v11.0.0-rc2"
QEMU_REPO = "https://gitlab.com/qemu-project/qemu.git"
UBOOT_COMMIT = "47e064f13171f15817aa1b22b04e309964b15c2c"
UBOOT_REPO = "https://github.com/u-boot/u-boot.git"
ALPINE_VERSION = "3.21.3"
ALPINE_URL = (
    f"https://dl-cdn.alpinelinux.org/alpine/v3.21/releases/aarch64/"
    f"alpine-minirootfs-{ALPINE_VERSION}-aarch64.tar.gz"
)
# RPi firmware - use the latest master for kernel8.img and DTB
RPI_FW_REPO = "https://github.com/raspberrypi/firmware.git"


def run(cmd, cwd=None, check=True):
    """Run a command and print it."""
    if isinstance(cmd, str):
        cmd = cmd.split()
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check)


def build_qemu():
    """Clone QEMU, apply GENET patches, and build."""
    qemu_dir = BASE / "upstream-qemu"
    qemu_bin = qemu_dir / "build" / "qemu-system-aarch64"

    if qemu_bin.exists():
        print(f"QEMU already built: {qemu_bin}")
        return

    print("\n=== Building QEMU with GENET patches ===")

    # Clone QEMU at the right version
    if not qemu_dir.exists():
        print("Cloning QEMU...")
        run(["git", "clone", "--depth=1", "--branch", QEMU_VERSION,
             QEMU_REPO, str(qemu_dir)])
    else:
        print(f"QEMU source exists at {qemu_dir}")

    # Apply GENET patches
    patches_dir = BASE / "ci" / "qemu-patches"
    if patches_dir.exists():
        patches = sorted(patches_dir.glob("*.patch"))
        print(f"Applying {len(patches)} GENET patches...")
        for patch in patches:
            result = subprocess.run(
                ["git", "apply", "--check", str(patch)],
                cwd=qemu_dir, capture_output=True
            )
            if result.returncode == 0:
                run(["git", "apply", str(patch)], cwd=qemu_dir)
                print(f"  Applied: {patch.name}")
            else:
                print(f"  Skipped (already applied?): {patch.name}")

    # Configure
    build_dir = qemu_dir / "build"
    build_dir.mkdir(exist_ok=True)
    if not (build_dir / "build.ninja").exists():
        print("Configuring QEMU...")
        run(["../configure", "--target-list=aarch64-softmmu"],
            cwd=build_dir)

    # Build
    print(f"Building QEMU with {NPROC} jobs...")
    run(["ninja", f"-j{NPROC}"], cwd=build_dir)

    if qemu_bin.exists():
        print(f"QEMU built successfully: {qemu_bin}")
    else:
        print("ERROR: QEMU build failed!")
        sys.exit(1)


def build_uboot():
    """Clone U-Boot and build with rpi_4_qemu_defconfig."""
    uboot_dir = BASE / "test-images" / "u-boot"
    uboot_bin = uboot_dir / "u-boot.bin"

    if uboot_bin.exists():
        print(f"U-Boot already built: {uboot_bin}")
        return

    print("\n=== Building U-Boot ===")

    # Clone U-Boot
    if not uboot_dir.exists():
        print("Cloning U-Boot...")
        (BASE / "test-images").mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--depth=100", UBOOT_REPO, str(uboot_dir)])
        run(["git", "checkout", UBOOT_COMMIT], cwd=uboot_dir)

    # Copy the custom defconfig
    defconfig_src = BASE / "test-images" / "u-boot" / "configs" / "rpi_4_qemu_defconfig"
    if not defconfig_src.exists():
        # The defconfig might be force-added in git, try to get it from repo root
        repo_defconfig = BASE / "ci" / "rpi_4_qemu_defconfig"
        if repo_defconfig.exists():
            shutil.copy2(repo_defconfig, defconfig_src)

    # Build
    print("Configuring U-Boot...")
    run(["make", "rpi_4_qemu_defconfig"], cwd=uboot_dir)
    print(f"Building U-Boot with {NPROC} jobs...")
    run(["make", f"-j{NPROC}", "CROSS_COMPILE=aarch64-linux-gnu-"],
        cwd=uboot_dir)

    if uboot_bin.exists():
        print(f"U-Boot built successfully: {uboot_bin}")
    else:
        print("ERROR: U-Boot build failed!")
        sys.exit(1)


def download_rpi_kernel():
    """Download the RPi kernel Image and DTB."""
    tftpboot = BASE / "test-images" / "tftpboot"
    tftpboot.mkdir(parents=True, exist_ok=True)

    image_path = tftpboot / "Image"
    dtb_path = tftpboot / "bcm2711-rpi-4-b.dtb"
    kernel8_path = BASE / "test-images" / "kernel8.img"

    if image_path.exists() and dtb_path.exists():
        print(f"RPi kernel already present: {image_path}")
        return

    print("\n=== Downloading RPi kernel and DTB ===")

    # Download kernel8.img (compressed) and DTB using sparse checkout
    fw_dir = BASE / "test-images" / "rpi-firmware"
    if not fw_dir.exists():
        run(["git", "clone", "--depth=1", "--filter=blob:none",
             "--sparse", RPI_FW_REPO, str(fw_dir)])
        run(["git", "sparse-checkout", "set", "boot/kernel8.img",
             "boot/bcm2711-rpi-4-b.dtb"], cwd=fw_dir)

    fw_kernel = fw_dir / "boot" / "kernel8.img"
    fw_dtb = fw_dir / "boot" / "bcm2711-rpi-4-b.dtb"

    if fw_kernel.exists():
        shutil.copy2(fw_kernel, kernel8_path)
        # Decompress to get uncompressed Image
        print("Decompressing kernel...")
        run(["gunzip", "-k", "-f", str(kernel8_path)])
        # gunzip creates kernel8 (without .img extension), rename to Image
        decompressed = BASE / "test-images" / "kernel8"
        if decompressed.exists():
            shutil.move(str(decompressed), str(image_path))
        print(f"Kernel Image: {image_path} ({image_path.stat().st_size} bytes)")

    if fw_dtb.exists():
        shutil.copy2(fw_dtb, dtb_path)
        # Also copy to test-images for the DTB prereq check
        shutil.copy2(fw_dtb, BASE / "test-images" / "bcm2711-rpi-4-b.dtb")
        print(f"DTB: {dtb_path}")

    # Clean up firmware clone
    shutil.rmtree(fw_dir, ignore_errors=True)


def build_initramfs():
    """Download Alpine and build the test initramfs."""
    initrd = BASE / "test-images" / "test-initramfs.cpio.gz"
    alpine_tar = BASE / "test-images" / "alpine-minirootfs.tar.gz"
    tftpboot = BASE / "test-images" / "tftpboot"

    if initrd.exists() and (tftpboot / "initrd.gz").exists():
        print(f"Initramfs already present: {initrd}")
        return

    print("\n=== Building initramfs ===")

    # Download Alpine
    if not alpine_tar.exists():
        (BASE / "test-images").mkdir(parents=True, exist_ok=True)
        print(f"Downloading Alpine {ALPINE_VERSION}...")
        run(["wget", "-q", "-O", str(alpine_tar), ALPINE_URL])

    # Build initramfs (needs root for mknod)
    build_script = BASE / "build-initramfs.py"
    if os.geteuid() == 0:
        run([sys.executable, str(build_script)])
    else:
        print("Running build-initramfs.py with sudo...")
        run(["sudo", sys.executable, str(build_script)])

    # Copy to tftpboot
    tftpboot.mkdir(parents=True, exist_ok=True)
    shutil.copy2(initrd, tftpboot / "initrd.gz")
    print(f"Initramfs: {tftpboot / 'initrd.gz'}")


def main():
    parser = argparse.ArgumentParser(description="Build all RPi QEMU test dependencies")
    parser.add_argument("--skip-qemu", action="store_true", help="Skip QEMU build")
    parser.add_argument("--skip-uboot", action="store_true", help="Skip U-Boot build")
    args = parser.parse_args()

    print("=" * 60)
    print("Building RPi QEMU Boot Test Dependencies")
    print("=" * 60)

    if not args.skip_qemu:
        build_qemu()
    if not args.skip_uboot:
        build_uboot()
    download_rpi_kernel()
    build_initramfs()

    print("\n" + "=" * 60)
    print("All dependencies built successfully!")
    print("Run: uv run run-rpi-boot-test.py")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
