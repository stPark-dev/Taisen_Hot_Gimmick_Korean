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


# Geometry (w, h, x, y) of the sprite templates that template-based GRAPHICS specs reference (layout facts only).
FIXTURE_TEMPLATES = {
    0x81068: (7, 7, 0, 0), 0x810A0: (12, 3, 112, 32), 0x810A8: (6, 2, 112, 80),
    **{a: (9, 4, 0, 0) for a in (0x84DA8, 0x84DE0, 0x84E18, 0x84E50, 0x84E88, 0x84EC0, 0x84F20, 0x84F88,
                                 0x84FC0, 0x84FF8, 0x85030, 0x85068, 0x850A0)},
    0x84CB8: (12, 14, -32, -112), 0x84CC0: (5, 5, -112, 32), 0x84CC8: (9, 14, 16, -112), 0x84CD0: (8, 5, -112, 32),
    0x84D00: (12, 14, -160, -112), 0x84D08: (6, 5, 32, 32), 0x84D10: (12, 14, -160, -112), 0x84D18: (6, 5, 32, 32),
    0x81778: (11, 10, 0, 0), 0x81780: (12, 8, -160, -112), 0x81788: (6, 6, -64, 16), 0x817A0: (9, 12, 0, 0),
    0x817A8: (10, 12, 0, 0), 0x817C0: (7, 5, 0, 0),
}


def _put_templates(data, base_tnum=0x18000):
    """Write synthetic template entries + tiles for every template-based GRAPHICS spec."""
    tnum = base_tnum
    for spec in (s for s in build.GRAPHICS if "template" in s):
        col = 0x01 if spec["type"] == "panel" else 0x08
        fill = spec.get("fill", 31) if spec["type"] == "panel" else (spec["bg"] if spec["type"] == "credit" else 1)
        for a in spec["template"].split("+"):
            w, h, x, y = FIXTURE_TEMPLATES[int(a, 16)]
            d0 = ((h - 1) << 28) | ((y & 0x3FF) << 16) | ((w - 1) << 12) | (x & 0x3FF)
            _cpu_put(data["1-u22.bin"], data["2-u23.bin"], int(a, 16), struct.pack(">II", d0, (col << 24) | tnum))
            for i in range(w * h * 256):
                f, o = layout.gfx_offset_to_file(tnum * 256 + i)
                data[f][o] = fill
            tnum += w * h
        for i in range(256):      # index 17 = white ... 31 = dark, like the system palettes
            v = max(0, min(255, 255 - (i - 17) * 17))
            _cpu_put(data["1-u22.bin"], data["2-u23.bin"], spec["palette_rom"] + i * 4, bytes((v, v, v, 0)))


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
    _put_templates(data)
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


def _write_tiles(d, prof, tiles_rows, sheet, extra=None):
    data = {n: bytearray((d / n).read_bytes()) for n in prof}
    for tn, tile in sheet.encode(tiles_rows).items():
        for i, v in enumerate(tile):
            f, o = layout.gfx_offset_to_file(tn * 256 + i)
            data[f][o] = v
    if extra:
        extra(data)
    for n, b in data.items():
        (d / n).write_bytes(bytes(b))
        prof[n] = (len(b), hashlib.sha1(b).hexdigest())


def _pal_put(data, addr, colors):
    for i, rgb in enumerate(colors):
        _cpu_put(data["1-u22.bin"], data["2-u23.bin"], addr + i * 4, bytes(rgb) + b"\x00")


def test_pow_label_keeps_red_numbers(tmp_path, monkeypatch):
    import zipfile
    from hotgmck import graphics
    d, prof = make_set(tmp_path)
    sheet = graphics.SpriteSheet((0x9000,), 3, 1)
    rows = [[0] * 48 for _ in range(16)]
    for y in range(2, 14):
        for x in range(2, 30):
            rows[y][x] = 3
    for y in range(8, 15):
        for x in range(30, 46):
            rows[y][x] = 5
    colors = [(0, 0, 0), (255, 255, 255), (16, 16, 48), (180, 170, 240), (220, 220, 250), (230, 20, 20)] + [(90, 90, 90)] * 250
    _write_tiles(d, prof, rows, sheet, lambda data: _pal_put(data, 0x60000, colors))
    monkeypatch.setattr(source, "PROFILE", prof)
    spec = {"id": "pow", "type": "pow_label", "sheets": ((0x9000, 3, 1),), "palette_rom": 0x60000,
            "indices": (1, 2, 3, 4, 5), "lines": ["작파워"]}
    monkeypatch.setattr(build, "GRAPHICS", [spec])
    table = tmp_path / "t.json"
    build.extract(d, table)
    build.build(d, table, tmp_path / "out", NANUM, assets_dir=tmp_path)
    z = zipfile.ZipFile(tmp_path / "out" / "hotgmck.zip")
    files = {n: z.read(n) for n in z.namelist()}
    out = sheet.read(lambda o, n: build.region_read(files, o, n))
    assert all(out[y][x] == 5 for y in range(8, 15) for x in range(30, 46))
    assert any(v not in (0, 5) for r in out for v in r)
    assert sum(v == 3 for r in out for v in r) < 28 * 12


