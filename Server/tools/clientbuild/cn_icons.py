"""Tach bieu tuong mua (khung "<ma>season" cua sprite 144 seasonIconBig) tu Label.big cua ban CN ra PNG 30x24
cho season.py (icons/<ma>.png). Usage: python -I cn_icons.py <Label.big CN> <thu muc ra> ma[=ma_moi] ...
vd: 70 72 18=80 (bieu tuong VTR cua CN o ma 18 -> icons/80.png)."""
import io, os, sys, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pylib"))
import bigdir
from labelicons import _tags, _Bits, _bitmaps, LABEL_GFX, LABEL_DDS
from PIL import Image


def icons(label_big):
    gfx = bigdir.read(label_big, LABEL_GFX); dds = bigdir.read(label_big, LABEL_DDS)
    body = zlib.decompress(gfx[8:])
    r = _Bits(body, 0); r.u(4 * r.u(5)); start = r.end() + 4
    tags = _tags(body, start, len(body))
    defs = {int.from_bytes(d[:2], "little"): (c, d) for c, d in tags if c in (2, 22, 32, 83, 1008, 39)}
    atlas = Image.open(io.BytesIO(bytes(dds))).convert("RGBA")
    spr = next(d for c, d in tags if c == 39 and b"17season" in d)  # sprite bieu tuong lon (id khac nhau giua cac ban)
    inner = _tags(spr, 4, len(spr))
    out, label = {}, None
    for c, d in inner:
        if c == 43:
            label = d.rstrip(b"\0").decode("latin1")
        elif c == 26 and label and label.endswith("season"):  # PlaceObject2: flags, depth, [char id]
            flags = d[0]
            if flags & 0x02:
                cid = int.from_bytes(d[3:5], "little")
                shape = defs.get(cid)
                if shape and shape[0] in (2, 22, 32, 83):
                    for bid in _bitmaps(shape[1]):
                        sub = defs.get(bid)
                        if sub and sub[0] == 1008:
                            x1, y1, x2, y2 = (int.from_bytes(sub[1][4 + 2 * i:6 + 2 * i], "little") for i in range(4))
                            out[int(label[:-6])] = atlas.crop((x1, y1, x2, y2))
            label = None
    return out


if __name__ == "__main__":
    ic = icons(sys.argv[1])
    os.makedirs(sys.argv[2], exist_ok=True)
    for a in sys.argv[3:]:
        src, _, dst = a.partition("=")
        im = ic[int(src)]
        im.save(os.path.join(sys.argv[2], f"{dst or src}.png"))
        print(src, "->", dst or src, im.size)
