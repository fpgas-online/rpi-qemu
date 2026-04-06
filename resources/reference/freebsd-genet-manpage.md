# GENET(4) - FreeBSD Kernel Interfaces Manual

Source: https://man.freebsd.org/cgi/man.cgi?query=genet&sektion=4

## NAME

**genet** -- Broadcom BCM2711 Gigabit Ethernet controller driver

## SYNOPSIS

To compile this driver into the kernel, add these lines to the kernel configuration file:

```
device miibus
device genet
```

## DESCRIPTION

The **genet** driver supports the BCM2711 Ethernet controller found on Raspberry Pi 4 systems.

### Supported Features

- IP/TCP/UDP checksum offload for IPv4 and IPv6
- 10/100/1000Mbps operation in full-duplex mode
- 10/100Mbps operation in half-duplex mode

**Important Note:** The operation of transmit checksum offload is coupled for IPv4 and IPv6; to disable it, both must be disabled even if both address families are not in use.

### Media Types

- **autoselect** -- Enables automatic media type selection; manual override possible via rc.conf(5)
- **10baseT/UTP** -- 10Mbps operation with full-duplex or half-duplex modes
- **100baseTX** -- 100Mbps Fast Ethernet with duplex options
- **1000baseT** -- 1000Mbps operation (full-duplex only)

### Media Options

- **full-duplex** -- Force full duplex operation
- **half-duplex** -- Force half duplex operation

## HARDWARE

The **genet** driver supports the Ethernet controller portion of the Broadcom BCM2711 on the Raspberry Pi 4 Model B and related systems, utilizing the BCM54213PE PHY.

## LOADER TUNABLES

**hw.genet.rx_batch** -- The maximum number of packets to pass to the link-layer input routine at one time. Default: 16.

## SYSCTL VARIABLES

**hw.genet.tx_hdr_min** -- Controls bytes added to Ethernet header when first buffer contains only the header. Default: 56 bytes.

## DIAGNOSTICS

The driver typically runs without diagnostics. When the **debug** option is enabled via ifconfig(8), transmission and reception failures produce diagnostic messages that require source code review for interpretation.

## SEE ALSO

altq(4), arp(4), miibus(4), netintro(4), ng_ether(4), vlan(4), ifconfig(8)

## HISTORY

The **genet** device driver first appeared in FreeBSD 13.0.

## AUTHORS

Mike Karels authored the **genet** driver. Portions derive from NetBSD's bcmgenet driver (Jared McNeill) and the awg driver for Allwinner EMAC (Jared McNeill).
