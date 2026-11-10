"""Experimental reliable message transport for Waveshare SX126X UART LoRa HAT.

Preserves existing radio settings. Intended for BB84 simulation demos, not
an authenticated or cryptographically secure transport.
"""
import os
import sys
import time
import struct
import secrets
import zlib
import json

CHUNK_SIZE = 220
MAX_RETRIES = 5
ACK_TIMEOUT = 5.0
FREQ_OFFSET = 65
PACKET_LEN = 3 + 8 + CHUNK_SIZE + 1  # RX prefix + metadata + padded payload + RSSI
ACK_LEN = 13  # RX prefix + ACK + message ID + index + RSSI
MAX_MESSAGE_BYTES = 2_000_000


class LoRaTransport:
    def __init__(self, role, *, port='/dev/ttyAMA0'):
        if role not in ('alice', 'bob'):
            raise ValueError('role must be alice or bob')
        driver_path = f'/home/{role}/Documents/SX126X_LoRa_HAT_915/SX126X_LoRa_HAT_Code/raspberrypi/python'
        if not os.path.isfile(os.path.join(driver_path, 'sx126x.py')):
            raise FileNotFoundError(f'SX126X driver not found: {driver_path}')
        sys.path.insert(0, driver_path)
        import sx126x
        self.node = sx126x.sx126x(serial_num=port, freq=915, addr=0, power=22,
                                  rssi=True, air_speed=2400, relay=False)
        self.serial = self.node.ser
        self.header = bytes([0, 0, FREQ_OFFSET, 0, 0, FREQ_OFFSET])
        self._last_completed_id = None
        self._last_completed_total = None
        self._last_completed_at = 0.0

    def close(self):
        self.serial.close()

    def _read_exact(self, n, timeout):
        out = bytearray()
        deadline = time.monotonic() + timeout
        while len(out) < n:
            if time.monotonic() >= deadline:
                raise TimeoutError(f'UART timed out reading {n} bytes (got {len(out)})')
            available = self.serial.in_waiting
            if available:
                out.extend(self.serial.read(min(n - len(out), available)))
            else:
                time.sleep(0.005)
        return bytes(out)

    def _ack(self, mid, idx):
        body = b'ACK' + struct.pack('>IH', mid, idx)
        self.node.send(self.header + body)

    def _get_packet(self, timeout=None):
        """Read one complete 220-byte padded radio frame; uses RSSI-enabled RX format."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('No LoRa data packet received')
            if self.serial.in_waiting >= 11:
                hdr = self._read_exact(11, 3)
                # The radio prepends source address (2 B) and frequency (1 B).
                mid, idx, total = struct.unpack('>IHH', hdr[3:11])
                if not (1 <= total <= 65535 and idx < total):
                    raise RuntimeError('Invalid LoRa chunk header; UART stream may be misaligned')
                body = self._read_exact(CHUNK_SIZE + 1, 12)
                return mid, idx, total, body[:CHUNK_SIZE]
            time.sleep(0.01)

    def send(self, data):
        if not isinstance(data, bytes):
            raise TypeError('send expects bytes')
        if len(data) > MAX_MESSAGE_BYTES:
            raise ValueError('Message too large')
        framed = struct.pack('>I', len(data)) + data
        chunks = [framed[i:i+CHUNK_SIZE].ljust(CHUNK_SIZE, b'\x00')
                  for i in range(0, len(framed), CHUNK_SIZE)]
        if len(chunks) > 65535:
            raise ValueError('Too many chunks')
        mid = secrets.randbits(32)
        print(f'[LoRa] Sending {len(data)} bytes in {len(chunks)} chunks')
        for idx, payload in enumerate(chunks):
            packet = self.header + struct.pack('>IHH', mid, idx, len(chunks)) + payload
            success = False
            for attempt in range(1, MAX_RETRIES + 1):
                self.node.send(packet)
                deadline = time.monotonic() + ACK_TIMEOUT
                while time.monotonic() < deadline:
                    if self.serial.in_waiting >= ACK_LEN:
                        ack = self._read_exact(ACK_LEN, 2)
                        if ack[3:6] == b'ACK':
                            ack_mid, ack_idx = struct.unpack('>IH', ack[6:12])
                            if ack_mid == mid and ack_idx == idx:
                                success = True
                                break
                    time.sleep(0.01)
                if success:
                    break
                print(f'[LoRa] Chunk {idx+1}/{len(chunks)} ACK timeout, attempt {attempt}')
            if not success:
                raise TimeoutError(f'Chunk {idx+1}/{len(chunks)} failed after {MAX_RETRIES} attempts')
            if (idx + 1) % 20 == 0 or idx + 1 == len(chunks):
                print(f'[LoRa] TX {idx+1}/{len(chunks)} chunks ACKed')

    def receive(self):
        chunks = {}
        current_mid = None
        total_expected = None
        while True:
            mid, idx, total, payload = self._get_packet()
            if mid == self._last_completed_id and total == self._last_completed_total:
                self._ack(mid, idx)
                continue
            if current_mid is None:
                current_mid, total_expected = mid, total
                if total * CHUNK_SIZE > MAX_MESSAGE_BYTES + CHUNK_SIZE:
                    raise ValueError('Incoming message too large')
                print(f'[LoRa] Receiving message: {total} chunks')
            if mid != current_mid or total != total_expected:
                # Ignore stale or unrelated frames rather than mixing messages.
                continue
            chunks.setdefault(idx, payload)
            self._ack(mid, idx)
            if len(chunks) == total_expected:
                combined = b''.join(chunks[i] for i in range(total_expected))
                length = struct.unpack('>I', combined[:4])[0]
                if length > MAX_MESSAGE_BYTES or length > len(combined) - 4:
                    raise ValueError('Invalid received message length')
                data = combined[4:4+length]
                self._last_completed_id = current_mid
                self._last_completed_total = total_expected
                self._last_completed_at = time.monotonic()
                # Keep listening long enough to ACK a retransmitted final frame
                # if the final ACK was lost. Sender waits up to ACK_TIMEOUT.
                grace = ACK_TIMEOUT + 0.5
                end = time.monotonic() + grace
                while time.monotonic() < end:
                    if self.serial.in_waiting >= 11:
                        try:
                            dup_mid, dup_idx, dup_total, _ = self._get_packet(timeout=1)
                            if dup_mid == current_mid and dup_total == total_expected:
                                self._ack(dup_mid, dup_idx)
                        except TimeoutError:
                            pass
                    else:
                        time.sleep(0.01)
                print(f'[LoRa] Received {len(data)} bytes')
                return data


def encode_message(obj):
    raw = json.dumps(obj, separators=(',', ':')).encode('utf-8')
    return zlib.compress(raw)


def decode_message(data):
    # Only run with trusted lab devices. zlib input is not independently authenticated.
    return json.loads(zlib.decompress(data).decode('utf-8'))
