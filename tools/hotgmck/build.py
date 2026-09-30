"""Extraction to the translation table and the primary product build."""
import hashlib
import struct
import io
import json
import os
import pathlib
import tempfile
import zipfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import charmap, graphics, layout, source, textblock
from .writeplan import WritePlan

SCHEMA = "hotgmck-dialogue/1"
STATES = ("untranslated", "in_progress", "needs_review", "needs_human_review", "distribution_eligible")
PROTECTED = ("id", "addr", "refs", "capacity", "line_cells", "source_codes", "source")
EDITABLE = ("ko", "state", "note")
GLYPH_INK, GLYPH_BG = 1, 15   # pixel values of source font glyphs (checked against source at build)


# Graphics-text assets (docs/initial-survey.md §10). Coordinates are sprite-sheet pixels.
TEXT_FONT = "/usr/share/fonts/truetype/nanum/NanumGothicExtraBold.ttf"
WHITE_ON_BLACK = {"palette_rom": 0x66CD0, "indices": tuple(range(17, 32)), "transparent": 0,
                  "fill": (244, 244, 244), "outline": (16, 16, 16), "outline_px": 2}
GRAPHICS = [
    {"id": "title_logo", "type": "image", "image": "gfx/title_logo_ko.png",
     "sheet": {"tiles": (0x3427, 0x34C7), "w": 10, "h": 16},
     "box": (23, 46, 298, 173), "bg": 30, "palette_rom": 0x6B210, "alpha_cut": 128},
    {"id": "title_logo_frame7", "type": "image", "image": "gfx/title_logo_ko.png",
     "sheet": {"tiles": (0x31F7, 0x3283), "w": 10, "h": 14},
     "box": (21, 46, 298, 173), "bg": 19, "palette_rom": 0x6B210, "alpha_cut": 128},
    {"id": "title_logo_frame8", "type": "image", "image": "gfx/title_logo_ko.png",
     "sheet": {"tiles": (0x330F, 0x339B), "w": 10, "h": 14},
     "box": (23, 46, 298, 173), "bg": (26, 19), "palette_rom": 0x6B210, "alpha_cut": 128},
    *[{"id": f"card_{cid}", "type": "card", "frames": frames, "palette_rom": pal, "lines": lines,
       "indents": (0, 2, 0, 2)[:len(lines)], "blur": (10, 7, 4, 2, 0)}
      for cid, frames, pal, lines in (
          ("tomoko", ((0x126F4, 11, 10), (0x127C2, 11, 10), (0x12890, 10, 10), (0x12954, 10, 10), (0x12A18, 10, 10)),
           0x69210, ("여고생", "시미즈 토모코", "CV", "야지마 아키코")),
          ("april", ((0x12B3C, 11, 10), (0x12C0A, 11, 10), (0x12CD8, 11, 10), (0x12DA6, 11, 10), (0x12E74, 11, 10)),
           0x69610, ("패션모델", "에이프릴 오가인", "CV", "마츠이 나오코")),
          ("setsuna", ((0x12FA2, 11, 10), (0x13070, 11, 10), (0x1313E, 11, 10), (0x1320C, 11, 10), (0x132DA, 11, 10)),
           0x69A10, ("공원의 소녀", "야스이 세츠나", "CV", "코오로기 사토미")),
          ("hatsune", ((0x13408, 11, 10), (0x134D6, 11, 10), (0x135A4, 11, 10), (0x13672, 10, 10), (0x13736, 9, 10)),
           0x69E10, ("회사원", "스즈키 하츠네", "CV", "미즈타니 유코")),
          ("aoi", ((0x13850, 11, 10), (0x1391E, 11, 10), (0x139EC, 11, 10), (0x13ABA, 10, 10), (0x13B7E, 10, 10)),
           0x6A210, ("간호사", "산조 아오이", "CV", "오오타니 이쿠에")),
          ("yuko_police", ((0x13CA2, 11, 10), (0x13D70, 11, 10), (0x13E3E, 11, 10), (0x13F0C, 10, 10), (0x13FD0, 10, 10)),
           0x6A610, ("여자 경찰", "오니 유코", "CV", "사사키 나츠미")),
          ("yuko_guard", ((0x14094, 11, 10), (0x14162, 11, 10), (0x14230, 11, 10), (0x142FE, 10, 10), (0x143C2, 10, 10)),
           0x6A610, ("경비원", "오니 유코", "CV", "사사키 나츠미")),
          ("staff_design", ((0x144F2, 8, 8), (0x1459E, 8, 8), (0x1464A, 6, 8), (0x146E6, 6, 8), (0x14782, 6, 8)),
           0x6AA10, ("캐릭터", "디자인", "", "츠카사 준")),
      )],
    {"id": "mode_versus", "type": "photo_label", "sheet": (0x1FE9, 8, 6), "palette_rom": 0x66E10,
     "band": (6, 56, 121, 95), "lines": ["통신 대전"]},
    {"id": "mode_single", "type": "photo_label", "sheet": (0x1E39, 8, 6), "palette_rom": 0x66E10,
     "band": (6, 56, 121, 95), "lines": ["1인 플레이"]},
    {"id": "insert_coin_big", "type": "text", "lines": ["코인을", "넣어 주세요!"], "size": 21, "line_gap": -1,
     "parts": ((0x6C2, 6, 1, 16, 0), (0x6C8, 9, 2, 0, 16)), **WHITE_ON_BLACK},
    {"id": "insert_coin_title", "type": "text", "lines": ["코인을 넣어 주세요!"], "size": 17, "line_gap": 0,
     "parts": ((0x6DA, 10, 2, 0, 0),), **WHITE_ON_BLACK},
    {"id": "jan_pow", "type": "pow_label",
     "sheets": tuple((t, 3, 1) for t in (0x64DA, 0x64DD, 0x64E0, 0x64E3, 0x64E9, 0x64EC, 0x64EF, 0x64F5, 0x64F8, 0x64FB,
                                         0x6501, 0x6504)),
     "palette_rom": 0x66A90, "indices": (113, 114, 115, 116, 117, 119, 127), "lines": ["작파워"]},   # 雀pow gauge (templates 83ED0-83F40)
    {"id": "tile_fuyo", "type": "photo_label", "sheet": (0x4D60, 2, 2), "palette_rom": 0x66550, "band": (1, 0, 19, 25),
     "lines": ["버", "림"], "detect": "red", "fill": (140, 16, 32), "outline": None, "outline_px": 0,
     "font": "/usr/share/fonts/truetype/nanum/NanumMyeongjoExtraBold.ttf", "antialias": True,
     "sizes": (13, 12, 11, 10)},   # 不要 marker on a hand tile (template 81D00): user decision 2026-09-30
]


