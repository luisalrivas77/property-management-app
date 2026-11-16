"""Keccak-256 as Ethereum uses it (the original Keccak submission's padding, 0x01), needed for
the EIP-55 checksum of an EVM address (docs/phases/phase-7.md D179 (2), step 1).

`hashlib.sha3_256` is NIST SHA-3, which pads with 0x06 and gives a different digest, and no
backend dependency provides Keccak, so this is a small pure-Python Keccak-f[1600] sponge
(FIPS 202 §3, rate 1088 bits). Only short inputs (a 40-character address) are hashed, so speed
does not matter. Tested against Keccak-256's published answer for the empty input, EIP-55's
example addresses, and, with the padding byte switched to 0x06, against hashlib.sha3_256.
"""

_MASK = (1 << 64) - 1
_RATE = 136  # bytes: 1600 - 2 * 256 bits

_ROUND_CONSTANTS = (
    0x0000000000000001,
    0x0000000000008082,
    0x800000000000808A,
    0x8000000080008000,
    0x000000000000808B,
    0x0000000080000001,
    0x8000000080008081,
    0x8000000000008009,
    0x000000000000008A,
    0x0000000000000088,
    0x0000000080008009,
    0x000000008000000A,
    0x000000008000808B,
    0x800000000000008B,
    0x8000000000008089,
    0x8000000000008003,
    0x8000000000008002,
    0x8000000000000080,
    0x000000000000800A,
    0x800000008000000A,
    0x8000000080008081,
    0x8000000000008080,
    0x0000000080000001,
    0x8000000080008008,
)

# The rotation offsets r[x][y] of the ρ step.
_ROTATIONS = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def _rotl(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (64 - shift))) & _MASK if shift else value


def _permute(lanes: list[list[int]]) -> None:
    """Keccak-f[1600] in place over lanes[x][y]."""
    for constant in _ROUND_CONSTANTS:
        # θ
        c = [lanes[x][0] ^ lanes[x][1] ^ lanes[x][2] ^ lanes[x][3] ^ lanes[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                lanes[x][y] ^= d[x]
        # ρ and π
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rotl(lanes[x][y], _ROTATIONS[x][y])
        # χ
        for x in range(5):
            for y in range(5):
                lanes[x][y] = b[x][y] ^ (~b[(x + 1) % 5][y] & b[(x + 2) % 5][y])
        # ι
        lanes[0][0] ^= constant


def _sponge_256(data: bytes, pad: int) -> bytes:
    padded = bytearray(data)
    padded.append(pad)
    padded.extend(b"\0" * (-len(padded) % _RATE))
    padded[-1] |= 0x80
    lanes = [[0] * 5 for _ in range(5)]
    for start in range(0, len(padded), _RATE):
        block = padded[start : start + _RATE]
        for i in range(_RATE // 8):
            lanes[i % 5][i // 5] ^= int.from_bytes(block[8 * i : 8 * i + 8], "little")
        _permute(lanes)
    out = b"".join(lanes[i % 5][i // 5].to_bytes(8, "little") for i in range(_RATE // 8))
    return out[:32]


def keccak256(data: bytes) -> bytes:
    """Keccak-256 (Ethereum's hash) of `data`."""
    return _sponge_256(data, 0x01)
