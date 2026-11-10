"""DXT5 (BC3) block encoder tot hon cho icon nho: thu moi cap mau trong khoi (+ cuc tri PCA), chon cap it sai
nhat; alpha: thu min/max va cap 8 muc. Dung thay labelicons._bc3 (cung giao dien)."""
import itertools


def _pack(c):
    return (min(255, max(0, c[0])) >> 3) << 11 | (min(255, max(0, c[1])) >> 2) << 5 | min(255, max(0, c[2])) >> 3


def _unpack(v):
    return ((v >> 11 & 31) * 255 // 31, (v >> 5 & 63) * 255 // 63, (v & 31) * 255 // 31)


def _color(px):
    w = [p[3] / 255 for p in px]
    cols = list({tuple(p[:3]) for p, a in zip(px, w) if a > 0}) or [tuple(px[0][:3])]
    best = None
    cands = set()
    for a, b in itertools.combinations_with_replacement(cols, 2):
        cands.add((_pack(a), _pack(b)))
    for c0, c1 in cands:
        if c0 < c1:
            c0, c1 = c1, c0
        e0, e1 = _unpack(c0), _unpack(c1)
        if c0 == c1:
            pal = [e0] * 4
        else:
            pal = [e0, e1, tuple((2 * x + y) // 3 for x, y in zip(e0, e1)), tuple((x + 2 * y) // 3 for x, y in zip(e0, e1))]
        idx, err = [], 0
        for p, a in zip(px, w):
            k = min(range(4), key=lambda k: sum((pal[k][c] - p[c]) ** 2 for c in range(3)))
            idx.append(k); err += a * sum((pal[k][c] - p[c]) ** 2 for c in range(3))
        if best is None or err < best[0]:
            best = (err, c0, c1, idx)
    _, c0, c1, idx = best
    bits = sum(k << (2 * i) for i, k in enumerate(idx)) if c0 != c1 else 0
    return c0.to_bytes(2, "little") + c1.to_bytes(2, "little") + bits.to_bytes(4, "little")


def _alpha(px):
    al = [p[3] for p in px]
    a0, a1 = max(al), min(al)
    if a0 == a1:
        return bytes([a0, a1]) + bytes(6)
    pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    bits = 0
    for i, a in enumerate(al):
        bits |= min(range(8), key=lambda k: abs(pal[k] - a)) << (3 * i)
    return bytes([a0, a1]) + bits.to_bytes(6, "little")


def bc3(px):
    return _alpha(px) + _color(px)
