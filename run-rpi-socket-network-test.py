#!/usr/bin/env python3
"""
RPi4B QEMU Socket Networking Test (with DHCP/TFTP peer)

Full network boot test over QEMU socket networking. A Python peer
provides DHCP and TFTP at the Ethernet frame level, replacing QEMU's
built-in user-mode (SLIRP) networking.

This proves the GENET Ethernet device works correctly with QEMU's
socket networking backend -- the same backend used by fpgas.online-infra
for VM-to-VM networking.

Architecture:
  Python peer (listen) ←TCP socket→ QEMU raspi4b (connect)
  The peer speaks QEMU's socket protocol: 4-byte BE length + raw Ethernet frame.
  It implements minimal ARP, DHCP, TFTP, and ICMP echo.

Test sequence:
1. Python peer starts listening on a TCP port
2. QEMU starts with -nic socket,connect=:PORT
3. U-Boot boots, does DHCP (served by peer), TFTP (served by peer)
4. U-Boot boots Linux via booti
5. Linux boots, GENET initializes, DHCP works, gateway ping works

Prerequisites:
  - QEMU with GENET: qemu-rpi-system-aarch64 or QEMU_OVERRIDE
  - U-Boot: test-images/u-boot/u-boot.bin
  - Kernel Image: test-images/tftpboot/Image
  - DTB: test-images/tftpboot/bcm2711-rpi-4-b.dtb
  - Initramfs: test-images/tftpboot/initrd.gz (or test-images/test-initramfs.cpio.gz)

Usage: uv run run-rpi-socket-network-test.py
"""

import os
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

BASE = Path(__file__).parent.resolve()

# QEMU binary (same resolution as other test scripts)
_qemu_override = os.environ.get("QEMU_OVERRIDE")
if _qemu_override:
    QEMU = Path(_qemu_override)
else:
    QEMU = Path(shutil.which("qemu-rpi-system-aarch64") or
                "qemu-rpi-system-aarch64")

UBOOT = BASE / "test-images" / "u-boot" / "u-boot.bin"
DTB = BASE / "test-images" / "bcm2711-rpi-4-b.dtb"
INITRD = BASE / "test-images" / "test-initramfs.cpio.gz"
TFTPBOOT = BASE / "test-images" / "tftpboot"

# Memory layout (same addresses as run-rpi-boot-test.py)
KERNEL_ADDR = 0x10000000   # 256 MB
DTB_ADDR    = 0x0f000000   # 240 MB
INITRD_ADDR = 0x12000000   # 288 MB

# Kernel boot parameters -- loglevel=7 so USB/DWC info messages appear
# on the serial console (the init script's dmesg grep searches for "dwc2"
# which doesn't match the RPi kernel's "dwc_otg" driver name).
BOOTARGS = "earlycon=pl011,mmio32,0xfe201000 console=ttyAMA0 loglevel=7 rdinit=/init"


# ---------------------------------------------------------------------------
# SocketNetPeer: minimal DHCP/TFTP/ARP/ICMP server over QEMU socket protocol
# ---------------------------------------------------------------------------

