"""Audio encoders for memdio — convert text to frequency-encoded audio."""

import struct
import zlib

import numpy as np

# Header format: magic (4 bytes) + version (1 byte) + data_length (4 bytes) + crc32 (4 bytes)
HEADER_MAGIC = b"MEM3"
HEADER_VERSION = 2
HEADER_FORMAT = "<4sBII"  # little-endian: 4s magic, B version, I data_len, I crc32
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


class DecodeError(ValueError):
    """Raised when an encoded memory blob cannot be recovered."""


class SimpleEncoder:
    """Original char-to-frequency encoder (v1, kept for backward compat)."""

    def __init__(self, sample_rate=44100):
        self.sample_rate = sample_rate
        self.min_freq = 200
        self.max_freq = 2000
        self.chars = "".join(chr(i) for i in range(32, 127))

    def char_to_freq(self, char):
        if char not in self.chars:
            char = "?"
        idx = self.chars.index(char)
        return self.min_freq + (idx / (len(self.chars) - 1)) * (self.max_freq - self.min_freq)

    def freq_to_char(self, freq):
        idx = round(
            (freq - self.min_freq) / (self.max_freq - self.min_freq) * (len(self.chars) - 1)
        )
        idx = max(0, min(idx, len(self.chars) - 1))
        return self.chars[idx]

    def encode(self, text):
        duration_per_char = 0.1
        total_duration = len(text) * duration_per_char
        t = np.linspace(0, total_duration, int(self.sample_rate * total_duration), endpoint=False)
        signal = np.zeros_like(t)
        segment_length = int(self.sample_rate * duration_per_char)

        for i, char in enumerate(text):
            freq = self.char_to_freq(char)
            start_idx = i * segment_length
            end_idx = min(start_idx + segment_length, len(t))
            if start_idx < len(t):
                segment_t = t[start_idx:end_idx]
                signal[start_idx:end_idx] = np.sin(2 * np.pi * freq * segment_t)

        if np.max(np.abs(signal)) > 0:
            signal = signal / np.max(np.abs(signal))
        return signal

    def decode(self, signal):
        duration_per_char = 0.1
        segment_length = int(self.sample_rate * duration_per_char)
        num_chars = len(signal) // segment_length
        text = ""

        for i in range(num_chars):
            start_idx = i * segment_length
            end_idx = min(start_idx + segment_length, len(signal))
            segment = signal[start_idx:end_idx]
            if len(segment) == 0:
                continue

            fft = np.fft.rfft(segment)
            freqs = np.fft.rfftfreq(len(segment), 1 / self.sample_rate)
            magnitudes = np.abs(fft)
            if len(magnitudes) > 0:
                max_idx = np.argmax(magnitudes)
                peak_freq = freqs[max_idx]
                text += self.freq_to_char(peak_freq)

        return text


class BinaryEncoder:
    """Binary encoder (v2): UTF-8 → zlib → optional ECC → frequency mapping.

    Encodes arbitrary bytes as tones in 200-4000 Hz range (256 frequency slots).
    Uses Hann windowing to reduce spectral leakage. Includes a binary header
    with magic bytes, version, data length, and CRC32 for integrity.
    """

    def __init__(self, sample_rate=48000, duration_per_byte=0.02):
        self.sample_rate = sample_rate
        self.duration_per_byte = duration_per_byte
        self.min_freq = 200.0
        self.max_freq = 4000.0
        self.segment_length = int(sample_rate * duration_per_byte)

    def _byte_to_freq(self, byte_val: int) -> float:
        return self.min_freq + (byte_val / 255.0) * (self.max_freq - self.min_freq)

    def _freq_to_byte(self, freq: float) -> int:
        val = round((freq - self.min_freq) / (self.max_freq - self.min_freq) * 255)
        return max(0, min(255, val))

    def _prepare_data(self, text: str) -> bytes:
        """Text → UTF-8 → zlib compress → ECC (if available) → header + payload."""
        raw = text.encode("utf-8")
        compressed = zlib.compress(raw, level=6)

        # Apply ECC if available
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
        """Header + payload → verify CRC → ECC decode → zlib decompress → text."""
        if len(data) < HEADER_SIZE:
            raise ValueError("Data too short to contain header")

        magic, version, data_len, expected_crc = struct.unpack(
            HEADER_FORMAT, data[:HEADER_SIZE]
        )

        if magic != HEADER_MAGIC:
            raise ValueError(f"Invalid magic bytes: {magic!r}")
        if version != HEADER_VERSION:
            raise ValueError(f"Unsupported encoder version: {version}")

        payload = data[HEADER_SIZE : HEADER_SIZE + data_len]
        if len(payload) != data_len:
            raise ValueError(
                f"Data truncated: expected {data_len} bytes, got {len(payload)}"
            )

        actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ValueError(
                f"CRC mismatch: expected {expected_crc:#010x}, got {actual_crc:#010x}"
            )

        # Decode ECC if available
        try:
            from memdio.core.ecc import ReedSolomonECC
            from reedsolo import ReedSolomonError

            ecc = ReedSolomonECC()
            decompressed_payload = ecc.decode(payload)
        except ImportError:
            decompressed_payload = payload
        except ReedSolomonError as e:
            raise DecodeError("unrecoverable/corrupt memory blob") from e

        try:
            return zlib.decompress(decompressed_payload).decode("utf-8")
        except zlib.error as e:
            raise DecodeError("unrecoverable/corrupt memory blob") from e

    def encode(self, text: str) -> np.ndarray:
        """Encode text to audio signal."""
        data = self._prepare_data(text)
        total_samples = len(data) * self.segment_length
        signal = np.zeros(total_samples, dtype=np.float64)
        t_segment = np.arange(self.segment_length) / self.sample_rate
        window = np.hanning(self.segment_length)

        for i, byte_val in enumerate(data):
            freq = self._byte_to_freq(byte_val)
            start = i * self.segment_length
            signal[start : start + self.segment_length] = (
                np.sin(2 * np.pi * freq * t_segment) * window
            )

        peak = np.max(np.abs(signal))
        if peak > 0:
            signal = signal / peak
        return signal

    def decode(self, signal: np.ndarray) -> str:
        """Decode audio signal back to text."""
        num_bytes = len(signal) // self.segment_length
        data = bytearray()

        for i in range(num_bytes):
            start = i * self.segment_length
            end = start + self.segment_length
            segment = signal[start:end]
            if len(segment) < self.segment_length:
                break

            fft = np.fft.rfft(segment)
            freqs = np.fft.rfftfreq(len(segment), 1 / self.sample_rate)
            magnitudes = np.abs(fft)

            # Ignore DC component
            magnitudes[0] = 0

            max_idx = np.argmax(magnitudes)

            # Parabolic interpolation for sub-bin frequency accuracy
            if 0 < max_idx < len(magnitudes) - 1:
                alpha = magnitudes[max_idx - 1]
                beta = magnitudes[max_idx]
                gamma = magnitudes[max_idx + 1]
                denom = alpha - 2 * beta + gamma
                if abs(denom) > 1e-10:
                    p = 0.5 * (alpha - gamma) / denom
                else:
                    p = 0.0
                peak_freq = freqs[max_idx] + p * (freqs[1] - freqs[0])
            else:
                peak_freq = freqs[max_idx]

            data.append(self._freq_to_byte(peak_freq))

        return self._extract_data(bytes(data))
