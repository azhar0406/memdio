"""Multi-channel frequency-division encoder — 4x throughput via OFDM.

Instead of encoding one byte per time slot across the full 200-4000Hz range,
we divide the spectrum into 4 non-overlapping channels and encode 4 bytes
simultaneously:

  Channel 0: 200 - 1100 Hz   (64 frequency slots)
  Channel 1: 1200 - 2100 Hz  (64 frequency slots)
  Channel 2: 2200 - 3100 Hz  (64 frequency slots)
  Channel 3: 3200 - 4100 Hz  (64 frequency slots)

Each channel encodes 6 bits (64 values). Four channels = 24 bits = 3 bytes
per time slot. With guard bands between channels to prevent interference.

The data is split into 3-byte chunks, each chunk encoded as 4 simultaneous
tones in a single time slot. This gives ~3x throughput vs single-channel.

Still uses the same zlib + Reed-Solomon + CRC32 header pipeline.
"""

import struct
import zlib

import numpy as np

from memdio.core.encoder import DecodeError

HEADER_MAGIC = b"MC3M"  # Multi-Channel memdio
HEADER_VERSION = 3
HEADER_FORMAT = "<4sBII"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# 4 channels with guard bands
CHANNELS = [
    (200.0, 1100.0),    # Channel 0
    (1200.0, 2100.0),   # Channel 1
    (2200.0, 3100.0),   # Channel 2
    (3200.0, 4100.0),   # Channel 3
]
BITS_PER_CHANNEL = 6  # 64 frequency slots per channel
VALUES_PER_CHANNEL = 2 ** BITS_PER_CHANNEL  # 64
BYTES_PER_SLOT = 3  # 24 bits = 3 bytes per time slot