MYEONGJO_XB = "/usr/share/fonts/truetype/nanum/NanumMyeongjoExtraBold.ttf"
# Styles for translated graphics text (translation/graphics_text.json). Palettes are ROM copies of the scene palette bank.
TEXT_STYLES = {
    "outline": {"kind": "outline", "col": 0x06, "palette_rom": 0x66CD0, "indices": tuple(range(17, 32)), "transparent": 0,
                "font": TEXT_FONT, "sizes": (22, 21, 20, 19, 18, 17, 16, 15, 14), "color": (244, 244, 244),
                "outline": (16, 16, 16), "outline_px": 2, "line_gap": 0, "align": "left", "margin": 3},
    "outline_big": {"kind": "outline", "col": 0x06, "palette_rom": 0x66CD0, "indices": tuple(range(17, 32)), "transparent": 0,
                    "font": TEXT_FONT, "sizes": tuple(range(40, 13, -1)), "color": (244, 244, 244),
                    "outline": (16, 16, 16), "outline_px": 2, "line_gap": 0, "align": "left", "margin": 3},
    "serif_white": {"kind": "outline", "col": 0x06, "palette_rom": 0x66CD0, "indices": tuple(range(1, 16)), "transparent": 0,
                    "font": MYEONGJO_XB, "sizes": tuple(range(32, 9, -1)), "color": (255, 255, 255), "outline": None,
                    "outline_px": 0, "line_gap": 0, "align": "center", "margin": 1, "antialias": True,
                    "blend_bg": (48, 106, 67), "alpha_cut": 40},
    "gothic_white": {"kind": "outline", "col": 0x06, "palette_rom": 0x66CD0, "indices": tuple(range(1, 16)), "transparent": 0,
                     "font": TEXT_FONT, "sizes": tuple(range(30, 9, -1)), "color": (255, 255, 255), "outline": None,
                     "outline_px": 0, "line_gap": 0, "align": "center", "margin": 1, "antialias": True,
                     "blend_bg": (48, 106, 67), "alpha_cut": 40},
    "red_small": {"kind": "outline", "col": 0x00, "palette_rom": 0x66A10, "indices": tuple(range(1, 16)), "transparent": 0,
                  "font": TEXT_FONT, "sizes": (13, 12, 11, 10, 9), "color": (215, 0, 0), "outline": (255, 255, 255),
                  "outline_px": 1, "line_gap": 0, "align": "center", "margin": 0},
    "glyph": {"kind": "glyph", "col": 0x01},
    "brush": {"kind": "twotone", "col": 0x10, "font": "/usr/share/fonts/truetype/nanum/NanumBrush.ttf",
              "sizes": tuple(range(48, 11, -1)), "align": "left", "margin": 2, "bold": True},
    "credit": {"kind": "twotone", "col": 0x08, "font": TEXT_FONT, "sizes": tuple(range(22, 9, -1)),
               "align": "left", "margin": 6, "line_gap": 2, "bold": True},
    "wind_small": {"kind": "twotone", "col": 0x00, "font": "/usr/share/fonts/truetype/nanum/NanumGothicExtraBold.ttf",
                   "sizes": (14, 13, 12, 11), "align": "center", "margin": 1, "roles": (0, 12, 15), "outer": True},
    "label_small": {"kind": "twotone", "col": 0x01, "font": "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
                    "sizes": (13, 12, 11, 10, 9, 8), "align": "center", "margin": 0},
    "tile_label": {"kind": "box", "col": 0x20, "palette_rom": 0x66550, "indices": "used", "fill": 78, "inset": 0,
                   "font": "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", "sizes": (14, 13, 12, 11),
                   "color": (247, 247, 243), "line_gap": 0, "antialias": False},
    "mono_white": {"kind": "outline", "col": 0x00, "fixed_palette": {65: (255, 255, 255)}, "indices": (65,), "transparent": 0,
                   "font": TEXT_FONT, "sizes": tuple(range(16, 9, -1)), "color": (255, 255, 255), "outline": None,
                   "outline_px": 0, "line_gap": 1, "align": "center", "margin": 1},
    "bubble": {"kind": "box", "col": 0x01, "palette_rom": 0x66910, "indices": tuple(range(17, 32)), "fill": 31, "inset": 2,
               "font": MYEONGJO_XB, "sizes": (30, 28, 26, 24, 22, 20, 18, 16, 14), "color": (255, 255, 255), "line_gap": 1},
}
GFX_SCHEMA = "hotgmck-graphics-text/1"


