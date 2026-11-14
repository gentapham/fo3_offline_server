"""Ve icon giay cho Locker Room (externalAssets/shoe/shoe_<ma>.png, 300x130 nhu icon goc) tu mo hinh 3D cua game
(data_2.big: shoe_<ma>.rx3 = mo hinh, shoe_<ma>_0_textures.rx3 = mau thiet ke 0) - 2026-11-14.

    python -I shoe_icons.py <data_2.big> <thu muc ra> [ma ...]     (khong ghi ma: moi giay chua co icon)

Mo hinh: khai bao dinh "p0:00 3f32, n0:0C 3s10n, t0:14 2f16" (bo dem dinh 0x587AA1, chi so 0x5878F4 u16, tam giac).
Ve truc giao nhin ngang (mui giay ben trai, de ben duoi), to bong Lambert + anh sang moi truong, khu rang cua 2x.
"""
import io, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pylib"))
import numpy as np
import bigdir
from photo_head import unchunk, TEX
from PIL import Image

VB, IB = 0x587AA1, 0x5878F4
W, H, SS = 300, 130, 2


def sections(rx):
    n = struct.unpack("<I", rx[12:16])[0]
    return [struct.unpack("<4I", rx[16 + 16 * i:32 + 16 * i]) for i in range(n)]


def mesh(rx):
    secs = sections(rx)
    vbs = [(o, sz) for h, o, sz, _ in secs if h == VB]
    ibs = [(o, sz) for h, o, sz, _ in secs if h == IB]
    decl = rx[rx.index(b"p0:"):]
    decl = decl[:decl.index(b"\0")].decode()
    fmt = {f.split(":")[0]: (int(f.split(":")[1], 16), f.split(":")[4]) for f in decl.split()}
    out = []
    for (vo, _), (io_, _) in zip(vbs, ibs):
        _, nv, stride, _ = struct.unpack_from("<4I", rx, vo)
        raw = np.frombuffer(rx, np.uint8, nv * stride, vo + 16).reshape(nv, stride)
        po = fmt["p0"][0]
        pos = raw[:, po:po + 12].copy().view("<f4").reshape(nv, 3).astype(np.float64)
        to = fmt["t0"][0]
        uv = raw[:, to:to + 4].copy().view("<f2").reshape(nv, 2).astype(np.float64)
        no = fmt["n0"][0]
        nw = raw[:, no:no + 4].copy().view("<u4").reshape(nv).astype(np.int64)
        nrm = np.stack([(((nw >> s) & 1023) ^ 512) - 512 for s in (0, 10, 20)], 1) / 511.0
        _, ni, isz, _ = struct.unpack_from("<4I", rx, io_)
        idx = np.frombuffer(rx, "<u2" if isz == 2 else "<u4", ni, io_ + 16).astype(np.int64).reshape(-1, 3)
        out.append((pos, uv, nrm, idx))
    return out


def texture(rx):
    for h, o, sz, _ in sections(rx):
        if h == TEX:
            _, _, _, w, hh, fmt, mips = struct.unpack("<IHHHHHH", rx[o:o + 16])
            size = struct.unpack("<4I", rx[o + 16:o + 32])[2]
            four = {1: b"DXT1", 2: b"DXT3", 3: b"DXT5"}[fmt]
            hdr = (b"DDS " + struct.pack("<7I", 124, 0x1007 | 0x80000, hh, w, size, 0, 1) + b"\0" * 44
                   + struct.pack("<2I", 32, 4) + four + b"\0" * 20 + struct.pack("<I", 0x1000) + b"\0" * 16)
            return np.asarray(Image.open(io.BytesIO(hdr + rx[o + 32:o + 32 + size])).convert("RGB"), np.float64) / 255
    return None