class MultiChannelEncoder:
    """OFDM-style encoder: 4 simultaneous frequency channels, ~3x throughput.

    Encoding: text -> UTF-8 -> zlib -> ECC -> split into 3-byte chunks ->
              each chunk -> 4 tones (one per channel) -> sum -> audio
    Decoding: audio -> FFT per slot -> extract 4 peaks -> reassemble bytes ->
              ECC decode -> zlib decompress -> text
    """

    def __init__(self, sample_rate=48000, duration_per_slot=0.02):
        self.sample_rate = sample_rate
        self.duration_per_slot = duration_per_slot
        self.segment_length = int(sample_rate * duration_per_slot)

    def _val_to_freq(self, val: int, channel: int) -> float:
        """Map a 6-bit value (0-63) to a frequency within a channel."""
        lo, hi = CHANNELS[channel]
        return lo + (val / (VALUES_PER_CHANNEL - 1)) * (hi - lo)

    def _freq_to_val(self, freq: float, channel: int) -> int:
        """Map a frequency back to a 6-bit value."""
        lo, hi = CHANNELS[channel]
        val = round((freq - lo) / (hi - lo) * (VALUES_PER_CHANNEL - 1))
        return max(0, min(VALUES_PER_CHANNEL - 1, val))

    def _prepare_data(self, text: str) -> bytes:
        """Text -> UTF-8 -> zlib -> ECC -> header + payload."""
        raw = text.encode("utf-8")
        compressed = zlib.compress(raw, level=6)

        try:
            from memdio.core.ecc import ReedSolomonECC
            ecc = ReedSolomonECC()
            payload = ecc.encode(compressed)
        except ImportError:
            payload = compressed

        crc = zlib.crc32(payload) & 0xFFFFFFFF
        header = struct.pack(HEADER_FORMAT, HEADER_MAGIC, HEADER_VERSION, len(payload), crc)
        return header + payload

    def _extract_data(self, data: bytes) -> str:
        """Header + payload -> verify -> ECC decode -> decompress -> text."""
        if len(data) < HEADER_SIZE:
            raise ValueError("Data too short for header")

        magic, version, data_len, expected_crc = struct.unpack(
            HEADER_FORMAT, data[:HEADER_SIZE]
        )
        if magic != HEADER_MAGIC:
            raise ValueError(f"Invalid magic: {magic!r}")
        if version != HEADER_VERSION:
            raise ValueError(f"Unsupported version: {version}")

        payload = data[HEADER_SIZE: HEADER_SIZE + data_len]
        if len(payload) != data_len:
            raise ValueError(f"Truncated: expected {data_len}, got {len(payload)}")

        actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ValueError(f"CRC mismatch: {expected_crc:#010x} vs {actual_crc:#010x}")

        try:
            from memdio.core.ecc import ReedSolomonECC
            from reedsolo import ReedSolomonError

            ecc = ReedSolomonECC()
            payload = ecc.decode(payload)
        except ImportError:
            pass
        except ReedSolomonError as e:
            raise DecodeError("unrecoverable/corrupt memory blob") from e

        try:
            return zlib.decompress(payload).decode("utf-8")
        except zlib.error as e:
            raise DecodeError("unrecoverable/corrupt memory blob") from e

    def _bytes_to_slots(self, data: bytes) -> list[tuple[int, int, int, int]]:
        """Split bytes into 3-byte chunks, each chunk -> 4 x 6-bit values."""
        slots = []
        for i in range(0, len(data), BYTES_PER_SLOT):
            chunk = data[i: i + BYTES_PER_SLOT]
            # Pad last chunk with zeros if needed
            while len(chunk) < BYTES_PER_SLOT:
                chunk += b"\x00"

            # 3 bytes = 24 bits -> 4 x 6-bit values
            bits = int.from_bytes(chunk, "big")
            v0 = (bits >> 18) & 0x3F
            v1 = (bits >> 12) & 0x3F
            v2 = (bits >> 6) & 0x3F
            v3 = bits & 0x3F
            slots.append((v0, v1, v2, v3))

        return slots

    def _slots_to_bytes(self, slots: list[tuple[int, int, int, int]], total_bytes: int) -> bytes:
        """Reassemble 4 x 6-bit values per slot back into bytes."""
        result = bytearray()
        for v0, v1, v2, v3 in slots:
            bits = (v0 << 18) | (v1 << 12) | (v2 << 6) | v3
            result.extend(bits.to_bytes(BYTES_PER_SLOT, "big"))
        return bytes(result[:total_bytes])

    def encode(self, text: str) -> np.ndarray:
        """Encode text to multi-channel audio signal."""
        data = self._prepare_data(text)
        slots = self._bytes_to_slots(data)

        total_samples = len(slots) * self.segment_length
        signal = np.zeros(total_samples, dtype=np.float64)
        t_segment = np.arange(self.segment_length) / self.sample_rate
        window = np.hanning(self.segment_length)

        for i, (v0, v1, v2, v3) in enumerate(slots):
            start = i * self.segment_length
            tone = np.zeros(self.segment_length)
            for ch_idx, val in enumerate([v0, v1, v2, v3]):
                freq = self._val_to_freq(val, ch_idx)
                tone += np.sin(2 * np.pi * freq * t_segment)
            signal[start: start + self.segment_length] = tone * window

        peak = np.max(np.abs(signal))
        if peak > 0:
            signal = signal / peak
        return signal

    def decode(self, signal: np.ndarray) -> str:
        """Decode multi-channel audio signal back to text."""
        num_slots = len(signal) // self.segment_length
        slots = []

        for i in range(num_slots):
            start = i * self.segment_length
            end = start + self.segment_length
            segment = signal[start:end]
            if len(segment) < self.segment_length:
                break

            fft = np.fft.rfft(segment)
            freqs = np.fft.rfftfreq(len(segment), 1 / self.sample_rate)
            magnitudes = np.abs(fft)
            magnitudes[0] = 0  # ignore DC

            values = []
            for ch_idx in range(4):
                lo, hi = CHANNELS[ch_idx]
                # Mask frequencies outside this channel
                mask = (freqs >= lo - 50) & (freqs <= hi + 50)
                ch_mags = np.zeros_like(magnitudes)
                ch_mags[mask] = magnitudes[mask]

                max_idx = np.argmax(ch_mags)

                # Parabolic interpolation
                if 0 < max_idx < len(ch_mags) - 1:
                    alpha = ch_mags[max_idx - 1]
                    beta = ch_mags[max_idx]
                    gamma = ch_mags[max_idx + 1]
                    denom = alpha - 2 * beta + gamma
                    if abs(denom) > 1e-10:
                        p = 0.5 * (alpha - gamma) / denom
                    else:
                        p = 0.0
                    peak_freq = freqs[max_idx] + p * (freqs[1] - freqs[0])
                else:
                    peak_freq = freqs[max_idx]

                values.append(self._freq_to_val(peak_freq, ch_idx))

            slots.append(tuple(values))

        # Reconstruct total byte count from header
        # First, reassemble enough bytes to read the header
        header_slots = (HEADER_SIZE + BYTES_PER_SLOT - 1) // BYTES_PER_SLOT
        if len(slots) < header_slots:
            raise ValueError("Not enough data for header")

        header_bytes = self._slots_to_bytes(slots[:header_slots], HEADER_SIZE)
        _, _, data_len, _ = struct.unpack(HEADER_FORMAT, header_bytes[:HEADER_SIZE])
        total_bytes = HEADER_SIZE + data_len

        all_bytes = self._slots_to_bytes(slots, total_bytes)
        return self._extract_data(all_bytes)
