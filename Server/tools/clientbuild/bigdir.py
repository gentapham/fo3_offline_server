"""List / extract entries of the client's "BIg4" archives (data/data_N.big ...).

Table format, from fifazf's in-place decoder at 0xB6E360 (2026-10-30):
    "BIg4" | u32 LE archive size | u32 count* | u32 header size* | u8 decoded flag (0 on disk)
    then per entry: offset* size* usize* namelen* (u32 BE each) | name (namelen bytes) | 1 byte
* = dec(x) = ~(nibswap(x) ^ 0x2938FA72), nibswap = swap the nibbles of every byte.
Name bytes: d = nibswap(c); out = d ^ k; k = (d or size & 0xFF) - 0x66, k starting at size & 0xFF.
usize 0 = stored; any other usize != size means RefPack-compressed data.

Usage: python -I bigdir.py <file.big> [substring]          list entries
       python -I bigdir.py <file.big> <name> <out_file>    extract (RefPack inflated)
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def nibswap(x):
    return ((x << 4) & 0xF0F0F0F0) | ((x >> 4) & 0x0F0F0F0F)


def dec(x):
    return ~(nibswap(x) ^ 0x2938FA72) & 0xFFFFFFFF


def enc(v):
    return nibswap(~v & 0xFFFFFFFF ^ 0x2938FA72)


def table(path):
    """Raw header + table bytes (the first `header size` bytes of the archive)."""
    with open(path, "rb") as f:
        head = f.read(16)
        assert head[:4] == b"BIg4", head[:4]
        f.seek(0)
        return f.read(dec(struct.unpack(">I", head[12:16])[0]))


def _walk(t):
    """(entry position, name, offset, size, usize) for every entry of a raw table."""
    p = 0x11
    for _ in range(dec(struct.unpack(">I", t[8:12])[0])):
        off, size, usize, nl = (dec(v) for v in struct.unpack(">4I", t[p:p + 16]))
        k, name = size & 0xFF, bytearray()
        for c in t[p + 16:p + 16 + nl]:
            d = ((c << 4) | (c >> 4)) & 0xFF
            name.append(d ^ k)
            k = ((d or size & 0xFF) - 0x66) & 0xFF
        yield p, name.decode("latin1"), off, size, usize
        p += 0x11 + nl


def entries(path):
    return [e[1:] for e in _walk(table(path))]


def patch(path, files, align=0x800, in_place=False, at=None):
    """Append {name: bytes (stored) | (refpacked bytes, unpacked size)} to the archive and
    re-point those entries; the table keeps its size because names do not change.
    in_place: write each one over its entry's own bytes instead (it must fit there), or at the
    offset `at` gives for its name (the caller keeps that space free), so the archive keeps its
    length (the game caches all of data_0.big: 40 MB more crashed its boot).
    Returns {name: (offset, size, usize)}."""
    t = bytearray(table(path))
    old = {name: (off, size) for _, name, off, size, _ in _walk(t)}
    placed = {}
    with open(path, "r+b") as f:
        end = f.seek(0, 2)
        for name, data in files.items():
            data, usize = data if isinstance(data, tuple) else (data, 0)
            if in_place and name in (at or {}):
                off = at[name]
                assert off + len(data) <= end
            elif in_place:
                off, room = old[name]
                assert len(data) <= room, f"{name}: {len(data)} bytes, its slot holds {room}"
            else:
                off = -(-end // align) * align
            f.seek(off)
            f.write(data)
            if not in_place:
                end = off + len(data)
            placed[name] = (off, len(data), usize)
        todo = dict(placed)
        for p, name, *_ in list(_walk(t)):
            if name not in todo:
                continue
            off, size, usize = todo.pop(name)
            struct.pack_into(">III", t, p, enc(off), enc(size), enc(usize))
            t[p + 16:p + 16 + len(name)] = _name(name, size)
        assert not todo, f"not in {path}: {list(todo)}"
        if not in_place:
            struct.pack_into("<I", t, 4, end)  # archive size
        f.seek(0)
        f.write(t)
    return placed


def _name(name, size):
    """A table entry's name bytes (keyed by the size's low byte)."""
    out, k = bytearray(), size & 0xFF
    for ch in name.encode("latin1"):
        d = ch ^ k
        out.append(((d << 4) | (d >> 4)) & 0xFF)
        k = ((d or size & 0xFF) - 0x66) & 0xFF
    return bytes(out)


def raw(path, name):
    """(an entry's bytes as stored, its usize)."""
    for n, off, size, usize in entries(path):
        if n == name:
            with open(path, "rb") as f:
                f.seek(off)
                return f.read(size), usize
    raise KeyError(name)


def read(path, name):
    data, usize = raw(path, name)
    if usize and usize != len(data):
        from refpack import unrefpack
        data = unrefpack(data)
    return data


def tail(path):
    """The table's bytes after its last entry (zeros and the packer's tag, e.g. "L286")."""
    t = table(path)
    p, name, *_ = list(_walk(t))[-1]
    return t[p + 0x11 + len(name.encode("latin1")):]


def build(files, tail=b"", align=0x40):
    """A whole archive of [(name, bytes | (bytes as stored, usize))], data `align`-aligned."""
    files = [(n, d if isinstance(d, tuple) else (d, 0)) for n, d in files]
    head = 0x11 + sum(0x11 + len(n.encode("latin1")) for n, _ in files) + len(tail)
    pos, at = -(-head // align) * align, []
    for _, (data, _) in files:
        at.append(pos)
        pos = -(-(pos + len(data)) // align) * align
    out = bytearray(b"BIg4" + struct.pack("<I", 0) + struct.pack(">II", enc(len(files)), enc(head)) + b"\0")
    for (n, (data, usize)), off in zip(files, at):
        out += struct.pack(">4I", enc(off), enc(len(data)), enc(usize), enc(len(n.encode("latin1"))))
        out += _name(n, len(data)) + b"\0"
    out += tail
    for (_, (data, _)), off in zip(files, at):
        out += bytes(off - len(out)) + data
    struct.pack_into("<I", out, 4, len(out))
    return bytes(out)


if __name__ == "__main__":
    if len(sys.argv) == 4:
        data = read(sys.argv[1], sys.argv[2])
        open(sys.argv[3], "wb").write(data)
        print(len(data), "bytes ->", sys.argv[3])
    else:
        sub = sys.argv[2].lower() if len(sys.argv) > 2 else ""
        for n, off, size, usize in entries(sys.argv[1]):
            if sub in n.lower():
                print(f"{off:#010x} {size:>10} {usize:>10} {n}")
