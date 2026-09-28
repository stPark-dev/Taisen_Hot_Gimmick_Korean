"""End-to-end extract/build on a synthetic MAME set (no copyrighted data)."""
import hashlib
import json
import os
import struct

import pytest

from hotgmck import build, charmap, layout, source, textblock

NANUM = "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"
pytestmark = pytest.mark.skipif(not os.path.exists(NANUM), reason="font not installed")


def _cpu_put(u22, u23, addr, data):
    for i, b in enumerate(data):
        chip, off = layout.main_addr_to_chip(addr + i)
        (u22 if chip == "1-u22.bin" else u23)[off] = b


def make_set(tmp_path):
    sizes = {n: s for n, (s, _) in source.PROFILE.items()}
    files = {n: bytearray(s) for n, s in sizes.items()}
    # text block: string A (2 lines), string B, then empty strings to the block end
    a = [0x086E, 0x07D1, 0xFFFD, 0xFFFE, 0x0008, 0xFFFD, 0xFFFD]
    b = [0x0075]
    blob = struct.pack(">%dH" % (len(a) + 1), *a, 0xFFFF)
    addr_b = textblock.BLOCK_START + len(blob)
    blob += struct.pack(">2H", *b, 0xFFFF)
    while textblock.BLOCK_START + len(blob) < textblock.BLOCK_END:
        blob += struct.pack(">2H", 0xFFFF, 0)
    blob = blob[:textblock.BLOCK_END - textblock.BLOCK_START]
    _cpu_put(files["1-u22.bin"], files["2-u23.bin"], textblock.BLOCK_START, blob)
    _cpu_put(files["1-u22.bin"], files["2-u23.bin"], 0x88000, struct.pack(">2I", textblock.BLOCK_START, addr_b))
    # font tiles: background 15 with one ink pixel, like the source font
    tile = bytes([1] + [15] * 255)
    for code in range(charmap.FONT_GLYPHS):
        base = (charmap.FONT_TILE_BASE + code) * 256
        for i, v in enumerate(tile):
            f, o = layout.gfx_offset_to_file(base + i)
            files[f][o] = v
    d = tmp_path / "src"
    d.mkdir()
    prof = {}
    for n, data in files.items():
        (d / n).write_bytes(bytes(data))
        prof[n] = (len(data), hashlib.sha1(data).hexdigest())
    return d, prof


@pytest.fixture
def env(tmp_path, monkeypatch):
    d, prof = make_set(tmp_path)
    monkeypatch.setattr(source, "PROFILE", prof)
    table = tmp_path / "t.json"
    build.extract(d, table)
    return d, table, tmp_path / "out"


def _edit(table, rid, **kw):
    doc = json.loads(table.read_text(encoding="utf-8"))
    for r in doc["entries"]:
        if r["id"] == rid:
            r.update(kw)
    table.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_extract_then_identity_build(env):
    d, table, out = env
    doc = json.loads(table.read_text(encoding="utf-8"))
    first = doc["entries"][0]
    assert first["id"] == "T0A77F0" and first["source"] == "対戦 \n！  " and first["refs"] == ["088000"]
    m = build.build(d, table, out, NANUM)
    assert m["writes"] == 0
    assert all(m["output_sha1"][n] == s for n, (_, s) in source.PROFILE.items())


def test_translated_build_writes_text_and_glyphs(env):
    d, table, out = env
    _edit(table, "T0A77F0", ko="가나\n!", state="in_progress")
    m = build.build(d, table, out, NANUM)
    assert m["entries"]["applied"] == 1 and m["glyphs_written"] == 2 and m["distribution"] is False
    assert (out / "hotgmck.zip").exists() and (out / "manifest.json").exists()


def test_failed_build_leaves_no_output(env):
    d, table, out = env
    _edit(table, "T0A77F0", ko="가나다라마바", state="in_progress")   # too wide
    with pytest.raises(build.TranslationError):
        build.build(d, table, out, NANUM)
    assert not out.exists() or not any(out.iterdir())


def test_missing_font_fails_before_writing(env):
    d, table, out = env
    with pytest.raises(OSError):
        build.build(d, table, out, "/nonexistent.ttf")
    assert not out.exists() or not any(out.iterdir())


def test_protected_field_drift_fails(env):
    d, table, out = env
    _edit(table, "T0A77F0", source="바뀐 원문")
    with pytest.raises(build.TranslationError, match="protected"):
        build.build(d, table, out, NANUM)


