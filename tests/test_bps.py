"""BPS patch format: encode/apply round trip, checksums, and rejection of wrong inputs."""
import random

import pytest

from hotgmck import bps


def test_roundtrip_small_edit():
    src = bytes(range(256)) * 16
    dst = bytearray(src)
    dst[100:104] = b"KORE"
    dst[3000] = 0
    patch = bps.create(src, bytes(dst))
    assert patch[:4] == b"BPS1"
    assert bps.apply(src, patch) == bytes(dst)


def test_roundtrip_identical_is_tiny():
    src = bytes(4096)
    patch = bps.create(src, src)
    assert bps.apply(src, patch) == src
    assert len(patch) < 40


@pytest.mark.parametrize("seed", range(5))
def test_roundtrip_random(seed):
    rnd = random.Random(seed)
    src = bytes(rnd.randrange(256) for _ in range(5000))
    dst = bytearray(src)
    for _ in range(40):
        i = rnd.randrange(len(dst))
        dst[i] = rnd.randrange(256)
    assert bps.apply(src, bps.create(src, bytes(dst))) == bytes(dst)


def test_rejects_wrong_source():
    src, dst = bytes(100), bytes([1]) * 100
    patch = bps.create(src, dst)
    with pytest.raises(bps.PatchError, match="source"):
        bps.apply(bytes([2]) * 100, patch)


def test_rejects_corrupted_patch():
    src, dst = bytes(100), bytes([1]) * 100
    patch = bytearray(bps.create(src, dst))
    patch[6] ^= 0xFF
    with pytest.raises(bps.PatchError):
        bps.apply(src, bytes(patch))


def test_rejects_size_change():
    with pytest.raises(bps.PatchError, match="size"):
        bps.create(bytes(10), bytes(11))


def test_varint_roundtrip_boundaries():
    for v in (0, 1, 127, 128, 129, 16383, 16384, 2 ** 32, 2 ** 40 + 7):
        enc = bps._encode_number(v)
        dec, pos = bps._decode_number(enc, 0)
        assert dec == v and pos == len(enc)
