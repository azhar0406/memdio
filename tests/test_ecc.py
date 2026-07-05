from memdio.core.ecc import ReedSolomonECC


class TestReedSolomonECC:
    def test_round_trip(self):
        ecc = ReedSolomonECC()
        data = b"Hello, World!"
        encoded = ecc.encode(data)
        assert ecc.decode(encoded) == data

    def test_error_correction(self):
        ecc = ReedSolomonECC()
        data = b"Test data for error correction"
        encoded = bytearray(ecc.encode(data))

        # Corrupt a few bytes (within RS correction capacity)
        for i in range(0, min(10, len(encoded)), 3):
            encoded[i] ^= 0xFF

        assert ecc.decode(bytes(encoded)) == data

    def test_large_data(self):
        ecc = ReedSolomonECC()
        data = bytes(range(256)) * 10
        assert ecc.decode(ecc.encode(data)) == data
