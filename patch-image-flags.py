#!/usr/bin/env python3
"""Patch arm64 Image header to set flags bit 3 (anywhere in physical memory)."""
import struct
import sys
from pathlib import Path

img = Path("/home/tim/github/fpgas-online/rpi-qemu/test-images/tftpboot/Image")
data = bytearray(img.read_bytes())

# arm64 Image header: flags at offset 0x30, 8 bytes LE
flags = struct.unpack_from("<Q", data, 0x30)[0]
print(f"Original flags: 0x{flags:x}")

# Set bit 3: kernel can be loaded anywhere in physical memory
flags |= (1 << 3)
struct.pack_into("<Q", data, 0x30, flags)
print(f"Patched flags: 0x{flags:x}")

img.write_bytes(data)
print(f"Patched {img}")
