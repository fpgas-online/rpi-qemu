#!/usr/bin/env python3
"""
Build Debian packages for QEMU with RPi GENET Ethernet support.

Downloads the Debian experimental QEMU v11.0.0-rc2 orig tarball,
applies our debian/ packaging with GENET patches, and builds .deb packages.

Must be run on Debian trixie (or compatible) to satisfy build dependencies.

Produces:
  - qemu-rpi-system-arm_*.deb     (binary: qemu-rpi-system-aarch64)
  - qemu-rpi-system-data_*.deb    (firmware, keymaps, etc.)
  - qemu-rpi_*.dsc                (source package descriptor)
  - qemu-rpi_*.debian.tar.xz      (debian packaging)
  - qemu-rpi_*.orig.tar.xz        (upstream source)
  - qemu-rpi_*.changes            (upload metadata)

Usage: uv run ci/build-debs.py [--output-dir DIR]
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent.resolve()
REPO_ROOT = BASE.parent

# Import the patch-setup helper from this directory
sys.path.insert(0, str(BASE))
from debian_patches import setup_debian_patches

# Debian experimental's QEMU orig tarball - works correctly on Debian trixie
ORIG_TARBALL_URL = (
    "https://deb.debian.org/debian/pool/main/q/qemu/"
    "qemu_11.0.0~rc2+ds.orig.tar.xz"
)
ORIG_TARBALL_NAME = "qemu-rpi_11.0.0~rc2+ds.orig.tar.xz"
SOURCE_DIR_NAME = "qemu-rpi-11.0.0~rc2+ds"


def run(cmd, cwd=None, check=True):
    """Run a command, printing it first."""
    if isinstance(cmd, str):
        cmd = cmd.split()
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check)


def main():
    parser = argparse.ArgumentParser(description="Build QEMU RPi Debian packages")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "tmp" / "deb-output",
                        help="Directory for build output")
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    work_dir = REPO_ROOT / "tmp" / "deb-build"

    # Clean and create work directory
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Building QEMU RPi Debian packages")
    print("=" * 60)

    # Step 1: Download upstream tarball
    orig_tarball = work_dir / ORIG_TARBALL_NAME
    print(f"\n=== Downloading upstream tarball ===")
    if not orig_tarball.exists():
        run(["wget", "-q", "-O", str(orig_tarball), ORIG_TARBALL_URL])
    print(f"  {orig_tarball} ({orig_tarball.stat().st_size / 1e6:.1f} MB)")

    # Step 2: Extract upstream source
    source_dir = work_dir / SOURCE_DIR_NAME
    print(f"\n=== Extracting upstream source ===")
    run(["tar", "xf", str(orig_tarball), "-C", str(work_dir)])

    # The tarball extracts to qemu-11.0.0-rc2/ - rename to match our source package name
    extracted_dirs = [d for d in work_dir.iterdir()
                      if d.is_dir() and d.name.startswith("qemu")]
    if extracted_dirs and extracted_dirs[0] != source_dir:
        extracted_dirs[0].rename(source_dir)
    print(f"  Source: {source_dir}")

    # Step 3: Copy our debian/ directory
    print(f"\n=== Setting up debian/ packaging ===")
    debian_dst = source_dir / "debian"
    if debian_dst.exists():
        shutil.rmtree(debian_dst)
    shutil.copytree(BASE / "debian", debian_dst)

    # Step 4: Install our patches and regenerate the series file.
    # The series file is ALWAYS regenerated from the files on disk so
    # a stale checked-in series can never drop patches — this was the
    # root cause of fpgas-online/rpi-qemu#6.
    patches_src = BASE / "qemu-patches"
    n_patches = setup_debian_patches(debian_dst, patches_src)
    print(f"  Installed {n_patches} patches and regenerated debian/patches/series")

    # Step 5: Build source and binary packages
    print(f"\n=== Building packages ===")
    env = os.environ.copy()
    env["DEB_BUILD_OPTIONS"] = f"parallel={os.cpu_count() or 4} nocheck"

    # -d: skip dependency checking (deps installed by container/workflow)
    # -b: binary-only (no source package — version auto-generated from
    #     git describe doesn't match the orig tarball naming)
    run(["dpkg-buildpackage", "-us", "-uc", "-d", "-b"],
        cwd=source_dir, check=True)

    # Step 6: Collect output files
    print(f"\n=== Collecting output ===")
    for f in work_dir.glob("qemu-rpi*"):
        if f.is_file():
            dst = output_dir / f.name
            shutil.copy2(f, dst)
            size_mb = f.stat().st_size / 1e6
            print(f"  {f.name} ({size_mb:.1f} MB)")

    # Also copy the orig tarball
    orig_dst = output_dir / ORIG_TARBALL_NAME
    if not orig_dst.exists():
        shutil.copy2(orig_tarball, orig_dst)

    print(f"\n{'=' * 60}")
    print(f"Output: {output_dir}")
    print(f"{'=' * 60}")

    # Clean up work directory
    shutil.rmtree(work_dir, ignore_errors=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
