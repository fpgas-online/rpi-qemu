# Ultibo GENET Unit Technical Reference

Source: https://ultibo.org/wiki/Unit_GENET

## Overview

The Unit GENET provides a driver for "Broadcom GENET Gigabit Ethernet devices" including the BCM54213PE in Raspberry Pi 4B. The driver supports 10BASE-T, 100BASE-TX, and 1000BASE-T speeds with WOL and EEE features.

## Core Constants

### Device Configuration
```pascal
GENET_NETWORK_DESCRIPTION = 'Broadcom GENET (Gigabit Ethernet) controller';
GENET_MAX_TX_ENTRIES = SIZE_256;
GENET_MAX_RX_ENTRIES = SIZE_512;
GENET_MAX_PACKET_SIZE = 2048;
GENET_TOTAL_DESC = 256;
GENET_DESC_INDEX = 16;
```

### Version Constants
```pascal
GENET_V1 = 1;
GENET_V2 = 2;
GENET_V3 = 3;
GENET_V4 = 4;
GENET_V5 = 5;
```

### DMA Configuration
```pascal
DMA_MAX_BURST_LENGTH = $08;
DMA_FC_THRESH_HI = (GENET_TOTAL_DESC shr 4);
DMA_FC_THRESH_LO = 5;
```

## Register Block Offsets

```pascal
GENET_SYS_OFF = $0000;
GENET_GR_BRIDGE_OFF = $0040;
GENET_EXT_OFF = $0080;
GENET_INTRL2_0_OFF = $0200;
GENET_INTRL2_1_OFF = $0240;
GENET_RBUF_OFF = $0300;
GENET_UMAC_OFF = $0800;
```

## UniMAC Registers

### Command Register (UMAC_CMD @ $008)
```pascal
CMD_TX_EN = (1 shl 0);
CMD_RX_EN = (1 shl 1);
UMAC_SPEED_10 = 0;
UMAC_SPEED_100 = 1;
UMAC_SPEED_1000 = 2;
UMAC_SPEED_2500 = 3;
CMD_SPEED_SHIFT = 2;
CMD_SPEED_MASK = 3;
CMD_PROMISC = (1 shl 4);
CMD_PAD_EN = (1 shl 5);
CMD_CRC_FWD = (1 shl 6);
```

### MDIO Command Register (UMAC_MDIO_CMD @ $614)
```pascal
MDIO_START_BUSY = (1 shl 29);
MDIO_READ_FAIL = (1 shl 28);
MDIO_RD = (2 shl 26);
MDIO_WR = (1 shl 26);
MDIO_PMD_SHIFT = 21;
MDIO_PMD_MASK = $1F;
MDIO_REG_SHIFT = 16;
MDIO_REG_MASK = $1F;
```

## Interrupt Definitions

### IRQ0 Bits (UniMAC INTRL2 IRQ0)
```pascal
UMAC_IRQ0_SCB = (1 shl 0);
UMAC_IRQ0_EPHY = (1 shl 1);
UMAC_IRQ0_PHY_DET_R = (1 shl 2);
UMAC_IRQ0_PHY_DET_F = (1 shl 3);
UMAC_IRQ0_LINK_UP = (1 shl 4);
UMAC_IRQ0_LINK_DOWN = (1 shl 5);
UMAC_IRQ0_UMAC = (1 shl 6);
UMAC_IRQ0_TBUF_UNDERRUN = (1 shl 8);
UMAC_IRQ0_RBUF_OVERFLOW = (1 shl 9);
UMAC_IRQ0_RXDMA_MBDONE = (1 shl 13);
UMAC_IRQ0_TXDMA_MBDONE = (1 shl 16);
UMAC_IRQ0_MDIO_DONE = (1 shl 23);
UMAC_IRQ0_MDIO_ERROR = (1 shl 24);
```

### IRQ1 Bits
```pascal
UMAC_IRQ1_TX_INTR_MASK = $FFFF;
UMAC_IRQ1_RX_INTR_MASK = $FFFF;
UMAC_IRQ1_RX_INTR_SHIFT = 16;
```

## RX/TX DMA Descriptors

### Common Descriptor Bits
```pascal
DMA_OWN = $8000;
DMA_EOP = $4000;
DMA_SOP = $2000;
DMA_WRAP = $1000;
DMA_BUFLENGTH_MASK = $0fff;
DMA_BUFLENGTH_SHIFT = 16;
```

### TX Specific Bits
```pascal
DMA_TX_UNDERRUN = $0200;
DMA_TX_APPEND_CRC = $0040;
DMA_TX_OW_CRC = $0020;
DMA_TX_DO_CSUM = $0010;
DMA_TX_QTAG_SHIFT = 7;
```