class TranslationError(ValueError):
    pass


# ---------------------------------------------------------------- translation records

def extraction_record(e: textblock.Entry) -> dict:
    return {
        "id": e.id, "addr": f"{e.addr:06X}", "refs": [f"{r:06X}" for r in e.refs],
        "capacity": e.capacity, "line_cells": e.line_cells,
        "source_codes": " ".join(f"{c:04X}" for c in e.codes), "source": charmap.decode(e.codes),
        "ko": "", "state": "untranslated", "note": "",
    }


def validate_record(rec: dict) -> None:
    unknown = set(rec) - set(PROTECTED) - set(EDITABLE)
    missing = (set(PROTECTED) | set(EDITABLE)) - set(rec)
    if unknown or missing:
        raise TranslationError(f"{rec.get('id')}: bad field set unknown={sorted(unknown)} missing={sorted(missing)}")
    if rec["state"] not in STATES:
        raise TranslationError(f"{rec['id']}: unknown state {rec['state']!r}")
    if rec["state"] != "untranslated" and not rec["ko"]:
        raise TranslationError(f"{rec['id']}: state {rec['state']} without ko text")
    if rec["state"] == "untranslated" and rec["ko"]:
        raise TranslationError(f"{rec['id']}: ko text present but state is untranslated")


def merge_record(base: dict, old: dict) -> dict:
    validate_record(old)
    diff = [k for k in PROTECTED if base[k] != old[k]]
    if diff:
        raise TranslationError(f"{base['id']}: protected fields differ from extraction: {diff}")
    return dict(base, **{k: old[k] for k in EDITABLE})


def read_table(path) -> dict:
    doc = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA:
        raise TranslationError(f"unknown schema {doc.get('schema')!r}")
    ids = [r["id"] for r in doc["entries"]]
    if len(ids) != len(set(ids)):
        raise TranslationError("duplicate entry ids")
    for r in doc["entries"]:
        validate_record(r)
    return doc


def extract(source_path, table_path) -> dict:
    files = source.load(source_path)
    image = layout.cpu_image(files["1-u22.bin"], files["2-u23.bin"])
    entries = textblock.load_entries(image)
    for e in entries:   # unchanged round trip: parse -> tokens/text -> serialize == source bytes
        raw = image[e.addr:e.addr + e.capacity * 2]
        if textblock.serialize(e, e.codes) != raw or charmap.encode_source(charmap.decode(e.codes)) != e.codes:
            raise TranslationError(f"{e.id}: round trip failed")
    records = [extraction_record(e) for e in entries]
    table_path = pathlib.Path(table_path)
    if table_path.exists():
        old = {r["id"]: r for r in read_table(table_path)["entries"]}
        stale = set(old) - {r["id"] for r in records}
        if stale:
            raise TranslationError(f"entries missing from new extraction: {sorted(stale)}")
        records = [merge_record(r, old[r["id"]]) if r["id"] in old else r for r in records]
    doc = {"schema": SCHEMA,
           "source": {"1-u22.bin": source.PROFILE["1-u22.bin"][1], "2-u23.bin": source.PROFILE["2-u23.bin"][1],
                      "block": [f"{textblock.BLOCK_START:06X}", f"{textblock.BLOCK_END:06X}"]},
           "entries": records}
    table_path.parent.mkdir(parents=True, exist_ok=True)
    table_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return doc


# ---------------------------------------------------------------- write helpers

def add_mapped(plan: WritePlan, writer: str, mapper, base: int, expected: bytes, final: bytes) -> None:
    """Register a write given in a logical view (CPU / gfx region), coalesced into file runs."""
    run = None
    for i in range(len(final)):
        f, off = mapper(base + i)
        if run and run[0] == f and run[1] + len(run[2]) == off:
            run[2].append(expected[i]); run[3].append(final[i])
        else:
            if run:
                plan.add(writer, run[0], run[1], bytes(run[2]), bytes(run[3]))
            run = [f, off, bytearray([expected[i]]), bytearray([final[i]])]
    if run:
        plan.add(writer, run[0], run[1], bytes(run[2]), bytes(run[3]))


