"""Season icon frames in Label.big (reviewed copy of the reference fo4_season.py section)."""
import zlib
# --- season icons: data/ui/components/Label.big ------------------------------------------------
# Label.gfx (CFX = zlib SWF) holds the two SeasonIcon clips: sprite 144 "seasonIconBig" and sprite
# 735, one frame per season labelled "<code>season" (SeasonIcon does gotoAndStop(season +
# "season")). Each frame puts a shape at depth 1 whose bitmap fill is a DefineSubImage (GFx tag
# 1008: id, image 0, x1, y1, x2, y2) of the DXT5 atlas Label_I1.dds. A new season gets its FO4
# icon drawn into free atlas space, a copy of the '17 shapes pointing at it, and one more frame
# in both clips.
LABEL_BIG = "data/ui/components/Label.big"
LABEL_GFX, LABEL_DDS = "data/ui/components/Label.gfx", "data/ui/components/Label_I1.dds"
ICON_SPRITES = (144, 735)
ICON_FREE_Y = 640  # the atlas is empty from about y 580 down
DEFINES = {2, 6, 7, 10, 11, 14, 20, 21, 22, 32, 33, 34, 35, 36, 37, 39, 46, 48, 60, 75, 83, 84, 1008}


def _tags(b, p, end):
    out = []
    while p < end:
        h = int.from_bytes(b[p:p + 2], "little"); p += 2
        code, ln = h >> 6, h & 0x3F
        if ln == 0x3F:
            ln = int.from_bytes(b[p:p + 4], "little"); p += 4
        out.append([code, bytes(b[p:p + ln])])
        p += ln
        if code == 0:
            break
    return out


def _join(tags):
    out = bytearray()
    for code, data in tags:
        if len(data) < 0x3F and code not in (6, 20, 21, 35, 36):
            out += ((code << 6) | len(data)).to_bytes(2, "little")
        else:
            out += ((code << 6) | 0x3F).to_bytes(2, "little") + len(data).to_bytes(4, "little")
        out += data
    return bytes(out)


