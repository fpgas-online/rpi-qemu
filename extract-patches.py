#!/usr/bin/env python3
"""
Extract new file contents from the Kambalin v6 mbox patches and apply
them sequentially to reconstruct the final state of each new file.

This handles patches that create new files and patches that incrementally
modify those files. It does NOT handle patches that modify existing QEMU
files (those need manual merging).
"""

import re
import subprocess
import sys
from pathlib import Path

QEMU_DIR = Path("/home/tim/github/fpgas-online/rpi-qemu/upstream-qemu")
PATCHES_DIR = Path("/home/tim/github/fpgas-online/rpi-qemu/resources/patches")
OUTPUT_DIR = Path("/home/tim/github/fpgas-online/rpi-qemu/extracted-files")

# All patches in application order
ALL_PATCHES = [
    "kambalin-v6-other/13-pcie-root-complex.mbox",
    "kambalin-v6-other/14-pcie-host.mbox",
    "kambalin-v6-other/15-pcie-host-pci.mbox",
    "kambalin-v6-other/16-rng200.mbox",
    "kambalin-v6-other/17-thermal-sensor.mbox",
    "kambalin-v6-other/18-clock-stub.mbox",
    "kambalin-v6-genet/19-genet-stub.mbox",
    "kambalin-v6-genet/20-genet-regs-part1.mbox",
    "kambalin-v6-genet/21-genet-regs-part2.mbox",
    "kambalin-v6-genet/22-genet-regs-part3.mbox",
    "kambalin-v6-genet/23-genet-regs-part4.mbox",
    "kambalin-v6-genet/24-genet-register-macros.mbox",
    "kambalin-v6-genet/25-genet-register-ops.mbox",
    "kambalin-v6-genet/26-genet-mdio.mbox",
    "kambalin-v6-genet/27-genet-tx-path.mbox",
    "kambalin-v6-genet/28-genet-rx-path.mbox",
    "kambalin-v6-genet/29-enable-genet.mbox",
]

# Files that are CREATED by the patches (not pre-existing in QEMU)
NEW_FILES = {
    "hw/arm/bcm2838_pcie.c",
    "include/hw/arm/bcm2838_pcie.h",
    "hw/misc/bcm2838_rng200.c",
    "include/hw/misc/bcm2838_rng200.h",
    "hw/misc/bcm2838_thermal.c",
    "include/hw/misc/bcm2838_thermal.h",
    "hw/net/bcm2838_genet.c",
    "include/hw/net/bcm2838_genet.h",
}


def extract_diff_from_mbox(mbox_path: Path) -> str:
    """Extract the diff portion from an mbox file."""
    content = mbox_path.read_text(errors="replace")
    # Find the start of the diff (first "diff --git" line)
    match = re.search(r'^diff --git ', content, re.MULTILINE)
    if not match:
        return ""
    return content[match.start():]


def apply_patches_to_tmpdir():
    """
    Create a temporary git repo, apply all patches sequentially,
    then extract the new files.
    """
    import tempfile
    import shutil

    tmpdir = Path(tempfile.mkdtemp(prefix="qemu-patch-extract-"))
    print(f"Working in temporary directory: {tmpdir}")

    try:
        # Initialize a git repo with the current QEMU state for new-file paths
        subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "extract@local"],
            cwd=tmpdir, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Extract"],
            cwd=tmpdir, capture_output=True
        )

        # Copy the files that patches modify from QEMU
        files_to_copy = [
            "hw/arm/bcm2838_peripherals.c",
            "include/hw/arm/bcm2838_peripherals.h",
            "hw/arm/bcm2838.c",
            "hw/arm/raspi4b.c",
            "hw/arm/meson.build",
            "hw/net/meson.build",
            "hw/misc/meson.build",
            "hw/net/trace-events",
            "hw/misc/trace-events",
            "hw/misc/bcm2835_property.c",
            "docs/system/arm/raspi.rst",
            "include/hw/arm/raspi_platform.h",
            "include/hw/arm/bcm2835_peripherals.h",
            "hw/arm/bcm2836.c",
            "include/hw/arm/bcm2836.h",
            "hw/gpio/meson.build",
        ]

        for f in files_to_copy:
            src = QEMU_DIR / f
            dst = tmpdir / f
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        # Initial commit
        subprocess.run(["git", "add", "-A"], cwd=tmpdir, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial QEMU v11 base"],
            cwd=tmpdir, capture_output=True
        )

        # Now apply each patch
        for patch_file in ALL_PATCHES:
            patch_path = PATCHES_DIR / patch_file
            if not patch_path.exists():
                print(f"  SKIP (missing): {patch_file}")
                continue

            diff_content = extract_diff_from_mbox(patch_path)
            if not diff_content:
                print(f"  SKIP (no diff): {patch_file}")
                continue

            # Write diff to temp file
            diff_file = tmpdir / "current.patch"
            diff_file.write_text(diff_content)

            # Try applying with --reject to get partial results
            result = subprocess.run(
                ["git", "apply", "--reject", str(diff_file)],
                cwd=tmpdir, capture_output=True, text=True
            )

            if result.returncode == 0:
                print(f"  APPLIED: {patch_file}")
            else:
                # Count what applied vs rejected
                applied_files = []
                rejected_files = []
                for line in result.stderr.split("\n"):
                    if "Applied patch" in line:
                        applied_files.append(line)
                    elif "patch does not apply" in line or "Rejected" in line:
                        rejected_files.append(line)
                print(f"  PARTIAL: {patch_file} ({len(applied_files)} applied, {len(rejected_files)} rejected)")
                if rejected_files:
                    for r in rejected_files[:3]:
                        print(f"    -> {r.strip()}")

            # Clean up .rej files
            for rej in tmpdir.rglob("*.rej"):
                rej.unlink()

            # Stage whatever applied
            subprocess.run(["git", "add", "-A"], cwd=tmpdir, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", f"Apply {patch_file}", "--allow-empty"],
                cwd=tmpdir, capture_output=True
            )

        # Now extract the new files
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        extracted = []
        for new_file in sorted(NEW_FILES):
            src = tmpdir / new_file
            if src.exists():
                dst = OUTPUT_DIR / new_file
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                size = dst.stat().st_size
                extracted.append((new_file, size))
                print(f"  EXTRACTED: {new_file} ({size} bytes)")
            else:
                print(f"  MISSING: {new_file}")

        print(f"\nExtracted {len(extracted)} files to {OUTPUT_DIR}")
        return extracted

    finally:
        shutil.rmtree(tmpdir)


def main():
    extracted = apply_patches_to_tmpdir()

    if not extracted:
        print("ERROR: No files extracted!")
        return 1

    print("\n=== Extracted Files ===")
    for path, size in extracted:
        print(f"  {path}: {size} bytes")

    return 0


if __name__ == "__main__":
    sys.exit(main())