def test_photo_label_red_detection_repaints_red_glyph(tmp_path, monkeypatch):
    import zipfile
    from hotgmck import graphics
    d, prof = make_set(tmp_path)
    sheet = graphics.SpriteSheet((0x9000,), 2, 2)
    rows = [[1] * 32 for _ in range(32)]
    for y in range(4, 28):
        for x in range(8, 20):
            rows[y][x] = 2
    rows[0] = [3] * 32
    colors = [(0, 0, 0), (225, 220, 200), (150, 10, 30), (40, 40, 40)] + [(i, i, i) for i in range(4, 256)]
    _write_tiles(d, prof, rows, sheet, lambda data: _pal_put(data, 0x60000, colors))
    monkeypatch.setattr(source, "PROFILE", prof)
    spec = {"id": "fuyo", "type": "photo_label", "sheet": (0x9000, 2, 2), "palette_rom": 0x60000, "band": (2, 2, 29, 29),
            "lines": ["버", "림"], "detect": "red", "fill": (150, 10, 30), "outline": None, "outline_px": 0}
    monkeypatch.setattr(build, "GRAPHICS", [spec])
    table = tmp_path / "t.json"
    build.extract(d, table)
    build.build(d, table, tmp_path / "out", NANUM, assets_dir=tmp_path)
    z = zipfile.ZipFile(tmp_path / "out" / "hotgmck.zip")
    files = {n: z.read(n) for n in z.namelist()}
    out = sheet.read(lambda o, n: build.region_read(files, o, n))
    assert out[0] == [3] * 32
    assert sum(out[y][x] == 2 for y in range(4, 28) for x in range(8, 20)) < 24 * 12
    assert any(v == 2 for r in out for v in r)


def test_panel_writer_draws_inside_parts_and_rejects_text_outside(tmp_path, monkeypatch):
    d, prof = make_set(tmp_path)
    prof = _fill_title(d, prof)
    monkeypatch.setattr(source, "PROFILE", prof)
    files = {n: (d / n).read_bytes() for n in prof}
    image = layout.cpu_image(files["1-u22.bin"], files["2-u23.bin"])
    spec = next(s for s in build.GRAPHICS if s["type"] == "panel")
    from hotgmck.writeplan import WritePlan
    plan = WritePlan(files)
    rep = build.panel_writes(plan, files, image, spec)
    assert rep[spec["id"]]["tiles_written"] > 0
    out = plan.apply()
    canvas, _ = build.template_canvas(image, spec["template"])
    rows = canvas.read(lambda o, n: build.region_read(out, o, n))
    assert any(v not in (None, spec["fill"]) for r in rows for v in r)        # text drawn
    bad = dict(spec, items=(("가나다라마바사아자차카타파하", 0, 0, 30),))       # runs past the 7x7 part into uncovered cells
    with pytest.raises(RuntimeError, match="outside"):
        build.panel_writes(WritePlan(files), files, image, bad)


def test_build_emits_bps_patches_that_reproduce_output(env):
    d, table, out = env
    _edit(table, "T0A77F0", ko="가나\n!", state="in_progress")
    build.build(d, table, out, NANUM)
    import zipfile
    built = {n: zipfile.ZipFile(out / "hotgmck.zip").read(n) for n in source.PROFILE}
    patch_dir = out / "patch"
    pm = json.loads((patch_dir / "patch.json").read_text(encoding="utf-8"))
    changed = {n for n in source.PROFILE if built[n] != (d / n).read_bytes()}
    assert set(pm["files"]) == changed and changed
    dest = out / "applied"
    build.apply_patch(d, patch_dir, dest)
    rebuilt = {n: zipfile.ZipFile(dest / "hotgmck.zip").read(n) for n in source.PROFILE}
    assert rebuilt == built