def rasterize(ch: str, font_path, size: int, ink: int = GLYPH_INK, bg: int = GLYPH_BG) -> bytes:
    font = ImageFont.truetype(str(font_path), size)
    top = font.getbbox("한")[1]
    im = Image.new("L", (16, 16), 0)
    x = (16 - font.getlength(ch)) / 2
    y = (16 - (font.getbbox("한")[3] - top)) / 2 - top
    if font.getlength(ch) > 16:
        raise ValueError(f"glyph {ch!r} wider than 16px at size {size}")
    draw = ImageDraw.Draw(im)
    draw.fontmode = "1"   # hinted monochrome: keeps final ㅁ/ㅇ distinct at 15px
    draw.text((x, y), ch, font=font, fill=255)
    return bytes(ink if im.getpixel((px, py)) >= 128 else bg for py in range(16) for px in range(16))


def region_read(files: dict[str, bytes], off: int, n: int) -> bytes:
    return bytes(files[f][o] for f, o in (layout.gfx_offset_to_file(off + i) for i in range(n)))


# ---------------------------------------------------------------- product build

def template(image: bytes, addr: int) -> tuple[int, int, int, int]:
    d0, d1 = struct.unpack_from(">II", image, addr)
    return d1 & 0x7FFFF, ((d0 >> 12) & 0xF) + 1, ((d0 >> 28) & 0xF) + 1, (d1 >> 24) & 0x3F


def fit_text(lines, font, sizes, width, height, color, line_gap, outline=None, outline_px=0, antialias=True,
             align="center", margin=0, check=None):
    for size in sizes:
        try:
            art = graphics.text_art(lines, font, size, width, height, color, outline, outline_px, line_gap,
                                    antialias=antialias, align=align, margin=margin)
            if check:
                check(art)
            return art
        except graphics.GraphicsError:
            continue
    raise graphics.GraphicsError(f"text does not fit {width}x{height} at any size: {lines}")


TEMPLATE_AREA = (0x80000, 0x89000)   # sprite template tables in the main ROM (docs/initial-survey.md 10.3)
GFX_TILES = layout.GFX_SIZE // 256


def template_canvas(image: bytes, spec: str):
    parts = []
    if spec.startswith("S:"):          # direct sprite without a template: S:<tnum>:<w>x<h>:<col>
        try:
            _, t, wh, c = spec.split(":")
            w, h = (int(v) for v in wh.split("x"))
            tnum, col = int(t, 16), int(c, 16)
        except ValueError as err:
            raise TranslationError(f"bad direct sprite spec {spec!r}") from err
        if not (1 <= w <= 16 and 1 <= h <= 16 and 0 <= tnum and tnum + w * h <= GFX_TILES and 0 <= col < 0x40):
            raise TranslationError(f"direct sprite spec out of range: {spec!r}")
        return graphics.Canvas(((tnum, w, h, 0, 0),)), {col}
    for a in spec.split("+"):
        try:
            addr = int(a, 16)
        except ValueError as err:
            raise TranslationError(f"bad template address {a!r}") from err
        if addr % 8 or not TEMPLATE_AREA[0] <= addr < TEMPLATE_AREA[1]:
            raise TranslationError(f"template address {addr:#x} outside the template tables")
        d0 = struct.unpack_from(">I", image, addr)[0]
        tnum, w, h, col = template(image, addr)
        x, y = d0 & 0x3FF, (d0 >> 16) & 0x3FF
        if tnum + w * h > GFX_TILES:
            raise TranslationError(f"template {addr:#x} tiles out of range")
        parts.append((tnum, w, h, x - 0x400 if x & 0x200 else x, y - 0x400 if y & 0x200 else y, col))
    mx, my = min(p[3] for p in parts), min(p[4] for p in parts)
    return graphics.Canvas(tuple((t, w, h, x - mx, y - my) for t, w, h, x, y, _ in parts)), {p[5] for p in parts}