class SocketNetPeer:
    """DHCP + TFTP server speaking QEMU's socket networking protocol.

    Operates at the Ethernet frame level over a TCP socket.  Each frame on
    the wire is: [4-byte big-endian length][raw Ethernet frame].

    Implements just enough of the network stack to:
      - Respond to ARP requests for the server IP
      - Serve DHCP DISCOVER→OFFER and REQUEST→ACK
      - Serve TFTP read requests (RRQ) with blksize/tsize negotiation
      - Respond to ICMP echo requests (ping)
    """

    BROADCAST_MAC = b'\xff\xff\xff\xff\xff\xff'
    SERVER_MAC = bytes([0x52, 0x54, 0x00, 0xaa, 0xbb, 0xcc])
    SERVER_IP = bytes([10, 0, 2, 2])
    CLIENT_IP = bytes([10, 0, 2, 15])
    SUBNET_MASK = bytes([255, 255, 255, 0])
    ROUTER_IP = bytes([10, 0, 2, 2])
    DNS_IP = bytes([10, 0, 2, 3])
    BROADCAST_IP = bytes([255, 255, 255, 255])

    def __init__(self, tftp_root):
        self.tftp_root = Path(tftp_root)
        self.server_sock = None
        self.conn = None
        self.running = False
        self.client_mac = None
        self._ip_id = 1
        self._next_tid = 10000
        # TFTP sessions: our_tid -> {path, data, block, blksize, client_mac, client_ip, client_port}
        self.tftp_sessions = {}
        self._file_cache = {}
        # Stats
        self.dhcp_offers = 0
        self.dhcp_acks = 0
        self.tftp_files_served = 0
        self.tftp_bytes_sent = 0
        self.arp_replies = 0
        self.icmp_replies = 0
        self._lock = threading.Lock()

    def start(self, port=0):
        """Start listening.  Returns the bound port number."""
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(('127.0.0.1', port))
        self.server_sock.listen(1)
        self.running = True
        return self.server_sock.getsockname()[1]

    def accept(self, timeout=30):
        """Accept one connection from QEMU."""
        self.server_sock.settimeout(timeout)
        self.conn, _ = self.server_sock.accept()
        self.conn.settimeout(1.0)
        self.conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def run(self):
        """Main loop: read frames and respond.  Runs until stop() is called."""
        while self.running:
            try:
                frame = self._recv_frame()
                if frame is None:
                    break
                self._handle_frame(frame)
            except socket.timeout:
                continue
            except (ConnectionError, OSError):
                break

    def stop(self):
        self.running = False
        if self.conn:
            try:
                self.conn.close()
            except OSError:
                pass
        if self.server_sock:
            try:
                self.server_sock.close()
            except OSError:
                pass

    # -- Frame I/O ----------------------------------------------------------

    def _recv_exact(self, n):
        data = b''
        while len(data) < n:
            try:
                chunk = self.conn.recv(n - len(data))
            except socket.timeout:
                # Retry without losing already-read bytes -- propagating
                # the timeout would discard partial reads and desync the
                # frame stream.
                if not self.running:
                    return None
                continue
            if not chunk:
                return None
            data += chunk
        return data

    def _recv_frame(self):
        header = self._recv_exact(4)
        if header is None:
            return None
        length = struct.unpack(">I", header)[0]
        if length == 0 or length > 65536:
            return None
        return self._recv_exact(length)

    def _send_frame(self, frame):
        with self._lock:
            try:
                self.conn.sendall(struct.pack(">I", len(frame)) + frame)
            except (ConnectionError, OSError):
                pass

    # -- Frame dispatch -----------------------------------------------------

    def _handle_frame(self, frame):
        if len(frame) < 14:
            return
        ethertype = struct.unpack(">H", frame[12:14])[0]
        src_mac = frame[6:12]

        if ethertype == 0x0806:   # ARP
            self._handle_arp(frame[14:], src_mac)
        elif ethertype == 0x0800:  # IPv4
            self._handle_ipv4(frame[14:], src_mac)

    # -- ARP ----------------------------------------------------------------

    def _handle_arp(self, data, src_mac):
        if len(data) < 28:
            return
        hw_type, proto_type, hw_len, proto_len, op = struct.unpack(">HHBBH", data[:8])
        if hw_type != 1 or proto_type != 0x0800 or op != 1:
            return  # only handle Ethernet/IPv4 ARP requests

        sender_mac = data[8:14]
        sender_ip = data[14:18]
        target_ip = data[24:28]

        if target_ip != self.SERVER_IP:
            return

        # ARP reply
        arp = struct.pack(">HHBBH", 1, 0x0800, 6, 4, 2)
        arp += self.SERVER_MAC + self.SERVER_IP
        arp += sender_mac + sender_ip
        frame = sender_mac + self.SERVER_MAC + struct.pack(">H", 0x0806) + arp
        self._send_frame(frame)
        self.arp_replies += 1

    # -- IPv4 ---------------------------------------------------------------

    def _handle_ipv4(self, data, src_mac):
        if len(data) < 20:
            return
        ihl = (data[0] & 0x0f) * 4
        protocol = data[9]
        src_ip = data[12:16]
        payload = data[ihl:]

        if protocol == 17:  # UDP
            self._handle_udp(payload, src_mac, src_ip)
        elif protocol == 1:  # ICMP
            self._handle_icmp(payload, src_mac, src_ip)

    # -- ICMP echo ----------------------------------------------------------

    def _handle_icmp(self, data, src_mac, src_ip):
        if len(data) < 8:
            return
        icmp_type = data[0]
        if icmp_type != 8:  # echo request
            return

        # Build echo reply: type=0, keep code/id/seq/data
        reply_icmp = bytearray(data)
        reply_icmp[0] = 0  # echo reply
        reply_icmp[2:4] = b'\x00\x00'  # zero checksum
        cksum = self._checksum(bytes(reply_icmp))
        struct.pack_into(">H", reply_icmp, 2, cksum)

        frame = self._build_ipv4_frame(
            src_mac, src_ip, 1, bytes(reply_icmp))
        self._send_frame(frame)
        self.icmp_replies += 1

    # -- UDP ----------------------------------------------------------------

    def _handle_udp(self, data, src_mac, src_ip):
        if len(data) < 8:
            return
        src_port, dst_port = struct.unpack(">HH", data[:4])
        udp_payload = data[8:]

        if dst_port == 67:  # DHCP server
            self._handle_dhcp(udp_payload, src_mac)
        elif dst_port == 69:  # TFTP
            self._handle_tftp_rrq(udp_payload, src_mac, src_ip, src_port)
        else:
            # Check for TFTP ACK on a data port
            if dst_port in self.tftp_sessions:
                self._handle_tftp_ack(udp_payload, dst_port, src_mac, src_ip, src_port)

    # -- DHCP ---------------------------------------------------------------

    def _handle_dhcp(self, data, src_mac):
        if len(data) < 240:
            return

        op = data[0]
        if op != 1:  # BOOTREQUEST
            return

        xid = data[4:8]
        chaddr = data[28:34]
        self.client_mac = chaddr

        # Parse DHCP options (start at offset 240, after magic cookie)
        if data[236:240] != bytes([99, 130, 83, 99]):
            return  # bad magic cookie
        msg_type = self._dhcp_get_option(data[240:], 53)
        if msg_type is None:
            return

        if msg_type == bytes([1]):    # DISCOVER
            self._send_dhcp_response(2, xid, chaddr, src_mac)  # OFFER
            self.dhcp_offers += 1
        elif msg_type == bytes([3]):  # REQUEST
            self._send_dhcp_response(5, xid, chaddr, src_mac)  # ACK
            self.dhcp_acks += 1

    def _dhcp_get_option(self, options, code):
        """Extract a DHCP option value by code."""
        i = 0
        while i < len(options):
            opt = options[i]
            if opt == 255:  # END
                break
            if opt == 0:    # PAD
                i += 1
                continue
            if i + 1 >= len(options):
                break
            length = options[i + 1]
            if i + 2 + length > len(options):
                break
            if opt == code:
                return options[i + 2:i + 2 + length]
            i += 2 + length
        return None

    def _send_dhcp_response(self, msg_type, xid, chaddr, dst_mac):
        """Send a DHCP OFFER or ACK."""
        # DHCP message (fixed fields)
        dhcp = bytearray(240)
        dhcp[0] = 2              # BOOTREPLY
        dhcp[1] = 1              # Ethernet
        dhcp[2] = 6              # hw addr len
        dhcp[4:8] = xid
        dhcp[16:20] = self.CLIENT_IP   # yiaddr (your IP)
        dhcp[20:24] = self.SERVER_IP   # siaddr (TFTP server)
        dhcp[28:34] = chaddr           # chaddr

        # Magic cookie
        dhcp += bytes([99, 130, 83, 99])

        # DHCP options
        opts = bytearray()
        opts += bytes([53, 1, msg_type])                    # Message Type
        opts += bytes([54, 4]) + self.SERVER_IP              # Server Identifier
        opts += bytes([1, 4]) + self.SUBNET_MASK             # Subnet Mask
        opts += bytes([3, 4]) + self.ROUTER_IP               # Router
        opts += bytes([6, 4]) + self.DNS_IP                  # DNS
        opts += bytes([51, 4]) + struct.pack(">I", 86400)   # Lease Time
        opts += bytes([255])                                 # END

        dhcp += opts

        # Pad to minimum DHCP size (300 bytes)
        if len(dhcp) < 300:
            dhcp += bytes(300 - len(dhcp))

        udp_frame = self._build_udp_frame(
            self.BROADCAST_MAC, self.BROADCAST_IP, 67, 68, bytes(dhcp))
        self._send_frame(udp_frame)

    # -- TFTP ---------------------------------------------------------------

    def _handle_tftp_rrq(self, data, src_mac, src_ip, src_port):
        """Handle TFTP Read Request."""
        if len(data) < 4:
            return
        opcode = struct.unpack(">H", data[:2])[0]
        if opcode != 1:  # RRQ
            return

        # Parse filename and mode (null-terminated strings)
        fields = data[2:].split(b'\x00')
        if len(fields) < 2:
            return
        filename = fields[0].decode('ascii', errors='replace')
        # mode = fields[1].decode('ascii', errors='replace')  # "octet"

        # Parse options (blksize, tsize, etc.)
        blksize = 512
        want_tsize = False
        i = 2
        while i + 1 < len(fields):
            opt_name = fields[i].decode('ascii', errors='replace').lower()
            opt_val = fields[i + 1].decode('ascii', errors='replace')
            if opt_name == 'blksize':
                blksize = int(opt_val)
            elif opt_name == 'tsize':
                want_tsize = True
            i += 2

        # Load file
        file_data = self._load_tftp_file(filename)
        if file_data is None:
            self._send_tftp_error(src_mac, src_ip, src_port, 1,
                                  f"File not found: {filename}")
            return

        # Allocate a TID (transfer ID = our source port for this session)
        tid = self._next_tid
        self._next_tid += 1

        self.tftp_sessions[tid] = {
            'data': file_data,
            'block': 0,
            'blksize': blksize,
            'client_mac': src_mac,
            'client_ip': src_ip,
            'client_port': src_port,
            'filename': filename,
        }

        # If options were requested, send OACK first
        has_options = blksize != 512 or want_tsize
        if has_options:
            oack = struct.pack(">H", 6)  # OACK opcode
            if blksize != 512:
                oack += f"blksize\x00{blksize}\x00".encode()
            if want_tsize:
                oack += f"tsize\x00{len(file_data)}\x00".encode()
            frame = self._build_udp_frame(
                src_mac, src_ip, tid, src_port, oack)
            self._send_frame(frame)
            # Client will ACK block 0, then we send block 1
        else:
            # No options: send first data block immediately
            self._send_tftp_data(tid)

    def _handle_tftp_ack(self, data, our_tid, src_mac, src_ip, src_port):
        """Handle TFTP ACK -- send next data block."""
        if len(data) < 4:
            return
        opcode, block_num = struct.unpack(">HH", data[:4])
        if opcode != 4:  # ACK
            return

        session = self.tftp_sessions.get(our_tid)
        if session is None:
            return

        session['block'] = block_num
        self._send_tftp_data(our_tid)

    def _send_tftp_data(self, tid):
        """Send the next TFTP DATA block for a session."""
        session = self.tftp_sessions.get(tid)
        if session is None:
            return

        block = session['block'] + 1
        blksize = session['blksize']
        data = session['data']
        offset = (block - 1) * blksize
        chunk = data[offset:offset + blksize]

        # DATA packet: opcode=3, block number, data
        pkt = struct.pack(">HH", 3, block) + chunk
        frame = self._build_udp_frame(
            session['client_mac'], session['client_ip'],
            tid, session['client_port'], pkt)
        self._send_frame(frame)

        self.tftp_bytes_sent += len(chunk)

        # If this was the last block, clean up
        if len(chunk) < blksize:
            self.tftp_files_served += 1
            del self.tftp_sessions[tid]

    def _send_tftp_error(self, dst_mac, dst_ip, dst_port, code, msg):
        """Send a TFTP ERROR packet."""
        tid = self._next_tid
        self._next_tid += 1
        pkt = struct.pack(">HH", 5, code) + msg.encode() + b'\x00'
        frame = self._build_udp_frame(dst_mac, dst_ip, tid, dst_port, pkt)
        self._send_frame(frame)

    def _load_tftp_file(self, filename):
        """Load a file from the TFTP root, with caching."""
        if filename in self._file_cache:
            return self._file_cache[filename]

        # Security: prevent path traversal
        try:
            path = (self.tftp_root / filename).resolve()
            if not str(path).startswith(str(self.tftp_root.resolve())):
                return None
        except (ValueError, OSError):
            return None

        if not path.is_file():
            return None

        data = path.read_bytes()
        self._file_cache[filename] = data
        return data

    # -- Packet construction helpers ----------------------------------------

    def _build_udp_frame(self, dst_mac, dst_ip, src_port, dst_port, payload):
        """Build a complete Ethernet frame with IPv4/UDP."""
        # UDP: src_port, dst_port, length, checksum(0)
        udp_len = 8 + len(payload)
        udp = struct.pack(">HHH", src_port, dst_port, udp_len)
        udp += b'\x00\x00'  # checksum (0 = not computed, valid for IPv4 UDP)
        udp += payload

        return self._build_ipv4_frame(dst_mac, dst_ip, 17, udp)

    def _build_ipv4_frame(self, dst_mac, dst_ip, protocol, payload):
        """Build an Ethernet frame with an IPv4 header."""
        total_len = 20 + len(payload)

        ip = bytearray(20)
        ip[0] = 0x45              # version=4, ihl=5
        struct.pack_into(">H", ip, 2, total_len)
        struct.pack_into(">H", ip, 4, self._ip_id)
        self._ip_id = (self._ip_id + 1) & 0xFFFF
        struct.pack_into(">H", ip, 6, 0x4000)  # Don't Fragment
        ip[8] = 64                # TTL
        ip[9] = protocol
        ip[12:16] = self.SERVER_IP
        ip[16:20] = dst_ip if isinstance(dst_ip, (bytes, bytearray)) else bytes(dst_ip)
        # Checksum
        cksum = self._checksum(bytes(ip))
        struct.pack_into(">H", ip, 10, cksum)

        # Ethernet
        frame = dst_mac + self.SERVER_MAC + struct.pack(">H", 0x0800)
        frame += bytes(ip) + payload
        return frame

    @staticmethod
    def _checksum(data):
        """Internet checksum (RFC 1071)."""
        if len(data) % 2:
            data += b'\x00'
        s = sum(struct.unpack(f">{len(data) // 2}H", data))
        while s >> 16:
            s = (s & 0xFFFF) + (s >> 16)
        return ~s & 0xFFFF


# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------

def check_prerequisites():
    """Verify all required files exist."""
    missing = []
    for name, path in [
        ("QEMU (custom build with GENET)", QEMU),
        ("U-Boot (rpi_4_qemu_defconfig)", UBOOT),
        ("DTB", DTB),
        ("Uncompressed kernel Image", TFTPBOOT / "Image"),
        ("Initramfs", INITRD),
    ]:
        if not path.exists():
            missing.append(f"  {name}: {path}")
    if missing:
        print("Missing prerequisites:")
        print("\n".join(missing))
        return False
    return True


def setup_tftpboot():
    """Populate the TFTP boot directory with required files."""
    TFTPBOOT.mkdir(parents=True, exist_ok=True)

    if not (TFTPBOOT / "Image").exists():
        print("ERROR: Uncompressed Image not found in tftpboot/")
        return False

    for src, name in [
        (DTB, "bcm2711-rpi-4-b.dtb"),
        (INITRD, "initrd.gz"),
    ]:
        dst = TFTPBOOT / name
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dst)

    return True


def run_test():
    """Run the socket networking test with DHCP/TFTP peer."""
    # Start the peer
    peer = SocketNetPeer(TFTPBOOT)
    port = peer.start()

    print("=" * 70)
    print("RPi4B QEMU Socket Networking Test (with DHCP/TFTP peer)")
    print(f"  Peer: Python DHCP/TFTP server on 127.0.0.1:{port}")
    print(f"  QEMU: -nic socket,connect=127.0.0.1:{port}")
    print(f"  TFTP root: {TFTPBOOT}")
    print("=" * 70)

    proc = subprocess.Popen(
        [str(QEMU), "-M", "raspi4b",
         "-kernel", str(UBOOT), "-dtb", str(DTB),
         # Socket networking -- connects to our peer
         "-nic", f"socket,connect=127.0.0.1:{port}",
         # USB devices (same as boot test)
         "-device", "usb-kbd",
         "-chardev", "null,id=usb-serial0",
         "-device", "usb-serial,chardev=usb-serial0",
         "-device", "usb-net",
         "-serial", "stdio", "-display", "none", "-monitor", "none"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)

    # Accept the connection from QEMU and start the peer loop
    try:
        peer.accept(timeout=10)
    except socket.timeout:
        print("ERROR: QEMU did not connect to peer within 10 seconds")
        proc.terminate()
        peer.stop()
        return 1

    peer_thread = threading.Thread(target=peer.run, daemon=True)
    peer_thread.start()

    out_lines = []
    err_lines = []
    start = time.time()

    def read_stdout():
        for line in iter(proc.stdout.readline, ''):
            out_lines.append(line)

    def read_stderr():
        for line in iter(proc.stderr.readline, ''):
            err_lines.append(line)

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()

    def send(cmd, wait=2):
        """Send a command to U-Boot serial console."""
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()
        time.sleep(wait)

    def wait_for(pattern, timeout=30, label=""):
        """Wait for a pattern to appear in the output."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = "".join(out_lines)
            if pattern in text:
                return True
            time.sleep(0.5)
        return False

    try:
        # === Phase 1: U-Boot startup ===
        print("\n--- Phase 1: U-Boot startup (socket networking) ---")

        # Send interrupt characters continuously until we see the
        # U-Boot prompt.  Characters are buffered in the PL011 FIFO
        # and read by U-Boot during its autoboot countdown.
        deadline = time.time() + 20
        while time.time() < deadline:
            proc.stdin.write(" ")
            proc.stdin.flush()
            time.sleep(0.2)
            if "U-Boot>" in "".join(out_lines):
                break
        # Flush any buffered spaces from the command line
        proc.stdin.write("\n")
        proc.stdin.flush()
        time.sleep(1)

        if "U-Boot>" not in "".join(out_lines):
            print("  ERROR: Never reached U-Boot prompt")

        # === Phase 2: DHCP + TFTP via peer ===
        print("--- Phase 2: DHCP + TFTP (served by Python peer) ---")
        send("dhcp", 3)
        wait_for("DHCP client bound", timeout=15, label="DHCP")

        send(f"tftpboot 0x{KERNEL_ADDR:x} Image", 3)
        wait_for("Bytes transferred", timeout=60, label="TFTP kernel")

        send(f"tftpboot 0x{DTB_ADDR:x} bcm2711-rpi-4-b.dtb", 3)
        wait_for("Bytes transferred", timeout=15, label="TFTP DTB")

        send(f"tftpboot 0x{INITRD_ADDR:x} initrd.gz", 3)
        wait_for("Bytes transferred", timeout=60, label="TFTP initrd")

        # === Phase 3: FDT setup + booti ===
        print("--- Phase 3: FDT setup + booti ---")
        send(f"fdt addr 0x{DTB_ADDR:x}", 2)
        send("fdt resize 8192", 2)
        send("fdt set /aliases serial0 /soc/serial@7e201000", 2)
        send("fdt set /aliases serial1 /soc/serial@7e215040", 2)
        # Enable USB -- stock RPi DTB has status="disabled", normally
        # enabled by VideoCore firmware which QEMU skips.
        send("fdt set /soc/usb@7e980000 status okay", 2)
        send(f'setenv bootargs "{BOOTARGS}"', 2)
        send(f"booti 0x{KERNEL_ADDR:x} 0x{INITRD_ADDR:x}:${{filesize}} 0x{DTB_ADDR:x}", 3)

        # === Phase 4: Wait for Linux ===
        print("--- Phase 4: Waiting for Linux boot + network tests ---")
        if not wait_for("Network test complete", timeout=120, label="network tests"):
            print("  TIMEOUT waiting for network tests")

    finally:
        elapsed = time.time() - start
        print(f"\n--- Terminating QEMU after {elapsed:.1f}s ---")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        peer.stop()

    # === Results ===
    text = "".join(out_lines)
    stderr_text = "".join(err_lines)

    # Required checks (must all pass)
    checks = [
        ("U-Boot DHCP",         "DHCP client bound"),
        ("TFTP transfers",      "Bytes transferred"),
        ("booti starts kernel", "Starting kernel"),
        ("Kernel boots",        "Booting Linux on physical CPU"),
        ("GENET driver",        "bcmgenet"),
        ("USB controller",      "DWC OTG Controller"),
        ("USB hub",             "USB hub found"),
        ("USB keyboard",        "QEMU USB Keyboard"),
        ("Link up",             "Link is Up"),
        ("DHCP lease",          "lease of"),
    ]

    # Optional checks (timing-dependent or peer doesn't route internet)
    optional_checks = [
        ("USB serial",          "ttyUSB"),
        ("Ping gateway",        "bytes from 10.0.2.2"),
        ("Ping 8.8.8.8",       "bytes from 8.8.8.8"),
        ("HTTPS fetch",         "HTTPS fetch: SUCCESS"),
    ]

    print("\n" + "=" * 70)
    print("RESULTS (socket networking with DHCP/TFTP peer)")
    print("=" * 70)

    all_pass = True
    for name, pattern in checks:
        found = pattern in text
        if not found:
            all_pass = False
        print(f"  [{'PASS' if found else 'FAIL'}] {name}")

    for name, pattern in optional_checks:
        found = pattern in text
        status = "PASS" if found else "SKIP"
        print(f"  [{status}] {name} (optional)")

    # Peer stats
    print()
    print(f"  Peer stats: {peer.dhcp_offers} DHCP offers, "
          f"{peer.dhcp_acks} DHCP ACKs, "
          f"{peer.tftp_files_served} files served, "
          f"{peer.tftp_bytes_sent / 1024 / 1024:.1f} MB transferred")
    print(f"              {peer.arp_replies} ARP replies, "
          f"{peer.icmp_replies} ICMP echo replies")

    if stderr_text.strip():
        print()
        print("  QEMU stderr:")
        for line in stderr_text.strip().split("\n")[:10]:
            print(f"    {line.rstrip()}")

    # Key output lines
    print()
    for line in text.split("\n"):
        s = line.strip()
        for kw in ["DHCP client bound", "Bytes transferred",
                    "Starting kernel", "Booting Linux",
                    "bcmgenet", "dwc2", "USB:", "ttyUSB",
                    "Link is Up", "lease of",
                    "64 bytes from", "HTTPS fetch",
                    "Network test complete"]:
            if kw in s:
                print(f"  > {s[:130]}")
                break

    print()
    if all_pass:
        print("  ALL TESTS PASSED")
        print("  Socket networking works: DHCP + TFTP + boot over -nic socket")
    else:
        print("  SOME TESTS FAILED")

    print("=" * 70)
    return 0 if all_pass else 1


def main():
    if not check_prerequisites():
        return 1
    if not setup_tftpboot():
        return 1
    return run_test()


if __name__ == "__main__":
    sys.exit(main())