def test_apply_patch_rejects_wrong_source(env, tmp_path):
    d, table, out = env
    _edit(table, "T0A77F0", ko="가", state="in_progress")
    build.build(d, table, out, NANUM)
    bad = tmp_path / "bad"
    bad.mkdir()
    for n in source.PROFILE:
        data = bytearray((d / n).read_bytes())
        if n == "1-u22.bin":
            data[0] ^= 1
        (bad / n).write_bytes(bytes(data))
    with pytest.raises(source.SourceError):
        build.apply_patch(bad, out / "patch", tmp_path / "dest")


def _all_eligible(table):
    doc = json.loads(table.read_text(encoding="utf-8"))
    doc["entries"] = [r for r in doc["entries"]]
    for r in doc["entries"]:
        if r["source_codes"]:
            r.update(ko=r["ko"] or "가", state="distribution_eligible")
    table.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_release_requires_exclusion_record(env, tmp_path):
    d, table, out = env
    _all_eligible(table)
    with pytest.raises(build.TranslationError, match="exclusion"):
        build.build(d, table, out, NANUM, policy="release")
    ex = tmp_path / "ex.json"
    ex.write_text(json.dumps({"schema": build.EXCLUSION_SCHEMA, "exclusions": [
        {"id": "X1", "content": "c", "reason": "r", "approved_by": "user", "date": "2026-10-01"}]}), encoding="utf-8")
    m = build.build(d, table, out, NANUM, policy="release", exclusions=ex)
    assert m["distribution"] is True and m["exclusions"] == ["X1"]


def test_release_rejects_incomplete_exclusion(env, tmp_path):
    d, table, out = env
    _all_eligible(table)
    ex = tmp_path / "ex.json"
    ex.write_text(json.dumps({"schema": build.EXCLUSION_SCHEMA, "exclusions": [
        {"id": "X1", "content": "c", "reason": "", "approved_by": "user", "date": "2026-10-01"}]}), encoding="utf-8")
    with pytest.raises(build.TranslationError, match="exclusion"):
        build.build(d, table, out, NANUM, policy="release", exclusions=ex)


def test_release_ignores_entries_with_empty_source(env, tmp_path):
    d, table, out = env
    doc = json.loads(table.read_text(encoding="utf-8"))
    assert any(not r["source_codes"] for r in doc["entries"])
    _all_eligible(table)
    ex = tmp_path / "ex.json"
    ex.write_text(json.dumps({"schema": build.EXCLUSION_SCHEMA, "exclusions": [
        {"id": "X1", "content": "c", "reason": "r", "approved_by": "user", "date": "2026-10-01"}]}), encoding="utf-8")
    build.build(d, table, out, NANUM, policy="release", exclusions=ex)


def test_credit_writer_redraws_page_on_black(tmp_path, monkeypatch):
    d, prof = make_set(tmp_path)
    prof = _fill_title(d, prof)
    monkeypatch.setattr(source, "PROFILE", prof)
    files = {n: (d / n).read_bytes() for n in prof}
    image = layout.cpu_image(files["1-u22.bin"], files["2-u23.bin"])
    data = {n: bytearray(b) for n, b in files.items()}
    tnum = 0x19000
    _cpu_put(data["1-u22.bin"], data["2-u23.bin"], 0x81778, struct.pack(">II", (3 << 28) | (3 << 12), (8 << 24) | tnum))
    for i in range(16 * 256):
        f, o = layout.gfx_offset_to_file(tnum * 256 + i)
        data[f][o] = 252 if i % 7 else 1
    for i in range(256):
        v = 0 if i == 252 else max(0, 251 - i)
        _cpu_put(data["1-u22.bin"], data["2-u23.bin"], 0x6BA10 + i * 4, bytes((v, v, v, 0)))
    files = {n: bytes(b) for n, b in data.items()}
    image = layout.cpu_image(files["1-u22.bin"], files["2-u23.bin"])
    spec = {"id": "c", "type": "credit", "template": "081778", "palette_rom": 0x6BA10, "bg": 252,
            "lines": (("가나", 2, 3), ("다라", 20, 30))}
    from hotgmck.writeplan import WritePlan
    plan = WritePlan(files)
    rep = build.credit_writes(plan, files, image, spec)
    out = plan.apply()
    from hotgmck import graphics
    rows = graphics.SpriteSheet((tnum,), 4, 4).read(lambda o, n: build.region_read(out, o, n))
    assert rep["c"]["tiles_written"] > 0
    assert rows[63][63] == 252 and any(v != 252 for v in rows[5])      # old ink gone, new text drawn
    with pytest.raises(RuntimeError, match="fit"):
        build.credit_writes(WritePlan(files), files, image, dict(spec, lines=(("가나다라마바사아자차카", 0, 0),)))