### RX Specific Bits
```pascal
DMA_RX_CHK_V3PLUS = $8000;
DMA_RX_CHK_V12 = $1000;
DMA_RX_BRDCAST = $0040;
DMA_RX_MULT = $0020;
DMA_RX_CRC_ERROR = $0002;
DMA_RX_OV = $0001;
```

## DMA Ring Registers

```pascal
DMA_RING_SIZE = $40;
DMA_RINGS_SIZE = (DMA_RING_SIZE * (GENET_DESC_INDEX + 1));
DMA_RING_SIZE_MASK = $FFFF;
DMA_RING_SIZE_SHIFT = 16;
DMA_RW_POINTER_MASK = $1FF;
```

## RX/TX Ring Register Indices
```pascal
TDMA_READ_PTR = 0;
TDMA_READ_PTR_HI = 1;
TDMA_CONS_INDEX = 2;
TDMA_PROD_INDEX = 3;
DMA_RING_BUF_SIZE = 4;
DMA_START_ADDR = 5;
DMA_START_ADDR_HI = 6;
DMA_END_ADDR = 7;
DMA_END_ADDR_HI = 8;
TDMA_WRITE_PTR = 11;
TDMA_WRITE_PTR_HI = 12;
```

## Key Type Definitions

### TGENETNetwork Record
Core network device structure containing:
- **Network Properties:** Base network device
- **Interrupt Handlers:** IRQ0, IRQ1 management
- **Device Access:** Address, Lock (spin lock for interrupt safety)
- **Hardware State:** Version, PhyRevision, Link status
- **Hardware Parameters:** Queue counts, descriptor counts (version-specific)
- **DMA Parameters:** Register arrays and offsets
- **PHY Parameters:** Identifier, address, mode, flags, speed/duplex state
- **RX/TX Parameters:** Ring structures and control block arrays

### TGENETRXRing Record
```pascal
Network: PGENETNetwork;          // Owner reference
Worker: TWorkerHandle;            // Service worker
Index: LongWord;                  // Ring index
Size: LongWord;                   // Ring size
Consumer: LongWord;               // Last consumer index
Read: LongWord;                   // Read pointer
First, Last: LongWord;            // Descriptor bounds
ControlBlocks: PGENETControlBlocks;
IntEnable, IntDisable: Callbacks;
```

### TGENETTXRing Record
```pascal
Network: PGENETNetwork;
Worker: TWorkerHandle;
Index, Queue: LongWord;           // Ring and queue indices
Size: LongWord;
Clean, Consumer: LongWord;        // Cleanup and consumption tracking
Free: LongWord;                   // Free descriptor count
Write, Producer: LongWord;        // Write and producer pointers
First, Last: LongWord;
ControlBlocks: PGENETControlBlocks;
IntEnable, IntDisable: Callbacks;
```

### TGENETStatus64 Record
Extended DMA status structure with:
- LengthStatus, ExtendedStatus fields
- RXChecksum, TXChecksumInfo fields
- Padding for alignment

## Function Declarations

### Initialization
```pascal
procedure GENETInit;
```
Initialize unit and parameters (internal use).

### Network Device Management
```pascal
function GENETNetworkCreate(Address: PtrUInt; MDIOOffset: LongWord; 
                           IRQ0, IRQ1: LongWord): PNetworkDevice;
function GENETNetworkDestroy(Network: PNetworkDevice): LongWord;
```

### Network Operations
```pascal
function GENETNetworkOpen(Network: PNetworkDevice): LongWord;
function GENETNetworkClose(Network: PNetworkDevice): LongWord;
function GENETNetworkControl(Network: PNetworkDevice; Request: Integer;
                            Argument1: PtrUInt; var Argument2: PtrUInt): LongWord;
```

### Buffer Management
```pascal
function GENETBufferAllocate(Network: PNetworkDevice; 
                            var Entry: PNetworkEntry): LongWord;
function GENETBufferRelease(Network: PNetworkDevice; 
                           Entry: PNetworkEntry): LongWord;
function GENETBufferReceive(Network: PNetworkDevice; 
                           var Entry: PNetworkEntry): LongWord;
function GENETBufferTransmit(Network: PNetworkDevice; 
                            Entry: PNetworkEntry): LongWord;
```

### Hardware Configuration
```pascal
function GENETGetHardwareParameters(Network: PGENETNetwork): LongWord;
function GENETGetMACAddress(Network: PGENETNetwork; 
                           Address: PHardwareAddress): LongWord;
function GENETSetMACAddress(Network: PGENETNetwork; 
                           Address: PHardwareAddress): LongWord;
procedure GENETPowerUp(Network: PGENETNetwork; Mode: LongWord);
function GENETPowerDown(Network: PGENETNetwork; Mode: LongWord): LongWord;
```

