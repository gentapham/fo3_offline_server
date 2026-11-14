"""Ghep do hoa cua ban FIFA Online 3 Trung Quoc (Tencent, 01/2021) vao data_2..6.big cua FO3 Offline (2026-11-13).

    python -I cn_assets.py <thu muc data CN> <thu muc data cua minh> <thu muc ra>

  data_2: bong, giay, gang TM, so ao, font so ao  -> lay ban CN (moi + cap nhat)
  data_3: da mat, data_4: dau / toc / mat, data_6: toc LOD -> them file CN moi; file CN cap nhat thay file cu tru file
          cua cau thu tu tao (ma >= 700000: cau thu FO4 / the theo spid) va file chi minh co
  data_5: ao dau -> lay ban CN (moi + cap nhat), giu ao chi minh co
  data_6: CHI toc LOD (hairlod); bo hoat anh moi cua CN (finesse, bouncing...) - dong co tran dau co the khong khop
Ghi file .big moi (bang muc luc + du lieu can 0x800, doc thang tu file nguon, khong nap ca file vao bo nho).
"""
import os, re, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bigdir

ALIGN = 0x800
TAKE_CHANGED = {2: True, 3: True, 4: True, 5: True, 6: True}
ONLY = {6: ("data/sceneassets/hairlod/",)}  # chi lay nhung thu muc nay tu CN


def custom(name):
    ids = [int(x) for x in re.findall(r"_(\d+)", name)]
    return bool(ids) and ids[0] >= 700000


def plan(i, cn_big, our_big):
    cn = {n: (o, s, u) for n, o, s, u in bigdir.entries(cn_big)}
    ours = bigdir.entries(our_big)
    have = {n for n, *_ in ours}
    only = ONLY.get(i)
    ok = lambda n: only is None or n.startswith(only)
    out, stat = [], {"kept": 0, "updated": 0, "added": 0}
    for n, o, s, u in ours:
        c = cn.get(n)
        if c and ok(n) and TAKE_CHANGED[i] and (c[1], c[2]) != (s, u) and not custom(n):
            out.append((n, cn_big, *c)); stat["updated"] += 1
        else:
            out.append((n, our_big, o, s, u)); stat["kept"] += 1
    for n, (o, s, u) in cn.items():
        if n not in have and ok(n):
            out.append((n, cn_big, o, s, u)); stat["added"] += 1
    return out, stat


def write(dst, items, tail=b""):
    head = 0x11 + sum(0x11 + len(n.encode("latin1")) for n, *_ in items) + len(tail)
    pos, at = -(-head // ALIGN) * ALIGN, []
    for _, _, _, s, _ in items:
        at.append(pos)
        pos = -(-(pos + s) // ALIGN) * ALIGN
    total = pos
    t = bytearray(b"BIg4" + struct.pack("<I", 0) + struct.pack(">II", bigdir.enc(len(items)), bigdir.enc(head)) + b"\0")
    for (n, _, _, s, u), off in zip(items, at):
        t += struct.pack(">4I", bigdir.enc(off), bigdir.enc(s), bigdir.enc(u), bigdir.enc(len(n.encode("latin1"))))
        t += bigdir._name(n, s) + b"\0"
    t += tail
    end_last = at[-1] + items[-1][3]
    struct.pack_into("<I", t, 4, end_last)
    srcs = {}
    with open(dst + ".part", "wb") as f:
        f.write(t)
        for (n, src, o, s, u), off in zip(items, at):
            f.write(bytes(off - f.tell()))
            h = srcs.get(src) or srcs.setdefault(src, open(src, "rb"))
            h.seek(o)
            left = s
            while left:
                b = h.read(min(left, 1 << 22)); f.write(b); left -= len(b)
    for h in srcs.values():
        h.close()
    os.replace(dst + ".part", dst)
    return end_last


def main(cn_dir, our_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for i in (2, 3, 4, 5, 6):
        cn_big, our_big = os.path.join(cn_dir, f"data_{i}.big"), os.path.join(our_dir, f"data_{i}.big")
        items, stat = plan(i, cn_big, our_big)
        dst = os.path.join(out_dir, f"data_{i}.big")
        size = write(dst, items, bigdir.tail(our_big))
        # kiem tra: doc lai vai muc, so voi nguon
        got = {n: (o, s, u) for n, o, s, u in bigdir.entries(dst)}
        assert len(got) == len(items)
        for n, src, o, s, u in items[:: max(1, len(items) // 50)] + items[-3:]:
            assert bigdir.raw(dst, n) == bigdir.raw(src, n), n
        print(f"data_{i}.big: {stat} -> {len(items)} entries, {size / 1e6:.0f} MB", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:4])