def render_graphics_text(files, image, entry):
    style = TEXT_STYLES[entry["style"]]
    sheet, cols = template_canvas(image, entry["template"])
    if cols != {style["col"]}:
        raise RuntimeError(f"{entry['id']}: template palette {sorted(cols)} != style palette {style['col']:#x}")
    rows = sheet.read(lambda off, n: region_read(files, off, n))
    if style["kind"] == "glyph":
        return sheet, rows, glyph_rows(sheet, rows, entry)
    if style["kind"] == "twotone":
        bg, fill, edge = style.get("roles") or graphics.twotone_roles(rows)
        present = {v for r in rows for v in r if v is not None}
        if not {bg, fill, edge} <= present | {0}:
            raise RuntimeError(f"{entry['id']}: two-tone roles {bg, fill, edge} not all used by the source")
        lines = entry["ko"].split("\n")

        def mask(art):
            m = art.getchannel("A")
            return m.filter(ImageFilter.MaxFilter(3)) if style.get("bold") else m

        def check(art):
            graphics.compose_twotone(rows, mask(art), bg, fill, edge, outer=style.get("outer", False))
        art = fit_text(lines, style["font"], style["sizes"], sheet.width, sheet.height, (255, 255, 255),
                       style.get("line_gap", 0), None, 0, False, style["align"], style["margin"], check)
        return sheet, rows, graphics.compose_twotone(rows, mask(art), bg, fill, edge, outer=style.get("outer", False))
    if style.get("indices") == "used":
        style = dict(style, indices=tuple(sorted({v for r in rows for v in r if v})))
    stray = {v for r in rows for v in r if v is not None} - set(style["indices"]) - {0}
    if stray:
        raise RuntimeError(f"{entry['id']}: unexpected palette indices {sorted(stray)}")
    pal = style.get("fixed_palette") or graphics.rom_palette(image, style["palette_rom"], style["indices"])
    lines = entry["ko"].split("\n")
    if style["kind"] == "outline":
        cut, bg = style.get("alpha_cut", 128), style.get("blend_bg")

        def check(art):
            graphics.compose_text(rows, art, pal, style["transparent"], cut, bg)
        art = fit_text(lines, style["font"], style["sizes"], sheet.width, sheet.height, style["color"], style["line_gap"],
                       style["outline"], style["outline_px"], style.get("antialias", False), style["align"],
                       style["margin"], check)
        return sheet, rows, graphics.compose_text(rows, art, pal, style["transparent"], cut, bg)
    erase = graphics.text_box(rows, style["fill"], 0)
    i = style["inset"]
    box = (erase[0] + i, erase[1] + i, erase[2] - i, erase[3] - i)
    art = fit_text(lines, style["font"], style["sizes"], box[2] - box[0] + 1, box[3] - box[1] + 1,
                   style["color"], style["line_gap"], antialias=style.get("antialias", True))
    return sheet, rows, graphics.compose_box(rows, art, box, style["fill"], pal, erase)


GLYPH_FONT = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"


def _graphics_fonts() -> set[str]:
    return {TEXT_FONT, MYEONGJO_XB, GLYPH_FONT} | {st["font"] for st in TEXT_STYLES.values() if "font" in st}


