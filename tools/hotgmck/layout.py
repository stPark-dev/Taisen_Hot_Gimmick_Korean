"""Coordinate mapping between CPU/region views and ROM chip files.

Established in docs/initial-survey.md §2 (images matched MAME's internal memory byte-for-byte):
- main CPU 0x000000-0x0FFFFF: each dword = 1-u22 word (byte-swapped) + 2-u23 word (byte-swapped)
- gfx1 region 32MB: per 8MB pair k, each dword = kl word + kh word (bytes as stored)
"""
MAIN_SIZE = 0x100000
GFX_PAIR = 0x800000
GFX_SIZE = 4 * GFX_PAIR


def cpu_image(u22: bytes, u23: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(u22), 2):
        out += bytes((u22[i + 1], u22[i], u23[i + 1], u23[i]))
    return bytes(out)


def main_addr_to_chip(addr: int) -> tuple[str, int]:
    if not 0 <= addr < MAIN_SIZE:
        raise ValueError(f"main address out of range: {addr:#x}")
    chip = "1-u22.bin" if addr % 4 < 2 else "2-u23.bin"
    return chip, (addr // 4) * 2 + (1 - addr % 2)


def gfx_offset_to_file(off: int) -> tuple[str, int]:
    if not 0 <= off < GFX_SIZE:
        raise ValueError(f"gfx offset out of range: {off:#x}")
    k, r = divmod(off, GFX_PAIR)
    half = "l" if r % 4 < 2 else "h"
    return f"{k}{half}.bin", (r // 4) * 2 + r % 2
