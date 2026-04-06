# BCM2711 GENET Ethernet Controller Hardware Pitfalls

Source: https://forums.raspberrypi.com/viewtopic.php?t=349563

## Critical Hardware Issues

### Link Status Detection Failure

The link status change interrupt mechanism is non-functional. The interrupt bits
at `0x00000010` (LINK_UP) and `0x00000020` (LINK_DOWN) do not trigger properly.
Additionally, the UMAC Mode register at address `0x0844` cannot be read to obtain
link status -- that register does not respond to accesses.

The only viable workaround is periodic PHY register polling via MDIO.

### Write-Once Register Limitation

Several critical registers can only be written once after a hardware reset of the
BCM2711. Even controller-level reset commands do not restore write capability.

**Affected Write-Once Registers:**

| Offset | Register | Notes |
|--------|----------|-------|
| `0x00` | Receive Ring Write Pointer | Write-once after HW reset |
| `0x00` | Transmit Ring Read Pointer | Write-once after HW reset |
| `0x08` | Receive Ring Producer Index | Upper 16 bits (discard count) remain writable |
| `0x08` | Transmit Ring Consumer Index | Write-once after HW reset |
| `0x14` | Ring Start Address | Write-once after HW reset |
| `0x1C` | Ring End Address | Write-once after HW reset |

## Initialization Workaround Strategy

Since ring configuration registers are write-once, the following approach is required:

1. Attempt initial writes to all ring registers
2. Read back actual values (which may differ from intended values)
3. Store these hardware-confirmed values
4. Use shadow registers for re-initialization:
   - Copy Receive Ring Write Pointer -> Receive Ring Read Pointer
   - Copy Receive Ring Producer Index -> Receive Ring Consumer Index
   - Copy Transmit Ring Read Pointer -> Transmit Ring Write Pointer
   - Copy Transmit Ring Consumer Index -> Transmit Ring Producer Index

## Operational Constraints

Ring size and count modifications require a full BCM2711 reset. Reloading drivers
with different configurations without a system reboot poses serious risks including
data corruption or system hangs.

## DMA Link Recovery Challenges

Link disconnection requires explicit DMA halting and transmit FIFO flushing.
Without this, reconnection transmits corrupted data preceding the first packet --
particularly problematic for latency-sensitive applications (e.g., 1ms packet
intervals).

## Documentation Gaps

Neither Broadcom nor the Raspberry Pi Foundation have provided comprehensive GENET
controller specifications despite NDA arrangements. Driver developers must rely on:
- Linux kernel bcmgenet driver source code
- Reverse engineering and hardware experimentation
- Community-shared findings (such as the write-once register discovery)

## Implications for QEMU Emulation

These hardware quirks are important to consider when implementing a GENET emulator:
- The write-once register behavior may or may not need to be emulated depending on
  the target guest OS (Linux drivers may not depend on it)
- Link status interrupt non-functionality means guest drivers typically use PHY
  polling, simplifying the interrupt emulation requirements
- The UMAC Mode register at `0x0844` being non-responsive should be considered in
  register access emulation