class _Bits:
    def __init__(self, b, p):
        self.b, self.bit = b, p * 8

    def u(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | (self.b[self.bit >> 3] >> (7 - (self.bit & 7)) & 1)
            self.bit += 1
        return v

    def end(self):
        return (self.bit + 7) // 8


def _fills(d):
    """Byte offsets of the bitmap ids of a DefineShape payload's bitmap fill styles."""
    r = _Bits(d, 2); r.u(4 * r.u(5)); p = r.end()  # id, bounds
    out, n, p = [], d[p], p + 1
    for _ in range(n):
        assert 0x40 <= d[p] <= 0x43, f"fill style {d[p]:#x}"
        out.append(p + 1)
        m = _Bits(d, p + 3)
        for _ in range(2):  # scale, rotate
            if m.u(1):
                m.u(2 * m.u(5))
        m.u(2 * m.u(5))  # translate
        p = m.end()
    return out


def _bitmaps(shape):
    return [int.from_bytes(shape[p:p + 2], "little") for p in _fills(shape)]


def _shape_copy(data, new_id, bitmap):
    """A DefineShape payload with a new id and every real bitmap fill (not 0xFFFF) on `bitmap`."""
    d = bytearray(data)
    d[0:2] = new_id.to_bytes(2, "little")
    for p in _fills(d):
        if d[p:p + 2] != b"\xff\xff":
            d[p:p + 2] = bitmap.to_bytes(2, "little")
    return bytes(d)


def _bc3(px):
    """One DXT5 block from 16 RGBA pixels (row-major): min/max endpoints, nearest indices."""
    al = [p[3] for p in px]
    a0, a1 = max(al), min(al)
    out = bytearray([a0, a1])
    bits = 0
    if a0 > a1:
        pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
        for i, a in enumerate(al):
            bits |= min(range(8), key=lambda k: abs(pal[k] - a)) << (3 * i)
    out += bits.to_bytes(6, "little")
    seen = [p for p in px if p[3]] or px
    # endpoints: the two most different pixels (min/max per channel makes colours none of them has)
    hi, lo = max(((p, q) for p in seen for q in seen), key=lambda pq: sum((pq[0][c] - pq[1][c]) ** 2 for c in range(3)))
    pack = lambda c: (c[0] >> 3) << 11 | (c[1] >> 2) << 5 | c[2] >> 3
    c0, c1 = pack(hi), pack(lo)
    unpack = lambda v: ((v >> 11 & 31) * 255 // 31, (v >> 5 & 63) * 255 // 63, (v & 31) * 255 // 31)
    bits = 0
    if c0 != c1:
        if c0 < c1:
            c0, c1 = c1, c0
        e0, e1 = unpack(c0), unpack(c1)
        pal = [e0, e1, tuple((2 * x + y) // 3 for x, y in zip(e0, e1)), tuple((x + 2 * y) // 3 for x, y in zip(e0, e1))]
        for i, p in enumerate(px):
            bits |= min(range(4), key=lambda k: sum((pal[k][c] - p[c]) ** 2 for c in range(3))) << (2 * i)
    return bytes(out + c0.to_bytes(2, "little") + c1.to_bytes(2, "little") + bits.to_bytes(4, "little"))


def _draw(dds, img, x, y):
    """Encode an RGBA PIL image into the DXT5 atlas at (x, y) (multiples of 4, free space)."""
    w = int.from_bytes(dds[16:20], "little")
    for by in range(0, img.height, 4):
        for bx in range(0, img.width, 4):
            px = [img.getpixel((bx + i, by + j)) if bx + i < img.width and by + j < img.height else (0, 0, 0, 0)
                  for j in range(4) for i in range(4)]
            at = 128 + (((y + by) // 4) * (w // 4) + (x + bx) // 4) * 16
            dds[at:at + 16] = __import__("bc3best").bc3(px)  # bo ma hoa tot hon (it nhoe hon _bc3)


def _lossless(bid, img):
    """DefineBitsLossless2 (tag 36, format 5 = ARGB nhan truoc alpha): anh khong nen, khong qua DXT5."""
    w, h = img.size
    raw = bytearray()
    for r, g, b_, a in img.getdata():
        raw += bytes((a, r * a // 255, g * a // 255, b_ * a // 255))
    return [36, bid.to_bytes(2, "little") + bytes([5]) + w.to_bytes(2, "little") + h.to_bytes(2, "little")
            + zlib.compress(bytes(raw), 9)]


def label_with_icons(gfx, dds, icons, lossless=()):
    """(gfx, dds) of Label.big with one more SeasonIcon frame per {season: PIL RGBA icon}."""
    import zlib
    from PIL import Image
    assert gfx[:3] == b"CFX", gfx[:3]
    body = zlib.decompress(gfx[8:])
    r = _Bits(body, 0); r.u(4 * r.u(5)); start = r.end() + 4
    tags = _tags(body, start, len(body))
    atlas = Image.open(__import__("io").BytesIO(bytes(dds))).convert("RGBA")
    dds = bytearray(dds)
    defs = {}
    for code, data in tags:
        if code in DEFINES:
            defs[int.from_bytes(data[:2], "little")] = (code, data)
    next_id = max(defs) + 1
    sprites = {sid: next(t for t in tags if t[0] == 39 and int.from_bytes(t[1][:2], "little") == sid) for sid in ICON_SPRITES}
    new_defs, x, row = [], 0, 0
    for season, icon in sorted(icons.items()):
        if x + 40 > atlas.width:  # het hang (25 icon) -> hang moi ben duoi (moi hang: 1 dai icon lon + 1 dai icon nho)
            x, row = 0, row + 1
        label = f"{season}season".encode() + b"\0"
        for sid in ICON_SPRITES:
            inner = _tags(sprites[sid][1], 4, len(sprites[sid][1]))
            # ma da co khung icon (icon goc cua client cho mua khong co the - mua tu tao GM Tool): doi ten khung cu
            # (gotoAndStop(<ma>season) se toi khung moi them o cuoi)
            inner = [[c, b"x" + d] if c == 43 and d == label else [c, d] for c, d in inner]
            # the '17 frame's shape and its sub-image size
            i = next(k for k, (c, d) in enumerate(inner) if c == 43 and d.startswith(b"17season"))
            place = next(d for c, d in inner[i:] if c == 26)
            shape_id = int.from_bytes(place[3:5], "little")
            sc, shape = defs[shape_id]
            assert sc == 2, sc
            ref = next(defs[b][1] for b in _bitmaps(shape) if b in defs and defs[b][0] == 1008)
            x1, y1, x2, y2 = (int.from_bytes(ref[k:k + 2], "little") for k in (4, 6, 8, 10))
            w, h = x2 - x1, y2 - y1
            fit = icon.copy()
            fit.thumbnail((w, h), Image.LANCZOS)
            cell = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            cell.paste(fit, ((w - fit.width) // 2, (h - fit.height) // 2))
            sub, shp = next_id, next_id + 1
            next_id += 2
            if season in lossless:  # anh rieng khong nen (giu dung pixel, khong nhoe DXT5)
                new_defs += [_lossless(sub, cell), [2, _shape_copy(shape, shp, sub)]]
            else:
                y = ICON_FREE_Y + 96 * row + (0 if sid == ICON_SPRITES[0] else 48)
                assert y + h <= atlas.height, "atlas full"
                assert atlas.crop((x, y, x + w, y + h)).getextrema()[3] == (0, 0), "atlas space not free"
                _draw(dds, cell, x, y)
                new_defs += [[1008, sub.to_bytes(2, "little") + bytes(2) + b"".join(v.to_bytes(2, "little") for v in (x, y, x + w, y + h))],
                             [2, _shape_copy(shape, shp, sub)]]
            frame = [[43, label], [26, bytes([0x03, 1, 0]) + shp.to_bytes(2, "little")], [1, b""]]
            inner = inner[:-1] + frame + inner[-1:]
            nf = int.from_bytes(sprites[sid][1][2:4], "little") + 1
            sprites[sid][1] = sid.to_bytes(2, "little") + nf.to_bytes(2, "little") + _join(inner)
        x += 40
    at = tags.index(sprites[ICON_SPRITES[0]])
    tags[at:at] = new_defs
    body = body[:start] + _join(tags)
    return b"CFX" + gfx[3:4] + (len(body) + 8).to_bytes(4, "little") + zlib.compress(body, 9), bytes(dds)