### UMAC/DMA Operations
```pascal
procedure GENETResetUMAC(Network: PGENETNetwork);
procedure GENETInitUMAC(Network: PGENETNetwork);
function GENETInitializeDMA(Network: PGENETNetwork): LongWord;
function GENETFinalizeDMA(Network: PGENETNetwork): LongWord;
function GENETShutdownDMA(Network: PGENETNetwork): LongWord;
function GENETDisableDMA(Network: PGENETNetwork; FlushRX: Boolean): LongWord;
procedure GENETEnableDMA(Network: PGENETNetwork; DMAControl: LongWord);
```

### Queue Initialization
```pascal
function GENETInitRXQueues(Network: PGENETNetwork): LongWord;
procedure GENETInitTXQueues(Network: PGENETNetwork);
function GENETInitRXRing(Network: PGENETNetwork; Index, Size, First, Last: LongWord): LongWord;
procedure GENETInitTXRing(Network: PGENETNetwork; Index, Size, First, Last: LongWord);
```

### Buffer Allocation
```pascal
function GENETAllocRXBuffers(Network: PGENETNetwork; 
                            Ring: PGENETRXRing): LongWord;
procedure GENETFreeRXBuffers(Network: PGENETNetwork);
```

### Hardware Filtering
```pascal
procedure GENETHFBInit(Network: PGENETNetwork);
procedure GENETHFBClear(Network: PGENETNetwork);
```

### Interface Control
```pascal
procedure GENETInterfaceStart(Network: PGENETNetwork);
procedure GENETInterfaceStop(Network: PGENETNetwork);
```

### PHY/MII Operations
```pascal
function GENETMIIProbe(Network: PGENETNetwork): LongWord;
procedure GENETMIISetup(Network: PGENETNetwork);
function GENETMIIConfig(Network: PGENETNetwork): LongWord;
function GENETMIIWait(Network: PGENETNetwork): LongWord;
function GENETPhyReadStatus(Network: PGENETNetwork): LongWord;
```

### MDIO Bus Operations
```pascal
procedure UniMACMDIOStart(Network: PGENETNetwork);
function UniMACMDIOBusy(Network: PGENETNetwork): LongWord;
function UniMACMDIOPoll(Network: PGENETNetwork): LongWord;
function UniMACMDIORead(Network: PGENETNetwork; Reg: LongWord; 
                       var Value: Word): LongWord;
function UniMACMDIOWrite(Network: PGENETNetwork; Reg: LongWord; 
                        Value: Word): LongWord;
function UniMACMDIOReset(Network: PGENETNetwork): LongWord;
```

## Broadcom PHY Constants

### PHY IDs
```pascal
PHY_ID_BCM54213PE = $600d84a0;  // Raspberry Pi 4B PHY
PHY_ID_BCM54810 = $03625d00;
PHY_ID_BCM5482 = $0143bcb0;
PHY_ID_BCM57780 = $03625d90;
PHY_ID_MASK = $fffffff0;
```

### PHY Flags
```pascal
PHY_BCM_FLAGS_MODE_COPPER = $00000001;
PHY_BCM_FLAGS_MODE_1000BX = $00000002;
PHY_BCM_FLAGS_INTF_SGMII = $00000010;
PHY_BRCM_WIRESPEED_ENABLE = $00000100;
PHY_BRCM_AUTO_PWRDWN_ENABLE = $00000200;
```

## Broadcom PHY Register Definitions

### Extended Control Register (MII_BCM54XX_ECR = $10)
```pascal
MII_BCM54XX_ECR_IM = $1000;  // Interrupt mask
MII_BCM54XX_ECR_IF = $0800;  // Interrupt force
```

### Interrupt Registers
```pascal
MII_BCM54XX_ISR = $1a;  // Status register
MII_BCM54XX_IMR = $1b;  // Mask register
MII_BCM54XX_INT_LINK = $0002;
MII_BCM54XX_INT_SPEED = $0004;
MII_BCM54XX_INT_DUPLEX = $0008;
MII_BCM54XX_INT_ANPR = $0400;
```

## Public Variables
```pascal
GENET_PHY_MODE: String;
GENET_PHY_ADDR: LongWord;
GENET_SKIP_UMAC_RESET: Boolean;
GENET_NO_PHY_INTERRUPT: Boolean;
```

## Implementation Notes

**Lock Requirements:** All GENET helper functions require caller to hold the network lock, a spin lock used by interrupt handlers.

**Queue Architecture:** 
- Queues 0-15 support priority-based operation with hardware filtering
- Queue 16 serves as default RX/TX queue
- TX queues 0-3 have 32 descriptors each; queue 16 has remainder (128 descriptors)

**DMA Descriptor Pool:** Total 256 descriptors shared between RX and TX operations.

**Power Modes:** CABLE_SENSE, PASSIVE, and WOL_MAGIC supported.
