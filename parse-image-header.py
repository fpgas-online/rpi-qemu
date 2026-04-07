#!/usr/bin/env python3
"""Parse ARM64 kernel Image header to understand booti behavior."""

import struct
import sys
from pathlib import Path

def parse_arm64_header(image_path):
    with open(image_path, "rb") as f:
        data = f.read(64)

    # ARM64 Image header per kernel docs
    code0 = struct.unpack_from("<I", data, 0)[0]
    code1 = struct.unpack_from("<I", data, 4)[0]
    text_offset = struct.unpack_from("<Q", data, 8)[0]
    image_size = struct.unpack_from("<Q", data, 16)[0]
    flags = struct.unpack_from("<Q", data, 24)[0]
    magic = struct.unpack_from("<I", data, 56)[0]

    print(f"File: {image_path}")
    print(f"code0:       0x{code0:08x}")
    print(f"code1:       0x{code1:08x}")
    print(f"text_offset: 0x{text_offset:016x}")
    print(f"image_size:  0x{image_size:016x} ({image_size} bytes, {image_size/1024/1024:.1f} MB)")
    print(f"flags:       0x{flags:016x}")
    print(f"  bit 0 (endian):  {flags & 1} (0=LE, 1=BE)")
    print(f"  bit 1-2 (page):  {(flags >> 1) & 3} (1=4K, 2=16K, 3=64K)")
    print(f"  bit 3 (phys):    {(flags >> 3) & 1} (0=anywhere, 1=must be at text_offset)")
    print(f"magic:       0x{magic:08x} (expected 0x644d5241 = ARMd)")

    if magic != 0x644d5241:
        print("WARNING: Magic mismatch! Not a valid ARM64 Image")

    # What booti will do
    print(f"\nbooti behavior:")
    print(f"  Kernel will be relocated to: DRAM_START + text_offset = 0x{text_offset:x}")
    print(f"  Kernel spans: 0x{text_offset:x} - 0x{text_offset + image_size:x}")

    actual_size = Path(image_path).stat().st_size
    print(f"  Actual file size: {actual_size} bytes ({actual_size/1024/1024:.1f} MB)")
    if image_size != actual_size:
        print(f"  NOTE: header image_size ({image_size}) != actual file size ({actual_size})")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "test-images/tftpboot/Image"
    parse_arm64_header(path)