def render(parts, tex, view=(2, 1, 0), flip=(1, 1)):
    """parts tu mesh(); view: chi so truc (ngang, doc, sau); flip: dao chieu ngang / doc."""
    Wr, Hr = W * SS, H * SS
    allp = np.concatenate([p[0] for p in parts])
    a, b, c = view
    x, y = allp[:, a] * flip[0], allp[:, b] * flip[1]
    s = min((Wr * .92) / (x.max() - x.min()), (Hr * .86) / (y.max() - y.min()))
    cx, cy = (x.max() + x.min()) / 2, (y.max() + y.min()) / 2
    img = np.zeros((Hr, Wr, 3)); zb = np.full((Hr, Wr), -1e18); alpha = np.zeros((Hr, Wr))
    light = np.array([.35, .75, .55]); light /= np.linalg.norm(light)
    th, tw = tex.shape[:2]
    for pos, uv, nrm, idx in parts:
        X = (pos[:, a] * flip[0] - cx) * s + Wr / 2
        Y = Hr / 2 - (pos[:, b] * flip[1] - cy) * s
        Z = pos[:, c]
        N = nrm[:, [a, b, c]] * np.array([flip[0], flip[1], 1.0])
        for t in idx:
            xs, ys = X[t], Y[t]
            x0, x1 = int(max(0, np.floor(xs.min()))), int(min(Wr - 1, np.ceil(xs.max())))
            y0, y1 = int(max(0, np.floor(ys.min()))), int(min(Hr - 1, np.ceil(ys.max())))
            if x1 < x0 or y1 < y0:
                continue
            d = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
            if abs(d) < 1e-9:
                continue
            gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + .5, np.arange(y0, y1 + 1) + .5)
            w0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / d
            w1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / d
            w2 = 1 - w0 - w1
            m = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
            if not m.any():
                continue
            z = w0 * Z[t[0]] + w1 * Z[t[1]] + w2 * Z[t[2]]
            sub = zb[y0:y1 + 1, x0:x1 + 1]
            m &= z > sub
            if not m.any():
                continue
            u = (w0 * uv[t[0], 0] + w1 * uv[t[1], 0] + w2 * uv[t[2], 0]) % 1.0
            v = (w0 * uv[t[0], 1] + w1 * uv[t[1], 1] + w2 * uv[t[2], 1]) % 1.0
            col = tex[np.clip((v * th).astype(int), 0, th - 1), np.clip((u * tw).astype(int), 0, tw - 1)]
            n = w0[..., None] * N[t[0]] + w1[..., None] * N[t[1]] + w2[..., None] * N[t[2]]
            n /= np.linalg.norm(n, axis=-1, keepdims=True) + 1e-9
            sh = .45 + .65 * np.clip(np.abs(n @ light), 0, 1)
            spec = np.clip(n @ light, 0, 1) ** 24 * .35
            px = np.clip(col * sh[..., None] + spec[..., None], 0, 1)
            sub[m] = z[m]
            img[y0:y1 + 1, x0:x1 + 1][m] = px[m]
            alpha[y0:y1 + 1, x0:x1 + 1][m] = 1
    rgba = np.dstack([img, alpha]) * 255
    out = Image.fromarray(rgba.astype(np.uint8), "RGBA").resize((W, H), Image.LANCZOS)
    return out


def icon(big, code, view=(2, 1, 0), flip=(1, 1)):
    rx = unchunk(bigdir.read(big, f"data/sceneassets/shoe/shoe_{code}.rx3"))
    tx = unchunk(bigdir.read(big, f"data/sceneassets/shoe/shoe_{code}_0_textures.rx3"))
    return render(mesh(rx), texture(tx), view, flip)


def models(big):
    """Ma giay co ca mo hinh lan mau thiet ke 0 trong data_2.big."""
    import re
    names = {n for n, *_ in bigdir.entries(big)}
    return sorted(int(m.group(1)) for n in names for m in [re.fullmatch(r"data/sceneassets/shoe/shoe_(\d+)\.rx3", n)]
                  if m and f"data/sceneassets/shoe/shoe_{m.group(1)}_0_textures.rx3" in names)


if __name__ == "__main__":
    big, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    codes = [int(c) for c in sys.argv[3:]] or [c for c in models(big) if not os.path.isfile(os.path.join(out, f"shoe_{c}.png"))]
    bad = []
    for code in codes:
        try:
            icon(big, code).save(os.path.join(out, f"shoe_{code}.png"))
            print("shoe", code, flush=True)
        except Exception as e:
            bad.append((code, repr(e)[:80]))
    print("done", len(codes) - len(bad), "failed", bad)
