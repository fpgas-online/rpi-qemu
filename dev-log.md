# Development Log: Single-Instance U-Boot → TFTP → booti → Linux → Internet

## 2026-04-07: Stage 1 - Reproduce and Characterize booti Failure

### Starting State
- QEMU v11.0.0-rc2 with GENET patches (custom build)
- U-Boot 2026.04-rc5 (rpi_4_defconfig)
- Linux 6.18.20-v8+ (stock RPi kernel)
- Previous test used two separate QEMU instances as workaround
- `booti` reportedly hangs - cause unknown

### Observations from Previous boot.scr.txt
- Used compressed `kernel8.img` at 0x80000 with DTB at 0x2600000, initrd at 0x2700000
- Potential memory overlap: compressed kernel decompresses to ~28MB starting at text_offset
- Used `console=ttyAMA0` - may be wrong device for this kernel (working direct-boot uses ttyAMA1)
- Uncompressed `Image` (28MB) already available in tftpboot

### Diagnosis Results

**Root cause identified via QEMU monitor:**
- PC stuck at `0x3bf470a4` (inside `get_ticks()` called from `__udelay()`)
- CPU at 100% - confirmed tight busy-wait loop
- The `__udelay()` function WORKS (counter advances, loop converges for each call)
- BUT something during `image_setup_libfdt()` calls `__udelay()` many times
- In QEMU TCG mode, each __udelay call is MUCH slower than on real hardware
- because the virtual clock advances faster than instruction execution
- Result: a few milliseconds of guest delays = many seconds of host wall time

**Not the FSL erratum:** `.config` confirms `CONFIG_SYS_FSL_ERRATUM_A008585` is NOT set.  
The simple `timer_read_counter()` is active (single read, no stability loop).

**Previous "timer hang" diagnosis was partially correct:** The original diagnosis blamed `timer_read_counter()` loop, but the REAL issue is that __udelay is called many times during boot prep, and each call is slow in TCG mode.

### Fix Strategy
Try QEMU `-icount` mode to make virtual time advance at a fixed, fast rate per instruction.
This should make __udelay complete quickly. Verify networking still works with icount.
