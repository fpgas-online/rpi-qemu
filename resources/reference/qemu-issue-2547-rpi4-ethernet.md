# QEMU GitLab Issue #2547: Raspberry 4B Ethernet Support

Source: https://gitlab.com/qemu-project/qemu/-/issues/2547

## Issue Title

Raspberry 4B Ethernet support

## Issue Description

### Goal

Implement Raspberry 4B with GENET Ethernet support in QEMU emulation.

### Technical Details

This is a feature request for adding BCM2711 GENET (Gigabit Ethernet) controller
emulation to QEMU's Raspberry Pi 4B machine model. The GENET controller is the
primary network interface on the RPi4B and is required for networking support in
the emulated machine.

### Related Work

A work-in-progress patch series addressing this feature exists at:
https://patchew.org/QEMU/20240226000259.2752893-1-sergey.kambalin@auriga.com/

The patch was submitted by Sergey Kambalin (Auriga) on February 26, 2024, as part
of broader BCM2838/RPi4B peripheral emulation work.

## Status

The issue remains open as a feature request, awaiting community contribution or
further patch development and review.

## Context

The RPi4B machine type (`raspi4b`) was added to QEMU but initially without many
of the BCM2711-specific peripherals. The GENET Ethernet controller is one of the
key missing peripherals needed for a more complete RPi4B emulation, particularly
for use cases requiring network connectivity.

Key technical considerations for GENET emulation include:
- BCM2711 GENET v5 controller with UniMAC
- DMA ring descriptors for TX/RX
- MDIO interface for BCM54213PE PHY
- Interrupt controller integration (IRQ0/IRQ1)
- Integration with QEMU's network backend infrastructure