def test_release_policy_requires_all_eligible(env):
    d, table, out = env
    _edit(table, "T0A77F0", ko="가", state="distribution_eligible")
    with pytest.raises(build.TranslationError, match="release"):
        build.build(d, table, out, NANUM, policy="release")


def test_ko_text_with_untranslated_state_is_rejected(env):
    d, table, out = env
    _edit(table, "T0A77F0", ko="가")
    with pytest.raises(build.TranslationError, match="untranslated"):
        build.build(d, table, out, NANUM)


def test_extract_refuses_stale_table_entries(env, tmp_path):
    d, table, _ = env
    doc = json.loads(table.read_text(encoding="utf-8"))
    doc["entries"].append(dict(doc["entries"][0], id="T0FFFFF"))
    table.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(build.TranslationError, match="missing"):
        build.extract(d, table)


def _fill_title(files_dir, prof):
    """Paint the title sheet tiles with background 30 and a fake logo, and a palette in main ROM."""
    from hotgmck import graphics
    data = {n: bytearray((files_dir / n).read_bytes()) for n in prof}
    for spec in (s for s in build.GRAPHICS if s["type"] == "image"):
        sheet = graphics.SpriteSheet(**spec["sheet"])
        x0, y0, x1, y1 = spec["box"]
        bg = spec["bg"]
        bg_at = (lambda x, y: bg[(x + y) % 2]) if isinstance(bg, tuple) else (lambda x, y: bg)
        rows = [[7 if x0 + 5 <= x <= x1 - 5 and y0 + 5 <= y <= y1 - 5 else bg_at(x, y) for x in range(sheet.width)]
                for y in range(sheet.height)]
        for tn, tile in sheet.encode(rows).items():
            for i, v in enumerate(tile):
                f, o = layout.gfx_offset_to_file(tn * 256 + i)
                data[f][o] = v
        for i in range(256):
            _cpu_put(data["1-u22.bin"], data["2-u23.bin"], spec["palette_rom"] + i * 4, bytes((i, 255 - i, i // 2, 0)))
    for spec in (s for s in build.GRAPHICS if s["type"] == "card"):
        for tnum, w, h in spec["frames"]:
            for i in range(w * h * 256):
                f, o = layout.gfx_offset_to_file(tnum * 256 + i)
                data[f][o] = 1 + (i % 2)
    new_prof = {}
    for n, d in data.items():
        (files_dir / n).write_bytes(bytes(d))
        new_prof[n] = (len(d), hashlib.sha1(d).hexdigest())
    return new_prof


def test_graphics_asset_replaces_only_editable_box(tmp_path, monkeypatch):
    from PIL import Image
    from hotgmck import graphics
    d, prof = make_set(tmp_path)
    prof = _fill_title(d, prof)
    monkeypatch.setattr(source, "PROFILE", prof)
    table = tmp_path / "t.json"
    build.extract(d, table)
    assets = tmp_path / "assets"
    (assets / "gfx").mkdir(parents=True)
    spec = build.GRAPHICS[0]
    Image.new("RGBA", (40, 20), (0, 0, 0, 255)).save(assets / spec["image"])
    out = tmp_path / "out"
    m = build.build(d, table, out, NANUM, assets_dir=assets)
    assert m["graphics"][spec["id"]]["tiles_written"] > 0
    import zipfile
    z = zipfile.ZipFile(out / "hotgmck.zip")
    files = {n: z.read(n) for n in z.namelist()}
    sheet = graphics.SpriteSheet(**spec["sheet"])
    rows = sheet.read(lambda o, n: build.region_read(files, o, n))
    x0, y0, x1, y1 = spec["box"]
    assert all(v == spec["bg"] for y, r in enumerate(rows) for x, v in enumerate(r)
               if not (x0 <= x <= x1 and y0 <= y <= y1))
    assert not any(v == 7 for r in rows for v in r)


def test_missing_graphics_asset_fails(tmp_path, monkeypatch):
    d, prof = make_set(tmp_path)
    prof = _fill_title(d, prof)
    monkeypatch.setattr(source, "PROFILE", prof)
    table = tmp_path / "t.json"
    build.extract(d, table)
    with pytest.raises(FileNotFoundError):
        build.build(d, table, tmp_path / "out", NANUM, assets_dir=tmp_path / "noassets")


def _put_bubble(d, prof, addr=0x80000, tnum=0x9000, w=4, h=2):
    data = {n: bytearray((d / n).read_bytes()) for n in prof}
    _cpu_put(data["1-u22.bin"], data["2-u23.bin"], addr,
             struct.pack(">II", ((h - 1) << 28) | ((w - 1) << 12), (0x01 << 24) | tnum))
    for i in range(32):
        _cpu_put(data["1-u22.bin"], data["2-u23.bin"], 0x66910 + i * 4, bytes((i * 8, i * 8, 255 - i * 8, 0)))
    for k in range(w * h):
        for i in range(256):
            x, y = (k % w) * 16 + i % 16, (k // w) * 16 + i // 16
            v = 17 if x == 0 or y == 0 or x == w * 16 - 1 or y == h * 16 - 1 else (20 if 4 <= x < w * 16 - 4 and 4 <= y < h * 16 - 4 and (x * y) % 7 == 0 else 31)
            f, o = layout.gfx_offset_to_file((tnum + k) * 256 + i)
            data[f][o] = v
    new = {}
    for n, b in data.items():
        (d / n).write_bytes(bytes(b))
        new[n] = (len(b), hashlib.sha1(b).hexdigest())
    return new


def test_graphics_text_table_rewrites_bubble(tmp_path, monkeypatch):
    d, prof = make_set(tmp_path)
    prof = _put_bubble(d, prof)
    monkeypatch.setattr(source, "PROFILE", prof)
    table = tmp_path / "t.json"
    build.extract(d, table)
    gt = tmp_path / "g.json"
    entry = {"id": "G080000", "template": "080000", "style": "bubble", "source": "カン!", "ko": "깡!",
             "state": "needs_review", "note": ""}
    gt.write_text(json.dumps({"schema": build.GFX_SCHEMA, "entries": [entry]}, ensure_ascii=False), encoding="utf-8")
    m = build.build(d, table, tmp_path / "out", NANUM, gfx_table=gt)
    assert m["graphics"]["G080000"]["tiles_written"] > 0
    import zipfile
    z = zipfile.ZipFile(tmp_path / "out" / "hotgmck.zip")
    files = {n: z.read(n) for n in z.namelist()}
    from hotgmck import graphics
    rows = graphics.SpriteSheet((0x9000,), 4, 2).read(lambda o, n: build.region_read(files, o, n))
    assert all(rows[0][x] == 17 for x in range(64))            # border kept
    assert not any(v == 20 for r in rows for v in r)            # old "text" erased
    gt.write_text(json.dumps({"schema": build.GFX_SCHEMA, "entries": [dict(entry, style="nope")]}), encoding="utf-8")
    with pytest.raises(build.TranslationError):
        build.build(d, table, tmp_path / "out2", NANUM, gfx_table=gt)


def test_template_canvas_parses_signed_offsets_and_direct_specs():
    img = bytearray(0x100000)
    struct.pack_into(">II", img, 0x80000, (1 << 28) | (0x3F0 << 16) | (2 << 12) | 0x3F0, (6 << 24) | 0x1234)
    struct.pack_into(">II", img, 0x80008, (0 << 28) | (0x010 << 16) | (0 << 12) | 0x020, (6 << 24) | 0x2000)
    canvas, cols = build.template_canvas(bytes(img), "080000+080008")
    assert cols == {6}
    assert canvas.parts == ((0x1234, 3, 2, 0, 0), (0x2000, 1, 1, 0x30, 0x20))
    canvas, cols = build.template_canvas(bytes(img), "S:00702:1x1:00")
    assert canvas.parts == ((0x702, 1, 1, 0, 0),) and cols == {0}
    for bad in ("S:00702:0x1:00", "S:zz:1x1:00", "07FFFF", "080001"):
        with pytest.raises((build.TranslationError, ValueError)):
            build.template_canvas(bytes(img), bad)


def test_gfx_table_rejects_bad_entries(tmp_path):
    base = {"id": "G1", "template": "080000", "style": "bubble", "source": "x", "ko": "가", "state": "needs_review", "note": ""}
    for bad in (dict(base, ko=3), dict(base, template=5), dict(base, state="done"), dict(base, style="zz"),
                dict(base, extra=1), dict(base, ko="", state="needs_review")):
        p = tmp_path / "g.json"
        p.write_text(json.dumps({"schema": build.GFX_SCHEMA, "entries": [bad]}), encoding="utf-8")
        with pytest.raises(build.TranslationError):
            build.read_gfx_table(p)
