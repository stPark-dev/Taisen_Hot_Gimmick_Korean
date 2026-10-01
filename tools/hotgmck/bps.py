"""Minimal BPS (beat patch) encoder/decoder for same-size files.

Format: "BPS1", varint source size, varint target size, varint metadata size (0), actions, then
CRC32 of source, target and patch (little endian). Only SourceRead and TargetRead actions are emitted,
which every BPS tool (Floating IPS, beat, ...) applies.
"""
import zlib

SOURCE_READ, TARGET_READ, SOURCE_COPY, TARGET_COPY = range(4)


class PatchError(ValueError):
    pass


def _encode_number(v: int) -> bytes:
    out = bytearray()
    while True:
        x = v & 0x7F
        v >>= 7
        if v == 0:
            out.append(0x80 | x)
            return bytes(out)
        out.append(x)
        v -= 1


def _decode_number(data: bytes, pos: int) -> tuple[int, int]:
    v, shift = 0, 1
    while True:
        if pos >= len(data):
            raise PatchError("truncated number")
        x = data[pos]
        pos += 1
        v += (x & 0x7F) * shift
        if x & 0x80:
            return v, pos
        shift <<= 7
        v += shift


def create(source: bytes, target: bytes) -> bytes:
    if len(source) != len(target):
        raise PatchError("size change is not supported")
    out = bytearray(b"BPS1")
    out += _encode_number(len(source)) + _encode_number(len(target)) + _encode_number(0)
    i, n = 0, len(target)
    while i < n:
        j = i
        while j < n and source[j] == target[j]:
            j += 1
        if j > i:
            out += _encode_number(((j - i - 1) << 2) | SOURCE_READ)
            i = j
            continue
        j = i
        while j < n and (source[j] != target[j] or (j + 1 < n and source[j + 1] != target[j + 1])):
            j += 1                      # a single equal byte between edits is cheaper to carry as data
        out += _encode_number(((j - i - 1) << 2) | TARGET_READ) + target[i:j]
        i = j
    out += zlib.crc32(source).to_bytes(4, "little") + zlib.crc32(target).to_bytes(4, "little")
    out += zlib.crc32(bytes(out)).to_bytes(4, "little")
    return bytes(out)


def apply(source: bytes, patch: bytes) -> bytes:
    if len(patch) < 16 or patch[:4] != b"BPS1":
        raise PatchError("not a BPS patch")
    if zlib.crc32(patch[:-4]) != int.from_bytes(patch[-4:], "little"):
        raise PatchError("patch checksum mismatch (corrupted patch)")
    if zlib.crc32(source) != int.from_bytes(patch[-12:-8], "little"):
        raise PatchError("source checksum mismatch (wrong source file)")
    pos = 4
    src_size, pos = _decode_number(patch, pos)
    dst_size, pos = _decode_number(patch, pos)
    meta, pos = _decode_number(patch, pos)
    pos += meta
    if src_size != len(source):
        raise PatchError("source size mismatch")
    out = bytearray()
    src_rel = dst_rel = 0
    end = len(patch) - 12
    while pos < end:
        cmd, pos = _decode_number(patch, pos)
        action, length = cmd & 3, (cmd >> 2) + 1
        if action == SOURCE_READ:
            out += source[len(out):len(out) + length]
        elif action == TARGET_READ:
            out += patch[pos:pos + length]
            pos += length
        else:
            off, pos = _decode_number(patch, pos)
            delta = (-1 if off & 1 else 1) * (off >> 1)
            if action == SOURCE_COPY:
                src_rel += delta
                out += source[src_rel:src_rel + length]
                src_rel += length
            else:
                dst_rel += delta
                for _ in range(length):
                    out.append(out[dst_rel])
                    dst_rel += 1
    if len(out) != dst_size:
        raise PatchError("target size mismatch")
    if zlib.crc32(bytes(out)) != int.from_bytes(patch[-8:-4], "little"):
        raise PatchError("target checksum mismatch")
    return bytes(out)
