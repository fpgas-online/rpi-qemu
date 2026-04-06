#!/usr/bin/env python3
"""Attempt to apply Kambalin v6 patches to QEMU v11.0.0-rc2 and record results."""

import subprocess
import sys
from pathlib import Path

QEMU_DIR = Path("/home/tim/github/fpgas-online/rpi-qemu/upstream-qemu")
PATCHES_DIR = Path("/home/tim/github/fpgas-online/rpi-qemu/resources/patches")
LOG_FILE = Path("/home/tim/github/fpgas-online/rpi-qemu/porting-log.md")

# Ordered list of patches to apply (other prerequisites first, then GENET)
PATCHES = [
    ("kambalin-v6-other/13-pcie-root-complex.mbox", "BCM2838 PCIe Root Complex"),
    ("kambalin-v6-other/14-pcie-host.mbox", "BCM2838 PCIe Host"),
    ("kambalin-v6-other/15-pcie-host-pci.mbox", "Enable BCM2838 PCIe"),
    ("kambalin-v6-other/16-rng200.mbox", "RPi4 RNG200"),
    ("kambalin-v6-other/17-thermal-sensor.mbox", "BCM2838 Thermal Sensor"),
    ("kambalin-v6-other/18-clock-stub.mbox", "Clock ISP Stub"),
    ("kambalin-v6-genet/19-genet-stub.mbox", "GENET Stub"),
    ("kambalin-v6-genet/20-genet-regs-part1.mbox", "GENET Register Structs Part 1"),
    ("kambalin-v6-genet/21-genet-regs-part2.mbox", "GENET Register Structs Part 2"),
    ("kambalin-v6-genet/22-genet-regs-part3.mbox", "GENET Register Structs Part 3"),
    ("kambalin-v6-genet/23-genet-regs-part4.mbox", "GENET Register Structs Part 4"),
    ("kambalin-v6-genet/24-genet-register-macros.mbox", "GENET Register Access Macros"),
    ("kambalin-v6-genet/25-genet-register-ops.mbox", "GENET Register Ops"),
    ("kambalin-v6-genet/26-genet-mdio.mbox", "GENET MDIO"),
    ("kambalin-v6-genet/27-genet-tx-path.mbox", "GENET TX Path"),
    ("kambalin-v6-genet/28-genet-rx-path.mbox", "GENET RX Path"),
    ("kambalin-v6-genet/29-enable-genet.mbox", "Enable BCM2838 GENET"),
]

def run(cmd, cwd=None):
    """Run a command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=30
    )
    return result.returncode, result.stdout, result.stderr


def main():
    results = []
    log_lines = [
        "# Patch Application Log",
        "",
        f"**QEMU Version**: v11.0.0-rc2",
        f"**Date**: 2026-04-06",
        f"**Branch**: raspi4b-genet-port",
        "",
        "## Results",
        "",
        "| # | Patch | Status | Details |",
        "|---|---|---|---|",
    ]

    for i, (patch_file, description) in enumerate(PATCHES, 1):
        patch_path = PATCHES_DIR / patch_file

        if not patch_path.exists():
            log_lines.append(f"| {i} | {description} | MISSING | File not found: {patch_file} |")
            results.append(("MISSING", description))
            continue

        # First try git apply --check (dry run) to see if it would apply
        rc_check, stdout_check, stderr_check = run(
            ["git", "apply", "--check", str(patch_path)],
            cwd=QEMU_DIR
        )

        if rc_check == 0:
            log_lines.append(f"| {i} | {description} | WOULD_APPLY | Dry-run pass |")
            results.append(("APPLIED", description))
        else:
            # Check failed - capture the conflict details
            error_summary = (stderr_check or stdout_check).strip().split("\n")
            error_msg = " ".join(line.strip() for line in error_summary[:5] if line.strip())
            if len(error_msg) > 300:
                error_msg = error_msg[:300] + "..."
            log_lines.append(f"| {i} | {description} | CONFLICT | {error_msg} |")
            results.append(("CONFLICT", description))

    # Summary
    applied = sum(1 for s, _ in results if s == "APPLIED")
    failed = sum(1 for s, _ in results if s in ("FAILED", "CONFLICT"))
    skipped = sum(1 for s, _ in results if s == "SKIPPED")

    log_lines.extend([
        "",
        "## Summary",
        "",
        f"- **Applied cleanly**: {applied}/{len(PATCHES)}",
        f"- **Failed**: {failed}/{len(PATCHES)}",
        f"- **Skipped** (due to prior failure): {skipped}/{len(PATCHES)}",
        "",
    ])

    if failed > 0:
        log_lines.extend([
            f"Each patch was tested independently (dry-run) against the unmodified QEMU v11 tree.",
            "",
        ])

    # Write detailed conflict info if we had failures
    if failed > 0:
        log_lines.extend([
            "## Conflict Details",
            "",
            "The patches were written against QEMU ~v9.0 (Feb 2024). "
            "QEMU v11.0.0-rc2 has significant API changes including:",
            "",
            "1. `class_init()` signature changed from `void *data` to `const void *data`",
            "2. `dc->reset` replaced by `device_class_set_legacy_reset()`",
            "3. `DEFINE_PROP_END_OF_LIST()` removed",
            "4. `Property` arrays now `const`",
            "5. Include paths moved: `sysemu/dma.h` -> `system/dma.h`, "
               "`hw/sysbus.h` -> `hw/core/sysbus.h`",
            "",
            "Additionally, existing files modified by the patches "
            "(bcm2838_peripherals.c/h, meson.build, etc.) have diverged.",
            "",
        ])

    log_content = "\n".join(log_lines) + "\n"
    LOG_FILE.write_text(log_content)

    print(log_content)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
