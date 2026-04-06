#!/usr/bin/env python3
"""Decode the tight loop at 0x3bf470a0 from the QEMU exec trace."""

# From the earlier QEMU exec trace output:
# 0x3bf470a0: OBJD-T: 5f8f007140ecff54
# But that only covers 0x3bf470a0-0x3bf470a7 (2 instructions)
# We need to see what's at 0x3bf470a8 too

# The trace also showed:
# 0x3bf669b0: OBJD-T: 5f8f007140ecff54  (same pattern!)
# 0x3bf669b8: OBJD-T: cce4ff54

# These are from BEFORE relocation. After relocation, the same code
# is at 0x3bf470xx. The pattern 5f8f007140ecff54 is the comparison loop.

# Let me decode the full sequence. The trace showed patterns around
# 0x3bf470a0. Let me check: is this U-Boot's dcache_flush or
# cache_line_invalidate loop?

# Instructions:
# 0x3bf470a0: 0x71008f5f = SUBS WZR, W26, #0x23  (CMP W26, #35, LSL #12?)
# Wait, let me recalculate:
# 0x71008f5f in little-endian bytes: 5f 8f 00 71
# Binary: 0111 0001 0000 0000 1000 1111 0101 1111
# This is SUBS (immediate): sf=0(32bit) op=1 S=1 shift=00 imm12=000000100011=0x23=35
# Rn=11010=W26 Rd=11111=WZR
# So: CMP W26, #35

# 0x54ffec40 in LE bytes: 40 ec ff 54
# Binary: 0101 0100 1111 1111 1110 1100 0100 0000
# b.cond: 0101 0100 imm19 0 cond
# cond = 0000 = EQ
# imm19 = 11111111111101100010 = let me recalculate
# bits[23:5] of 0x54ffec40:
# 0x54ffec40 >> 5 = 0x2a7fff62
# imm19 = 0x2a7fff62 & 0x7FFFF = 0x7ff62
# Hmm that's positive. Let me redo:
# 0x54ffec40:
# bit 31-25: 0101010 (fixed for b.cond)
# bit 24: 0
# bits 23-5: imm19
# bits 4: 0
# bits 3-0: cond = 0000 = EQ

val = 0x54ffec40
imm19 = (val >> 5) & 0x7FFFF
if imm19 >= (1 << 18):
    imm19 -= (1 << 19)
offset = imm19 * 4
cond = val & 0xF
conds = ["eq","ne","cs","cc","mi","pl","vs","vc",
         "hi","ls","ge","lt","gt","le","al","nv"]

target = 0x3bf470a4 + offset  # b.cond is PC-relative from THIS instruction
print(f"CMP W26, #35")
print(f"B.{conds[cond]} 0x{target:x}  (offset={offset})")
print()

# If B.EQ doesn't branch (W26 != 35), execution falls through to 0x3bf470a8
# But PC is stuck at 0x3bf470a0, so either:
# 1. The instruction at 0x3bf470a8 branches back to 0x3bf470a0
# 2. The B.EQ IS taken (W26 == 35) and the target is a loop

if target == 0x3bf470a0:
    print("B.EQ branches back to 0x3bf470a0! This IS a 2-instruction tight loop.")
    print("W26 == 35, so B.EQ is always taken.")
    print()
    print("This is likely U-Boot stuck in a WFI-like polling loop")
    print("waiting for a hardware event that never occurs in QEMU.")
elif offset < 0:
    print(f"B.EQ branches backward to 0x{target:x}")
    print("The loop body is between 0x{:x} and 0x3bf470a4".format(target))
else:
    print(f"B.EQ branches forward to 0x{target:x}")
    print("If not taken, falls through to 0x3bf470a8")
