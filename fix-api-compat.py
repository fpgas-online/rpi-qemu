#!/usr/bin/env python3
"""
Apply QEMU v11 API compatibility fixes to the extracted patch files.

Fixes:
1. class_init signature: void *data -> const void *data
2. dc->reset = X -> device_class_set_legacy_reset(dc, X)
3. Remove DEFINE_PROP_END_OF_LIST()
4. Property arrays: static Property -> static const Property
5. Include paths: sysemu/dma.h -> system/dma.h
"""

import re
import sys
from pathlib import Path

EXTRACTED_DIR = Path("/home/tim/github/fpgas-online/rpi-qemu/extracted-files")

FIXES = [
    # 1. class_init signature
    (
        r'(static void \w+_class_init\(ObjectClass \*\w+,)\s*void \*data\)',
        r'\1 const void *data)',
        "class_init const void"
    ),
    # 2. dc->reset = X  ->  device_class_set_legacy_reset(dc, X)
    (
        r'(\w+)->reset\s*=\s*(\w+);',
        r'device_class_set_legacy_reset(\1, \2);',
        "device_class_set_legacy_reset"
    ),
    # 3. Remove DEFINE_PROP_END_OF_LIST()
    (
        r'\s*DEFINE_PROP_END_OF_LIST\(\),?\s*\n',
        '\n',
        "Remove DEFINE_PROP_END_OF_LIST"
    ),
    # 4. Property arrays: add const
    (
        r'static Property (\w+)\[\]',
        r'static const Property \1[]',
        "const Property array"
    ),
    # 5. Include paths
    (
        r'#include "sysemu/dma\.h"',
        '#include "system/dma.h"',
        "sysemu/dma.h -> system/dma.h"
    ),
    (
        r'#include "hw/sysbus\.h"',
        '#include "hw/core/sysbus.h"',
        "hw/sysbus.h -> hw/core/sysbus.h"
    ),
    (
        r'#include "hw/registerfields\.h"',
        '#include "hw/core/registerfields.h"',
        "hw/registerfields.h -> hw/core/registerfields.h"
    ),
    (
        r'#include "hw/qdev-properties\.h"',
        '#include "hw/core/qdev-properties.h"',
        "hw/qdev-properties.h -> hw/core/qdev-properties.h"
    ),
    (
        r'#include "hw/irq\.h"',
        '#include "hw/core/irq.h"',
        "hw/irq.h -> hw/core/irq.h"
    ),
]


def fix_file(filepath: Path) -> list[str]:
    """Apply all API fixes to a file. Returns list of fixes applied."""
    content = filepath.read_text()
    applied = []

    for pattern, replacement, description in FIXES:
        new_content = re.sub(pattern, replacement, content)
        if new_content != content:
            applied.append(description)
            content = new_content

    if applied:
        filepath.write_text(content)

    return applied


def main():
    total_fixes = 0

    for cfile in sorted(EXTRACTED_DIR.rglob("*.c")):
        fixes = fix_file(cfile)
        if fixes:
            relpath = cfile.relative_to(EXTRACTED_DIR)
            print(f"{relpath}:")
            for f in fixes:
                print(f"  - {f}")
            total_fixes += len(fixes)

    for hfile in sorted(EXTRACTED_DIR.rglob("*.h")):
        fixes = fix_file(hfile)
        if fixes:
            relpath = hfile.relative_to(EXTRACTED_DIR)
            print(f"{relpath}:")
            for f in fixes:
                print(f"  - {f}")
            total_fixes += len(fixes)

    print(f"\nTotal fixes applied: {total_fixes}")

    # Verify no old patterns remain
    print("\n=== Verification: checking for remaining old patterns ===")
    remaining = 0
    for f in sorted(EXTRACTED_DIR.rglob("*.[ch]")):
        content = f.read_text()
        relpath = f.relative_to(EXTRACTED_DIR)

        if "void *data)" in content and "class_init" in content:
            print(f"  WARNING: {relpath} still has non-const class_init")
            remaining += 1
        if re.search(r'\w+->reset\s*=\s*\w+;', content):
            print(f"  WARNING: {relpath} still has dc->reset assignment")
            remaining += 1
        if "DEFINE_PROP_END_OF_LIST" in content:
            print(f"  WARNING: {relpath} still has DEFINE_PROP_END_OF_LIST")
            remaining += 1
        if '"sysemu/dma.h"' in content:
            print(f"  WARNING: {relpath} still has old sysemu/dma.h include")
            remaining += 1

    if remaining == 0:
        print("  All old patterns removed successfully!")
    else:
        print(f"  {remaining} old patterns still found!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
