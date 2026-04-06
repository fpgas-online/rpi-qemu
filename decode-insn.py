#!/usr/bin/env python3
"""Decode the stuck instructions from the QEMU trace."""
import struct

# From earlier trace: 0x3bf470a0: OBJD-T: 5f8f007140ecff54
# And: 0x3bf470a8: OBJD-T: df021a6b41fcff54
data = bytes.fromhex("5f8f007140ecff54")
insn1 = struct.unpack("<I", data[0:4])[0]
insn2 = struct.unpack("<I", data[4:8])[0]
print(f"0x3bf470a0: 0x{insn1:08x}")
print(f"0x3bf470a4: 0x{insn2:08x}")

# Decode insn2: looks like a conditional branch (b.cond)
# ARM64 b.cond format: 0101 0100 imm19 0 cond
if (insn2 & 0xFF000010) == 0x54000000:
    imm19 = (insn2 >> 5) & 0x7FFFF
    if imm19 & 0x40000:
        imm19 -= 0x80000
    offset = imm19 * 4
    cond = insn2 & 0xF
    conds = ["eq","ne","cs","cc","mi","pl","vs","vc",
             "hi","ls","ge","lt","gt","le","al","nv"]
    target = 0x3bf470a4 + offset
    print(f"  -> b.{conds[cond]} 0x{target:x}")
    if target == 0x3bf470a0:
        print("  THIS IS A TIGHT BRANCH BACK TO ITSELF! (2-instruction loop)")

# Decode insn1: CMP or similar
# 0x7100 8f5f = 0111 0001 0000 0000 1000 1111 0101 1111
# This looks like: cmp w26, #0x23 or subs wzr, w26, #0x23
# Actually 0x5f8f0071 in LE = 0x71008f5f
# 0111 0001 0000 0000 1000 1111 0101 1111
# Encoding: SUBS Wd, Wn, #imm12
# op=01 S=1 shift=00 imm12=000010001111=0x8F=143 Rn=11010=x26 Rd=11111=xzr
print(f"  -> subs wzr, w26, #0x{0x8F}  (cmp w26, #143)")
print()
print("U-Boot is stuck in a loop comparing W26 to 143.")
print("This is likely a timeout loop waiting for a hardware event")
print("(e.g., cache flush, DMA completion, or PSCI call).")
print()
print("W26 probably holds a counter or status value that never reaches 143.")