def glyph_rows(sheet, rows, entry):
    """One score-font glyph per 16x16 cell (ink 1 / bg 15, like the dialogue font)."""
    if {v for r in rows for v in r if v is not None} - {GLYPH_INK, GLYPH_BG}:
        raise RuntimeError(f"{entry['id']}: not a font-glyph tile")
    chars = entry["ko"]
    if any(v is None for r in rows for v in r):
        raise RuntimeError(f"{entry['id']}: glyph entries must be a single full sprite")
    cells = [(x, y) for y in range(0, sheet.height, 16) for x in range(0, sheet.width, 16)]
    if len(chars) != len(cells):
        raise RuntimeError(f"{entry['id']}: {len(chars)} chars for {len(cells)} cells")
    out = [list(r) for r in rows]
    for ch, (cx, cy) in zip(chars, cells):
        t = rasterize(ch, GLYPH_FONT, 15)
        for i, v in enumerate(t):
            out[cy + i // 16][cx + i % 16] = v
    return out


def read_gfx_table(path) -> list[dict]:
    doc = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if doc.get("schema") != GFX_SCHEMA:
        raise TranslationError(f"unknown graphics schema {doc.get('schema')!r}")
    ids = [e["id"] for e in doc["entries"]]
    if len(ids) != len(set(ids)):
        raise TranslationError("duplicate graphics entry ids")
    for e in doc["entries"]:
        if set(e) != {"id", "template", "style", "source", "ko", "state", "note"}:
            raise TranslationError(f"{e.get('id')}: bad field set")
        if not all(isinstance(e[k], str) for k in e):
            raise TranslationError(f"{e.get('id')}: all fields must be strings")
        if e["state"] not in STATES or e["style"] not in TEXT_STYLES:
            raise TranslationError(f"{e['id']}: unknown state/style")
        if (e["state"] == "untranslated") == bool(e["ko"]):
            raise TranslationError(f"{e['id']}: ko/state mismatch")
    return doc["entries"]


def graphics_text_writes(plan, files, image, entries) -> dict:
    report = {}
    for e in entries:
        if e["state"] == "untranslated":
            continue
        sheet, old, new = render_graphics_text(files, image, e)
        old_t, new_t = sheet.encode(old), sheet.encode(new)
        changed = {tn: t for tn, t in new_t.items() if t != old_t[tn]}
        for tn, t in changed.items():
            add_mapped(plan, f"gtext:{e['id']}:{tn:05X}", layout.gfx_offset_to_file, tn * 256, old_t[tn], t)
        report[e["id"]] = {"tiles_written": len(changed), "_tiles": changed}
    return report


def photo_label_writes(plan, files, image, spec) -> dict:
    """Text over a photo: detect old text pixels in the band, inpaint them, draw outlined Korean text."""
    tnum, w, h = spec["sheet"]
    sheet = graphics.SpriteSheet((tnum,), w, h)
    rows = sheet.read(lambda off, n: region_read(files, off, n))
    used = sorted({v for r in rows for v in r})
    pal = graphics.rom_palette(image, spec["palette_rom"], used)
    rgb = [[pal[v] for v in r] for r in rows]
    x0, y0, x1, y1 = spec["band"]
    lum = lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    band = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    if spec.get("detect") == "red":     # red glyph on a tile face (e.g. 不要 marker)
        core = {(x, y) for x, y in band if rgb[y][x][0] - max(rgb[y][x][1], rgb[y][x][2]) > 50}
    else:
        core = {(x, y) for x, y in band
                if (lum(rgb[y][x]) > 200 and max(rgb[y][x]) - min(rgb[y][x]) < 40) or lum(rgb[y][x]) < 45}
    mask = {(x + dx, y + dy) for x, y in core for dx in (-1, 0, 1) for dy in (-1, 0, 1)
            if x0 <= x + dx <= x1 and y0 <= y + dy <= y1}
    rgb = graphics.inpaint(rgb, mask)
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    art = fit_text(spec["lines"], spec.get("font", TEXT_FONT), spec.get("sizes", tuple(range(30, 11, -1))), bw, bh,
                   spec.get("fill", (255, 255, 255)), 0, spec.get("outline", (0, 0, 0)), spec.get("outline_px", 2),
                   spec.get("antialias", False))
    new = [list(r) for r in rows]
    cache: dict[tuple, int] = {}
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            r, g, b, a = art.getpixel((x - x0, y - y0))
            c = (r, g, b) if a >= 128 else rgb[y][x]
            if (x, y) in mask or a >= 128:
                new[y][x] = cache.setdefault(c, graphics.nearest(pal, c))
    old_t, new_t = sheet.encode(rows), sheet.encode(new)
    written = {}
    for tn, t in new_t.items():
        if t != old_t[tn]:
            add_mapped(plan, f"photo:{spec['id']}:{tn:05X}", layout.gfx_offset_to_file, tn * 256, old_t[tn], t)
            written[tn] = t
    return {spec["id"]: {"tiles_written": len(written), "_tiles": written}}



def pow_label_writes(plan, files, image, spec) -> dict:
    """雀pow gauge label: red '+N' pixels stay on top; everything else is redrawn as outlined text with a
    white-to-lavender vertical gradient, like the source."""
    pal = graphics.rom_palette(image, spec["palette_rom"], spec["indices"])
    red = lambda v: v in pal and pal[v][0] - max(pal[v][1], pal[v][2]) > 80
    plain = {k: q for k, q in pal.items() if not red(k)}
    written = {}
    for tnum, w, h in spec["sheets"]:
        sheet = graphics.SpriteSheet((tnum,), w, h)
        rows = sheet.read(lambda off, n: region_read(files, off, n))
        stray = {v for r in rows for v in r} - set(spec["indices"]) - {0}
        if stray:
            raise RuntimeError(f"{spec['id']}: unexpected palette indices {sorted(stray)}")
        art = None
        for size in range(16, 8, -1):
            try:
                art = graphics.text_art(spec["lines"], TEXT_FONT, size, sheet.width, sheet.height, (255, 255, 255),
                                        (16, 16, 48), 1, align="left", margin=1)
                break
            except graphics.GraphicsError:
                continue
        if art is None:
            raise graphics.GraphicsError(f"{spec['id']}: text does not fit")
        new, cache = [], {}
        for y, r in enumerate(rows):
            t = y / max(1, sheet.height - 1)
            grad = tuple(round(a + (b - a) * t) for a, b in zip((255, 255, 255), (170, 160, 235)))
            row = []
            for x, v in enumerate(r):
                if red(v):
                    row.append(v)
                    continue
                cr, cg, cb, a = art.getpixel((x, y))
                if a < 128:
                    row.append(0)
                    continue
                c = grad if (cr, cg, cb) == (255, 255, 255) else (cr, cg, cb)
                row.append(cache.setdefault(c, graphics.nearest(plain, c)))
            new.append(row)
        old_t, new_t = sheet.encode(rows), sheet.encode(new)
        for tn, tile in new_t.items():
            if tile != old_t[tn]:
                add_mapped(plan, f"pow:{spec['id']}:{tn:05X}", layout.gfx_offset_to_file, tn * 256, old_t[tn], tile)
                written[tn] = tile
    return {spec["id"]: {"tiles_written": len(written), "_tiles": written}}


def card_writes(plan, files, image, spec) -> dict:
    """Name card: sharp Korean card drawn at the last frame's size, blurred for the earlier frames."""
    reader = lambda off, n: region_read(files, off, n)
    sw, sh = spec["frames"][-1][1] * 16, spec["frames"][-1][2] * 16
    sharp = None
    for size in range(22, 11, -1):
        try:
            sharp = graphics.card_art(spec["lines"], spec["indents"], TEXT_FONT, size, sw, sh, margin=6)
            break
        except graphics.GraphicsError:
            continue
    if sharp is None:
        raise graphics.GraphicsError(f"{spec['id']}: card text does not fit")
    written: dict[int, bytes] = {}
    if len(spec["frames"]) != len(spec["blur"]):
        raise RuntimeError(f"{spec['id']}: frames/blur length mismatch")
    for (tnum, w, h), radius in zip(spec["frames"], spec["blur"]):
        sheet = graphics.SpriteSheet((tnum,), w, h)
        rows = sheet.read(reader)
        used = sorted({v for r in rows for v in r})
        if 0 in used:
            raise RuntimeError(f"{spec['id']}: card frame {tnum:#x} has transparent pixels")
        pal = graphics.rom_palette(image, spec["palette_rom"], used)
        canvas = Image.new("L", (w * 16, h * 16), 255)
        canvas.paste(sharp.crop((0, 0, min(sw, w * 16), min(sh, h * 16))), (0, 0))
        art = graphics.hblur(canvas, radius)
        cache: dict[int, int] = {}
        new = [[cache.setdefault(art.getpixel((x, y)), graphics.nearest(pal, (art.getpixel((x, y)),) * 3))
                for x in range(w * 16)] for y in range(h * 16)]
        old_t, new_t = sheet.encode(rows), sheet.encode(new)
        for tn, t in new_t.items():
            if t != old_t[tn]:
                add_mapped(plan, f"card:{spec['id']}:{tn:05X}", layout.gfx_offset_to_file, tn * 256, old_t[tn], t)
                written[tn] = t
    return {spec["id"]: {"tiles_written": len(written), "_tiles": written}}


def graphics_writes(plan, files, image, assets_dir) -> dict:
    report = {}
    reader = lambda off, n: region_read(files, off, n)
    for spec in GRAPHICS:
        if spec["type"] == "card":
            report.update(card_writes(plan, files, image, spec))
            continue
        if spec["type"] == "pow_label":
            report.update(pow_label_writes(plan, files, image, spec))
            continue
        if spec["type"] == "photo_label":
            report.update(photo_label_writes(plan, files, image, spec))
            continue
        if spec["type"] == "image":
            path = pathlib.Path(assets_dir) / spec["image"]
            data = path.read_bytes()
            art = Image.open(io.BytesIO(data))
            sheet = graphics.SpriteSheet(**spec["sheet"])
            rows = sheet.read(reader)
            pal = graphics.rom_palette(image, spec["palette_rom"], sorted({v for r in rows for v in r}))
            new = graphics.compose(rows, art, spec["box"], spec["bg"], pal, spec["alpha_cut"])
        else:
            data = "\n".join(spec["lines"]).encode("utf-8") + pathlib.Path(TEXT_FONT).read_bytes()
            sheet = graphics.Canvas(spec["parts"])
            rows = sheet.read(reader)
            allowed = set(spec["indices"]) | {spec["transparent"]}
            stray = {v for r in rows for v in r if v is not None} - allowed
            if stray:
                raise RuntimeError(f"{spec['id']}: source uses palette indices outside the declared set: {sorted(stray)}")
            art = graphics.text_art(spec["lines"], TEXT_FONT, spec["size"], sheet.width, sheet.height,
                                    spec["fill"], spec["outline"], spec["outline_px"], spec["line_gap"])
            pal = graphics.rom_palette(image, spec["palette_rom"], spec["indices"])
            new = graphics.compose_text(rows, art, pal, spec["transparent"], 128)
        old_t, new_t = sheet.encode(rows), sheet.encode(new)
        changed = {tn: t for tn, t in new_t.items() if t != old_t[tn]}
        for tn, t in changed.items():
            add_mapped(plan, f"gfx:{spec['id']}:{tn:05X}", layout.gfx_offset_to_file, tn * 256, old_t[tn], t)
        report[spec["id"]] = {"image_sha1": hashlib.sha1(data).hexdigest(), "tiles_written": len(changed), "_tiles": changed}
    return report


def build(source_path, table_path, out_dir, font_path, font_size=15, policy="development", assets_dir=None,
          gfx_table=None, version=None) -> dict:
    if policy not in ("development", "release"):
        raise ValueError(f"unknown policy {policy}")
    font_path = pathlib.Path(font_path)
    font_sha1 = hashlib.sha1(font_path.read_bytes()).hexdigest()
    table_sha1 = hashlib.sha1(pathlib.Path(table_path).read_bytes()).hexdigest()
    files = source.load(source_path)
    image = layout.cpu_image(files["1-u22.bin"], files["2-u23.bin"])
    entries = {e.id: e for e in textblock.load_entries(image)}
    table = read_table(table_path)
    recs = table["entries"]
    if {r["id"] for r in recs} != set(entries):
        raise TranslationError("translation table ids do not match current extraction")
    for r in recs:
        base = extraction_record(entries[r["id"]])
        diff = [k for k in PROTECTED if base[k] != r[k]]
        if diff:
            raise TranslationError(f"{r['id']}: protected fields differ from source: {diff}")
    if policy == "release":
        bad = [r["id"] for r in recs if r["state"] != "distribution_eligible"]
        if bad:
            raise TranslationError(f"release policy: {len(bad)} entries not distribution_eligible (e.g. {bad[:5]})")
    selected = [r for r in recs if r["state"] != "untranslated"]

    used = {c for e in entries.values() for c in e.codes if c < charmap.FONT_GLYPHS}
    slots = charmap.hangul_slots(used)

    plan = WritePlan(files)
    encoded, errors = {}, []
    for r in selected:
        e = entries[r["id"]]
        try:
            codes = textblock.encode_ko(e, r["ko"], slots)
        except textblock.LayoutError as err:
            errors.append(f"{r['id']}: {err}")
            continue
        encoded[r["id"]] = codes
        final = textblock.serialize(e, codes)
        if not textblock.BLOCK_START <= e.addr < e.addr + len(final) <= textblock.BLOCK_END:
            raise RuntimeError(f"{r['id']}: text write outside dialogue block")
        add_mapped(plan, f"text:{r['id']}", layout.main_addr_to_chip, e.addr,
                   image[e.addr:e.addr + len(final)], final)
    if errors:
        raise TranslationError("layout/encoding failures:\n  " + "\n  ".join(errors))

    needed = sorted({ch for r in selected for ch in r["ko"] if ch in slots}, key=slots.get)
    glyphs = {}
    for ch in needed:
        if not charmap.KANJI_START <= slots[ch] < charmap.FONT_GLYPHS:
            raise RuntimeError(f"glyph slot {slots[ch]:#x} outside font kanji area")
        off = (charmap.FONT_TILE_BASE + slots[ch]) * 256
        old = region_read(files, off, 256)
        if not set(old) <= {GLYPH_INK, GLYPH_BG}:
            raise RuntimeError(f"slot {slots[ch]:#x} is not a source font glyph tile")
        glyphs[ch] = rasterize(ch, font_path, font_size)
        add_mapped(plan, f"font:{slots[ch]:03X}", layout.gfx_offset_to_file, off, old, glyphs[ch])

    gfx = graphics_writes(plan, files, image, assets_dir) if assets_dir is not None else {}
    if gfx_table is not None:
        gentries = read_gfx_table(gfx_table)
        if policy == "release" and any(e["state"] != "distribution_eligible" for e in gentries):
            raise TranslationError("release policy: graphics text not all distribution_eligible")
        gfx.update(graphics_text_writes(plan, files, image, gentries))

    out = plan.apply()

    # artifact verification: re-read output through the same views
    out_img = layout.cpu_image(bytes(out["1-u22.bin"]), bytes(out["2-u23.bin"]))
    for rid, codes in encoded.items():
        e = entries[rid]
        if out_img[e.addr:e.addr + e.capacity * 2] != textblock.serialize(e, codes):
            raise RuntimeError(f"{rid}: output verification failed")
    for rep_ in gfx.values():
        for tn, tile in rep_.pop("_tiles").items():
            if region_read(out, tn * 256, 256) != tile:
                raise RuntimeError(f"graphics tile {tn:#x}: output verification failed")
    for ch, tile in glyphs.items():
        if region_read(out, (charmap.FONT_TILE_BASE + slots[ch]) * 256, 256) != tile:
            raise RuntimeError(f"glyph {ch}: output verification failed")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in source.PROFILE:
            z.writestr(name, bytes(out[name]))
    manifest = {
        "patch_version": version, "policy": policy, "distribution": policy == "release",
        "source_profile": {n: s for n, (_, s) in source.PROFILE.items()},
        "translation_table_sha1": table_sha1,
        "font": {"path": font_path.name, "sha1": font_sha1, "size": font_size},
        "graphics_fonts": {pathlib.Path(f).name: hashlib.sha1(pathlib.Path(f).read_bytes()).hexdigest()
                           for f in sorted(_graphics_fonts()) if pathlib.Path(f).exists()},
        "pillow": Image.__version__ if hasattr(Image, "__version__") else __import__("PIL").__version__,
        "entries": {"total": len(recs), "applied": len(encoded), "by_state": {s: sum(r["state"] == s for r in recs) for s in STATES}},
        "glyphs_written": len(glyphs), "writes": len(plan.writes), "graphics": gfx,
        "output_sha1": {n: hashlib.sha1(bytes(out[n])).hexdigest() for n in source.PROFILE},
    }
    _publish(pathlib.Path(out_dir), {
        "hotgmck.zip": buf.getvalue(),
        "manifest.json": (json.dumps(manifest, ensure_ascii=False, indent=1) + "\n").encode("utf-8"),
    })
    return manifest


def _publish(out_dir: pathlib.Path, outputs: dict[str, bytes]) -> None:
    """Write all outputs to temp files first, then move them into place; stale outputs are removed first."""
    out_dir.mkdir(parents=True, exist_ok=True)
    temps = {}
    try:
        for name, data in outputs.items():
            fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{name}.")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            temps[name] = tmp
        for name in outputs:
            (out_dir / name).unlink(missing_ok=True)
        for name, tmp in temps.items():
            os.replace(tmp, out_dir / name)
    finally:
        for tmp in temps.values():
            if os.path.exists(tmp):
                os.unlink(tmp)
